"""Build pipeline (PC side).

Per build:
  1. generate K_brain (32 random bytes) — lives on the server only after register
  2. build brain.enc from the gate-crypto brain dict
  3. (PyInstaller) build the onefile exe(s)       [--exe]
  4. measure M on the FROZEN exe (same code path as runtime integrity.measure)
  5. write build.blob {build_id, module_id, m_expected(hex), k_brain(hex)}
  6. register the build on the server via the admin API  [--register]

The admin token is NEVER embedded in an exe. Pass it via REROLL_ADMIN_TOKEN env
(or let the server side run the register step and hand you back the key).

Deploy order (hard rule): upload artifacts + sha-verify, THEN flip allowed=1.

Usage:
  python -I release.py --module reroll --build reroll-2026.11.1 \
         --brain-src brain_src.json [--exe] [--register]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from build_brain import build_brain
from loader import brain as brain_mod
from loader import integrity

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_SRC = os.path.join(HERE, "engine_src.py")


def load_brain(path: str | None) -> dict:
    """Real brain assembled from pes_api (default) or from an explicit JSON."""
    if not path:
        return build_brain()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_native_modules() -> None:
    """L5: compile sensitive modules to .pyd before freezing (see build_native.py)."""
    subprocess.run([sys.executable, os.path.join(HERE, "build_native.py")], check=True)


class _native_only:
    """Temporarily hide the .py of any module that now has a .pyd, so PyInstaller
    freezes the native version. Restores the .py afterward."""
    def __init__(self):
        import glob
        self.moved = []
        loader = os.path.join(HERE, "loader")
        for pyd in glob.glob(os.path.join(loader, "*.pyd")):
            base = os.path.basename(pyd).split(".")[0]
            py = os.path.join(loader, base + ".py")
            if os.path.exists(py):
                self.moved.append((py, py + ".hidden"))

    def __enter__(self):
        for py, hidden in self.moved:
            os.replace(py, hidden)
        return self

    def __exit__(self, *exc):
        for py, hidden in self.moved:
            if os.path.exists(hidden):
                os.replace(hidden, py)


def build_engine_artifact(k_brain: bytes, dist: str) -> str:
    """module_<id>.bin = nonce(12) || AES-256-GCM(k_brain).encrypt(engine_src).

    One engine source serves all modules (run() dispatches by module_id); we emit
    it under each module name the server expects.
    """
    with open(ENGINE_SRC, "rb") as f:
        src = f.read()
    nonce = os.urandom(12)
    blob = nonce + AESGCM(k_brain).encrypt(nonce, src, None)
    out = os.path.join(dist, "module.bin")
    with open(out, "wb") as f:
        f.write(blob)
    return out


def build_exe(module_id: str, dist: str) -> str:
    name = {"reroll": "eFootballReroll", "gacha": "eFootballGacha",
            "daily": "eFootballDaily"}[module_id]
    brain_enc = os.path.join(dist, "brain.enc")
    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm",
        "--name", name,
        "--distpath", dist, "--workpath", os.path.join(dist, "_work"),
        "--specpath", os.path.join(dist, "_spec"),
        "--add-data", f"{brain_enc}{sep}.",
        "--add-data", f"{os.path.join(dist, 'build_id.txt')}{sep}.",
        # engine is pulled from the server at runtime, but its 3rd-party deps must
        # be in the exe (PyInstaller can't see imports inside the encrypted blob):
        "--hidden-import", "requests",
        "--hidden-import", "msgpack",
        "--collect-all", "Crypto",
        os.path.join(HERE, "run.py"),
    ]
    print("[*] building exe:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return os.path.join(dist, name + (".exe" if os.name == "nt" else ""))


def register(server_url: str, token: str, build_id: str, module_id: str,
             m_hex: str, k_brain_hex: str) -> None:
    import ssl
    import urllib.request

    body = json.dumps({"build_id": build_id, "module_id": module_id,
                       "m_expected": m_hex, "k_brain": k_brain_hex,
                       "allowed": 1}).encode()
    req = urllib.request.Request(f"{server_url}/admin/build", data=body,
                                 method="POST")
    req.add_header("X-Admin-Token", token)
    req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed; pin not needed for admin-from-build
    with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
        print("[*] register ->", r.status, r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", required=True,
                    choices=["reroll", "gacha", "daily"])
    ap.add_argument("--build", required=True, help="build_id, e.g. reroll-2026.11.1")
    ap.add_argument("--brain-src", help="JSON file with the real brain dict")
    ap.add_argument("--dist", default=os.path.join(HERE, "dist"))
    ap.add_argument("--exe", action="store_true", help="also build the onefile exe")
    ap.add_argument("--native", action="store_true",
                    help="L5: compile crypto modules to .pyd before freezing")
    ap.add_argument("--register", action="store_true",
                    help="register build on the server (needs REROLL_ADMIN_TOKEN)")
    args = ap.parse_args()

    os.makedirs(args.dist, exist_ok=True)
    k_brain = os.urandom(32)
    brain = load_brain(args.brain_src)
    brain_enc = brain_mod.encrypt_brain(k_brain, brain)

    with open(os.path.join(args.dist, "brain.enc"), "wb") as f:
        f.write(brain_enc)
    with open(os.path.join(args.dist, "build_id.txt"), "w", encoding="utf-8") as f:
        f.write(args.build)

    engine_artifact = build_engine_artifact(k_brain, args.dist)
    print(f"[*] engine artifact -> {engine_artifact} "
          f"({os.path.getsize(engine_artifact)} bytes, encrypted with this build's k_brain)")

    m_hex = None
    exe_path = None
    if args.exe:
        if args.native:
            build_native_modules()
            with _native_only():
                exe_path = build_exe(args.module, args.dist)
        else:
            exe_path = build_exe(args.module, args.dist)
        m_hex = integrity.measure(exe_path).hex()   # measure the FROZEN exe
    else:
        print("[!] --exe not set: brain.enc written but M not measured "
              "(M must come from the frozen exe before registering).")

    blob = {"build_id": args.build, "module_id": args.module,
            "m_expected": m_hex, "k_brain": k_brain.hex(),
            "exe": exe_path}
    blob_path = os.path.join(args.dist, f"build.blob.{args.build}.json")
    with open(blob_path, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=2)
    print(f"[*] wrote {blob_path}")
    print(f"    build_id={args.build} module={args.module} "
          f"m_expected={m_hex} k_brain={k_brain.hex()}")

    if args.register:
        token = os.environ.get("REROLL_ADMIN_TOKEN")
        if not token:
            print("error: --register needs REROLL_ADMIN_TOKEN env", file=sys.stderr)
            return 2
        if not m_hex:
            print("error: cannot register without M (use --exe)", file=sys.stderr)
            return 2
        register("https://140.99.254.10:8787", token, args.build, args.module,
                 m_hex, k_brain.hex())
    else:
        print("\n[next] register on the server (or hand build_id/m_expected/k_brain "
              "to server side to run):")
        print(f'  curl -sk -X POST https://140.99.254.10:8787/admin/build \\\n'
              f'    -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \\\n'
              f'    -d \'{{"build_id":"{args.build}","module_id":"{args.module}",'
              f'"m_expected":"{m_hex}","k_brain":"{k_brain.hex()}","allowed":1}}\'')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
