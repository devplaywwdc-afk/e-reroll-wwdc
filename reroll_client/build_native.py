"""L5 — compile the sensitive modules to native .pyd (Nuitka).

The key-handling + crypto modules become machine code instead of readable
Python bytecode, so a cracker must reverse compiled native code (not edit .pyc)
to touch the unwrap/decrypt path. Run on the build machine before release.py.

Needs a C toolchain; Nuitka auto-fetches MinGW64 on first run
(--assume-yes-for-downloads). Output .pyd files replace the .py in loader/.

  python build_native.py            # compile all sensitive modules
  python build_native.py crypto_core
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOADER = os.path.join(HERE, "loader")

# modules whose logic we want in native code (no relative imports between them,
# so each compiles standalone as loader.<name>)
SENSITIVE = ["crypto_core", "brain", "canonical", "integrity", "config", "antidebug"]


def compile_module(name: str) -> None:
    src = os.path.join(LOADER, f"{name}.py")
    cmd = [
        sys.executable, "-m", "nuitka", "--module", src,
        f"--output-dir={LOADER}",
        "--no-pyi-file",
        "--remove-output",
        "--assume-yes-for-downloads",
    ]
    print(f"[native] compiling {name} -> .pyd")
    subprocess.run(cmd, check=True, cwd=LOADER)


def main() -> int:
    names = sys.argv[1:] or SENSITIVE
    for n in names:
        if n not in SENSITIVE:
            print(f"skip unknown module {n!r}")
            continue
        compile_module(n)
    print("[native] done — .pyd written into loader/. PyInstaller will bundle them.")
    print("[native] keep the .py sources for dev; ship the .pyd. Rebuild the exe "
          "after this so frozen M covers the native modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
