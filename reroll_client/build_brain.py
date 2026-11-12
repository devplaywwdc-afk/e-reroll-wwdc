"""Assemble the real `brain` dict from the pes_api sources at BUILD TIME.

Runs only on the dev/build machine (where pes_api lives). Pulls the reversed
gate crypto + device constants + templates and emits the brain that release.py
encrypts into brain.enc. The secrets never ship in source — only inside the
server-wrapped brain.enc.
"""
from __future__ import annotations

import json
import os

PES_API = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pes_api")


def build_brain() -> dict:
    import importlib.util
    import sys

    sys.path.insert(0, PES_API)
    import gate_crypto as gc  # type: ignore
    import flows as fl        # type: ignore

    templates = json.load(open(os.path.join(PES_API, "templates.json"), encoding="utf-8"))
    entry_tpl = json.load(open(os.path.join(PES_API, "entry_barcelona.json"), encoding="utf-8"))

    return {
        # license-layer metadata
        "client_version": "11.1.1",
        # gate crypto (reversed secrets)
        "aes_key": gc.AES_KEY.hex(),
        "sign_seed": gc.SIGN_SEED.decode(),
        "gate_host": gc.GATE_HOST,
        "gate_url": gc.GATE_URL,
        # device / flow constants
        "device_id": fl.DEVICE_ID,
        "device_hash": fl.DEVICE_HASH,
        "login_hash": fl.LOGIN_HASH,
        "s_keyword": fl.S_KEYWORD,
        "gate_client_version": "6.1.1",
        "eula_info": fl.EULA_INFO,
        "survey": fl.SURVEY,
        "default_prefix": fl.DEFAULT_PREFIX,
        "device_profiles": [list(p) for p in fl._DEVICE_PROFILES],
        # templates
        "templates": {"CMD_LOGIN": templates["CMD_LOGIN"]},
        "entry_tpl": entry_tpl,
    }


if __name__ == "__main__":
    import sys
    b = build_brain()
    out = sys.argv[1] if len(sys.argv) > 1 else None
    keys = {k: (f"<{len(str(v))} chars>" if k in ("aes_key", "entry_tpl", "templates")
                else v) for k, v in b.items()}
    if out:
        json.dump(b, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"wrote {out}")
    print(json.dumps(keys, ensure_ascii=False, indent=1, default=str)[:800])
