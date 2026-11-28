"""R17 diagnostic row transaction model; no Windows driver or live I/O here.

The transport must hold the existing EC ownership lock and report synchronization
only when it knows the previous transaction completed. An idle status byte alone
does not establish synchronization. This module is not a deployable EC driver.
"""

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    status: str
    before: tuple | None = None
    after: tuple | None = None
    row_a: bytes | None = None
    row_b: bytes | None = None
    error: str | None = None


class Observer:
    def __init__(self):
        self.inhibited = False

    def sample(self, transport):
        if self.inhibited:
            return Observation("inhibited")
        if not transport.acquire():
            return Observation("lock_unavailable")
        before = after = a = b = None
        selected = False
        status = "failed"
        error = None

        def read_state():
            return tuple(transport.read(i) for i in (0x2F, 0xC8, 0xCB, 0x81))

        def restore():
            if not transport.synchronized_idle():
                return False
            observed = transport.read(0x81)
            if observed == before[3]:
                return True
            if observed != 0x56:
                return False
            transport.write_selector(before[3])
            return transport.read(0x81) == before[3]

        try:
            if not transport.synchronized_idle():
                self.inhibited = True
                return Observation("transport_uncertain")
            before = read_state()
            # Only configurations with historical successful hardware receipts.
            if (before[0], before[1], before[2]) not in ((1, 0, 6), (1, 1, 6), (128, 1, 6)) or before[3] != 96:
                return Observation("guard_rejected", before)
            # Write might take effect even if the transport reports failure.
            selected = True
            transport.write_selector(0x56)
            if transport.read(0x81) != 0x56:
                raise ValueError("selection not confirmed")
            a = bytes(transport.read(i) for i in range(0xA0, 0xAA))
            b = bytes(transport.read(i) for i in range(0xA0, 0xAA))
            status = "complete"
        except Exception as exc:
            error = type(exc).__name__
        finally:
            try:
                if selected:
                    transport.begin_restore()
                    # Exactly one restoration attempt; never drain or retry
                    # an ambiguous transaction, never overwrite a foreign page.
                    if not restore():
                        self.inhibited = True
                        status = "restore_unverified"
                    else:
                        after = read_state()
                        if after != before:
                            status = "state_changed"
                if not transport.synchronized_idle():
                    self.inhibited = True
                    status = "restore_unverified" if selected else "transport_uncertain"
            except Exception as exc:
                self.inhibited = True
                status = "restore_unverified" if selected else "transport_uncertain"
                error = type(exc).__name__
            finally:
                transport.release()
        return Observation(status, before, after, a, b, error)


def qualify(obs):
    """Field agreement is not atomicity or independent physical truth."""
    names = ("target_rpm", "processed_rpm", "output_pwm", "upper_pwm", "floor_pwm")
    result = dict.fromkeys(names)
    result["quality"] = "unavailable"
    if obs.status != "complete" or obs.before != obs.after or obs.row_a is None or obs.row_b is None:
        return result
    if len(obs.row_a) != 10 or len(obs.row_b) != 10:
        return result
    a, b = (struct.unpack("<5H", raw) for raw in (obs.row_a, obs.row_b))
    for i in (0, 1):
        if a[i] == b[i] and a[i] <= 8191:
            result[names[i]] = a[i]
    # Bounds are a joint invariant. Zero output may legitimately be below
    # the learned floor (historical resume observation); retain it.
    if a[3:] == b[3:] and 0 <= a[4] <= a[3] <= 500 and a[3] > 0:
        result["upper_pwm"], result["floor_pwm"] = a[3:]
        if a[2] == b[2] and a[2] <= a[3]:
            result["output_pwm"] = a[2]
    n = sum(result[k] is not None for k in names)
    result["quality"] = "repeatable" if n == 5 else "partial_agreement" if n else "unavailable"
    return result
