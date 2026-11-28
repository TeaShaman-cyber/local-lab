"""Read signed IntelMSR and IntelMCHBAR modules; no register writes or EC accesses."""

import ctypes as C
import hashlib
import struct
import time
from contextlib import ExitStack
from pathlib import Path

from cpu_limits import decode_mmio, make_sample

MODULE = Path("E:/tpfan-qa/msr-probe-289/IntelMSR-0.2.12.bin")
SHA = "30a5a302283de1e080dfee99e686eca91d7e53e911095cb91a320fb961f1ce12"
PACKAGE = (0x606, 0x610, 0x611, 0x1B1)
CORE = (0x64F, 0x19C, 0x198, 0xE7, 0xE8)


class Reader:
    module = MODULE
    sha = SHA

    def __enter__(self):
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            identity = winreg.QueryValueEx(key, "Identifier")[0]
            vendor = winreg.QueryValueEx(key, "VendorIdentifier")[0]
        if vendor != "GenuineIntel" or not identity.startswith("Intel64 Family 6 Model 142 Stepping "):
            raise ValueError("unsupported CPU model")
        blob = self.module.read_bytes()
        if len(blob) > 65536 or hashlib.sha256(blob).hexdigest() != self.sha:
            raise ValueError("unexpected signed module")
        k = self.k = C.WinDLL("kernel32", use_last_error=True)
        ptr, dw, size = C.c_void_p, C.c_uint32, C.c_size_t
        k.GetCurrentProcess.restype = ptr
        k.GetCurrentThread.restype = ptr
        k.GetActiveProcessorGroupCount.restype = C.c_uint16
        k.GetProcessAffinityMask.argtypes = [ptr, C.POINTER(size), C.POINTER(size)]
        k.SetThreadAffinityMask.argtypes = [ptr, size]
        k.SetThreadAffinityMask.restype = size
        k.GetTickCount64.restype = C.c_uint64
        k.CreateFileW.argtypes = [C.c_wchar_p, dw, dw, ptr, dw, dw, ptr]
        k.CreateFileW.restype = ptr
        k.CloseHandle.argtypes = [ptr]
        k.DeviceIoControl.argtypes = [ptr, dw, ptr, dw, ptr, dw, C.POINTER(dw), ptr]
        process, system = size(), size()
        if (
            k.GetActiveProcessorGroupCount() != 1
            or not k.GetProcessAffinityMask(k.GetCurrentProcess(), C.byref(process), C.byref(system))
            or process.value != 255
            or system.value != 255
        ):
            raise ValueError("unexpected CPU affinity topology")
        self.h = k.CreateFileW(r"\\.\GLOBALROOT\Device\PawnIO", 0xC0000000, 3, None, 3, 0, None)
        if self.h in (None, ptr(-1).value):
            raise OSError(C.get_last_error(), "open signed module driver")
        try:
            self.ioctl(0x821, blob, 0)
        except Exception:
            k.CloseHandle(self.h)
            raise
        return self

    def __exit__(self, *args):
        self.k.CloseHandle(self.h)

    def ioctl(self, code, payload, length):
        src = C.create_string_buffer(payload)
        dst = C.create_string_buffer(length) if length else None
        got = C.c_uint32()
        if not self.k.DeviceIoControl(
            self.h, (41394 << 16) | (code << 2), src, len(payload), dst, length, C.byref(got), None
        ):
            raise OSError(C.get_last_error(), "read-only PawnIO operation")
        if got.value != length:
            raise ValueError("unexpected MSR reply size")
        return dst.raw if dst else b""

    def read(self, address):
        if address not in PACKAGE + CORE:
            raise ValueError("MSR outside telemetry allowlist")
        return struct.unpack("<Q", self.ioctl(0x841, struct.pack("<32sQ", b"ioctl_read_msr", address), 8))[0]

    def sample(self):
        start_tick, start_ns = self.k.GetTickCount64(), time.time_ns()
        thread = self.k.GetCurrentThread()
        cores = []
        package = None
        for i in range(8):
            old = self.k.SetThreadAffinityMask(thread, 1 << i)
            if not old:
                raise OSError("affinity set failed")
            try:
                if i == 0:
                    package = [self.read(a) for a in PACKAGE]
                cores.append([i, *[self.read(a) for a in CORE]])
            finally:
                if not self.k.SetThreadAffinityMask(thread, old):
                    raise OSError("affinity restore failed")
        return make_sample(start_ns, time.time_ns(), start_tick, self.k.GetTickCount64(), package, cores)


class MmioReader(Reader):
    module = Path("E:/tpfan-qa/msr-probe-289/IntelMCHBAR-0.2.12.bin")
    sha = "3f82b832d99b4aac37d2a20fdb7c9baa2a3bc0488612c9019c9484eb0e8a6eae"

    def read_limits(self):
        tick, utc = self.k.GetTickCount64(), time.time_ns()
        raw = struct.unpack("<Q", self.ioctl(0x841, struct.pack("<32sQ", b"ioctl_read_qword", 0x59A0), 8))[0]
        unit = struct.unpack("<Q", self.ioctl(0x841, struct.pack("<32sQ", b"ioctl_read_dword", 0x5938), 8))[0]
        return dict(
            status="ok",
            start_ns=utc,
            end_ns=time.time_ns(),
            start_tick=tick,
            end_tick=self.k.GetTickCount64(),
            raw=raw,
            unit=unit,
        )


class PlatformReader:
    """MSR remains available even if the independent read-only MMIO module fails."""

    def __init__(self, msr_factory=Reader, mmio_factory=MmioReader):
        self.msr_factory = msr_factory
        self.mmio_factory = mmio_factory

    def __enter__(self):
        self.stack = ExitStack()
        try:
            self.msr = self.stack.enter_context(self.msr_factory())
            try:
                self.mmio = self.stack.enter_context(self.mmio_factory())
            except (OSError, ValueError, RuntimeError) as exc:
                print("CPU_MMIO_UNAVAILABLE", str(exc)[:200], flush=True)
                self.mmio = None
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def sample(self):
        s = self.msr.sample()
        s["mmio"] = {"status": "unavailable"}
        if self.mmio is not None:
            try:
                s["mmio"] = self.mmio.read_limits()
                decode_mmio(s)
            except (OSError, ValueError, RuntimeError) as exc:
                print("CPU_MMIO_UNAVAILABLE", str(exc)[:200], flush=True)
                s["mmio"] = {"status": "unavailable"}
                self.mmio = None
        return s
