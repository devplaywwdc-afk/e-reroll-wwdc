"""Minimal usage / smoke test for genlink_web.

    python example.py <user_id> <auth_code> [device_identifier]
"""
import sys
import json
from genlink import generate_link

if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: python example.py <user_id> <auth_code> [device_identifier]")
    uid, auth = sys.argv[1], sys.argv[2]
    dev = sys.argv[3] if len(sys.argv) > 3 else None
    print(json.dumps(generate_link(uid, auth, dev), ensure_ascii=False, indent=2))
