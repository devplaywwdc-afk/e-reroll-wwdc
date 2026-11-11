"""Account persistence — matches the .dat format in ตัวอย่างผลลัพท์ ID / auth_new.dat."""
import os
import json
import glob

HERE = os.path.dirname(__file__)
ACC_DIR = os.path.join(HERE, "accounts")
os.makedirs(ACC_DIR, exist_ok=True)

VERSION = "2.0.0"


def save(user_code: str, auth_code: str, user_id: int,
         device_identifier: str | None = None, extra: dict | None = None):
    rec = {"auth_code": auth_code, "user_code": user_code,
           "user_id": user_id, "version": VERSION}
    if device_identifier:
        rec["device_identifier"] = device_identifier  # kept for consistent login; .dat core 4 fields unchanged
    path = os.path.join(ACC_DIR, f"{user_code}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False))
    if extra:
        with open(os.path.join(ACC_DIR, f"{user_code}.meta.json"), "w", encoding="utf-8") as f:
            json.dump(extra, f, ensure_ascii=False, indent=1, default=str)
    return path


def load_all() -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(ACC_DIR, "*.txt"))):
        try:
            out.append(json.load(open(p, encoding="utf-8")))
        except Exception:
            pass
    return out
