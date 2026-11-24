"""Prepare a separate TPFanCtrl2 calibration INI; never activate it."""
from __future__ import annotations

import argparse
from pathlib import Path

REQUIRED = {
    b"active": b"0",
    b"singlefan": b"1",
    b"portbackend": b"pawnio",
    b"cycle": b"1",
    b"powersuspendmode": b"1",
    b"log2csv": b"1",
    b"manfanspeed": None,
    b"manmodeexit": None,
}
REPLACE = {
    b"active": b"Active=1",
    b"manfanspeed": b"ManFanSpeed=5",
    b"manmodeexit": b"ManModeExit=85",
    b"level": b"Level=1 128 0 0",
    b"level2": b"Level2=1 128 0 0",
}


def calibration_profile(source: bytes) -> bytes:
    """Require the known passive single-fan profile; preserve other settings."""
    if b"\x00" in source or b"\r" in source.replace(b"\r\n", b""):
        raise ValueError("unsupported INI encoding or newline")
    newline = b"\r\n" if b"\r\n" in source else b"\n"
    seen = set()
    result = []
    for line in source.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        ending = line[len(body):] or newline
        key, sep, value = body.partition(b"=")
        key = key.strip().lower()
        if not sep or key not in REQUIRED.keys() | REPLACE.keys():
            result.append(line)
            continue
        if key in seen:
            if key in (b"level", b"level2"):
                continue
            raise ValueError(f"duplicate setting: {key!r}")
        seen.add(key)
        expected = REQUIRED.get(key)
        if expected is not None and value.split(b"//", 1)[0].strip().lower() != expected:
            raise ValueError(f"unexpected setting: {key!r}")
        result.append(REPLACE[key] + ending if key in REPLACE else line)
        if key == b"level" and not any(
            row.partition(b"=")[0].strip().lower() == b"level2" for row in source.splitlines()
        ):
            result.append(REPLACE[b"level2"] + ending)
    if REQUIRED.keys() - seen or b"level" not in seen:
        raise ValueError("missing required passive-profile settings")
    return b"".join(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    data = calibration_profile(args.source.read_bytes())
    with args.destination.open("xb") as output:
        output.write(data)
    print(f"Prepared {args.destination}; not activated.")


if __name__ == "__main__":
    main()
