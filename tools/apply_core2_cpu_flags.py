#!/usr/bin/env python3
"""Apply the reviewed x64 Core 2 ABI flags to Chromium's Linux GN config."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ANCHOR = re.compile(r'^(?P<indent>[ \t]*)cpu_abi_cflags \+= \[ "-msse3" \]$', re.M)
PATCHED = re.compile(
    r'^[ \t]*cpu_abi_cflags \+= \[\n'
    r'[ \t]*"-msse3",\n'
    r'[ \t]*"-march=core2",\n'
    r'[ \t]*"-mtune=core2",\n'
    r'[ \t]*\]$',
    re.M,
)


class PatchError(ValueError):
    """The Chromium compiler configuration does not match the reviewed shape."""


def apply(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    patched = list(PATCHED.finditer(text))
    anchors = list(ANCHOR.finditer(text))
    if len(patched) == 1 and not anchors:
        return False
    if patched or text.count("-march=core2") or text.count("-mtune=core2"):
        raise PatchError("partial or ambiguous Core 2 CPU flags in compiler_cpu_abi.gn")
    if len(anchors) != 1:
        raise PatchError(
            "expected one x64 '-msse3' cpu_abi_cflags assignment in compiler_cpu_abi.gn"
        )

    indent = anchors[0].group("indent")
    replacement = "\n".join(
        (
            f'{indent}cpu_abi_cflags += [',
            f'{indent}  "-msse3",',
            f'{indent}  "-march=core2",',
            f'{indent}  "-mtune=core2",',
            f'{indent}]',
        )
    )
    path.write_text(ANCHOR.sub(lambda _: replacement, text, count=1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiler_gn", type=Path)
    args = parser.parse_args()
    try:
        changed = apply(args.compiler_gn)
    except (OSError, PatchError) as error:
        print(f"Core 2 compiler flags: {error}", file=sys.stderr)
        return 1
    print(("patched " if changed else "already patched ") + str(args.compiler_gn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
