#!/usr/bin/env python3
"""Verify a built MeetingTranscriber.exe actually contains the expected fixes.

Extracts the PyInstaller bundle's inner PYZ archive and inspects the compiled
Python modules' code-object constants. If a fix is supposed to add a string
constant (e.g. "utf-8" passed to write_text), it should appear in co_consts.

This catches the class of bug where we *think* a fix made it into the build
(green CI, matching hash) but it actually didn't (push silently rejected,
stale source bundled).

Usage:
    python scripts/verify_build.py path/to/MeetingTranscriber.exe
"""
import sys, types
from pathlib import Path

# Each check: (module name, function name, list of substrings that must appear
# somewhere in that function's recursive co_consts).
EXPECTED_FIXES = [
    ("abbu_transcriber.render",   "render_html", ["utf-8"]),
    ("abbu_transcriber.render",   "render_txt",  ["utf-8"]),
    ("abbu_transcriber.app",      "main",        []),  # just confirms module loads
    ("abbu_transcriber.pipeline", "run_pipeline", []),
    ("abbu_transcriber.utils",    "run_silent",  []),  # confirms utils module exists
]


def walk_consts(code):
    """Yield every constant from a code object and its nested code objects."""
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from walk_consts(const)
        else:
            yield const


def find_func(module_code, func_name):
    """Find a function by name within a module's code object."""
    for const in module_code.co_consts:
        if isinstance(const, types.CodeType) and const.co_name == func_name:
            return const
    return None


def verify(exe_path: Path) -> bool:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    print(f"Verifying {exe_path} ({exe_path.stat().st_size / 1024 / 1024:.1f} MB)")
    ar = CArchiveReader(str(exe_path))
    pyz_data = ar.extract("PYZ-00.pyz")
    pyz_path = Path("/tmp/_verify_inner.pyz")
    pyz_path.write_bytes(pyz_data)
    pyz = ZlibArchiveReader(str(pyz_path))

    ok = True
    for mod_name, func_name, must_contain in EXPECTED_FIXES:
        if mod_name not in pyz.toc:
            print(f"  ✗ module missing from bundle: {mod_name}")
            ok = False
            continue
        mod_code = pyz.extract(mod_name)
        if not isinstance(mod_code, types.CodeType):
            print(f"  ✗ couldn't read code object for {mod_name}")
            ok = False
            continue
        if func_name == "main":
            # Just confirm module is importable code; nothing else needed.
            print(f"  ✓ {mod_name} loads")
            continue
        func_code = find_func(mod_code, func_name)
        if func_code is None:
            print(f"  ✗ {mod_name}.{func_name} not found in bundle")
            ok = False
            continue
        consts = list(walk_consts(func_code))
        missing = [s for s in must_contain if s not in consts]
        if missing:
            print(f"  ✗ {mod_name}.{func_name} missing constants: {missing}")
            print(f"    (this means the fix containing those strings is NOT in the build)")
            ok = False
        else:
            label = f"contains {must_contain}" if must_contain else "loads"
            print(f"  ✓ {mod_name}.{func_name} {label}")
    return ok


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: verify_build.py path/to/MeetingTranscriber.exe", file=sys.stderr)
        sys.exit(2)
    target = Path(sys.argv[1])
    if not target.exists():
        print(f"Not found: {target}", file=sys.stderr); sys.exit(2)
    sys.exit(0 if verify(target) else 1)
