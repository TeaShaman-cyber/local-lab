"""One-shot DYTC CQL control for ThinkPad L14 Gen 1 Intel (20U2).

This clears the active flag, not the automatic motion policy. No fan writes.
"""
from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes

GET = 2
CQL_OFF = 0x000F1001
CQL_BIT = 1 << 17
# Exact supported observed state, with only CQL allowed to differ.
PSC7_STATE = 0x20017D01


def clear_cql(call):
    before = call(GET)
    if before & ~CQL_BIT != PSC7_STATE:
        raise RuntimeError(f"unexpected DYTC state: {before:#010x}")
    if not before & CQL_BIT:
        return before
    # SET may have taken effect even if its transport reports failure.
    # Never retry a mutation blindly.
    call(CQL_OFF)
    after = call(GET)
    if after != PSC7_STATE:
        raise RuntimeError(f"CQL clear not verified: {after:#010x}")
    return after


def check_model():
    if sys.platform != "win32":
        raise RuntimeError("Windows required")
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
        product = winreg.QueryValueEx(key, "SystemProductName")[0]
    if not isinstance(product, str) or not product.startswith("20U2"):
        raise RuntimeError(f"unsupported ThinkPad product: {product!r}")


class Dytc:
    def __enter__(self):
        check_model()
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.DeviceIoControl.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        self.k = k
        self.handle = k.CreateFileW(r"\\.\IBMPmDrv", 0xC0000000, 3, None, 3, 0, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return self

    def __exit__(self, *_):
        self.k.CloseHandle(self.handle)

    def __call__(self, command):
        if command not in (GET, CQL_OFF):
            raise ValueError("unsupported DYTC command")
        request = wintypes.DWORD(command)
        reply = wintypes.DWORD()
        size = wintypes.DWORD()
        if not self.k.DeviceIoControl(self.handle, 0x22270C, ctypes.byref(request), 4,
                                      ctypes.byref(reply), 4, ctypes.byref(size), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if size.value != 4:
            raise RuntimeError(f"unexpected DYTC reply size: {size.value}")
        return reply.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear-once", action="store_true", help="explicitly clear active CQL once")
    args = parser.parse_args()
    with Dytc() as call:
        state = clear_cql(call) if args.clear_once else call(GET)
    print(f"DYTC={state:#010x} CQL={bool(state & CQL_BIT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
