#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
eFootball account -> adb-bot .dat converter (free tool).

Usage:
  * Double-click convert.bat  -> converts everything in .\input  into .\output
  * Or drag account files/folders onto convert.bat (or this .py)
      -> converted .dat land next to them in .\output

Input : our account record (.txt/.dat, JSON with user_code/user_id/auth_code)
Output: compact .dat   {"auth_code":..,"user_code":..,"user_id":..,"version":".."}
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")   # keep Thai readable in any console
except Exception:
    pass

VERSION = "2.0.0"   # adb-bot expects this; change if the bot wants another

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)   # folder the .exe sits in
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "input")
OUT_DIR = os.path.join(HERE, "output")


def convert_one(path, out_dir):
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    uc = r["user_code"]
    out = {
        "auth_code": r["auth_code"],
        "user_code": uc,
        "user_id": int(r["user_id"]),
        "version": VERSION,
    }
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f"{uc}.dat")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    return dst


def iter_files(paths):
    """Yield every .txt/.dat under the given files/folders."""
    for p in paths:
        if os.path.isdir(p):
            for fn in sorted(os.listdir(p)):
                if fn.lower().endswith((".txt", ".dat")):
                    yield os.path.join(p, fn)
        elif os.path.isfile(p) and p.lower().endswith((".txt", ".dat")):
            yield p


def main(argv):
    args = argv[1:]
    if args:
        sources = args                 # drag-drop: files/folders dropped on the bat
        out_dir = OUT_DIR
    else:
        if not os.path.isdir(IN_DIR):
            os.makedirs(IN_DIR, exist_ok=True)
            print(f"สร้างโฟลเดอร์ input แล้ว: {IN_DIR}")
            print("เอาไฟล์บัญชี (.txt/.dat) ใส่ในโฟลเดอร์ input แล้วรันใหม่")
            return 0
        sources = [IN_DIR]
        out_dir = OUT_DIR

    ok = fail = 0
    for fp in iter_files(sources):
        try:
            dst = convert_one(fp, out_dir)
            ok += 1
            print(f"[OK]   {os.path.basename(fp)} -> {os.path.basename(dst)}")
        except KeyError as e:
            fail += 1
            print(f"[ข้าม] {os.path.basename(fp)}: ขาด field {e}")
        except Exception as e:
            fail += 1
            print(f"[ข้าม] {os.path.basename(fp)}: {type(e).__name__}: {e}")

    print(f"\nเสร็จ: แปลงได้ {ok} ไฟล์, ข้าม {fail} ไฟล์")
    if ok:
        print(f"ไฟล์ผลลัพธ์อยู่ใน: {out_dir}")
    return 0


if __name__ == "__main__":
    rc = main(sys.argv)
    if getattr(sys, "frozen", False):   # exe double-click: keep window open
        try:
            input("\nกด Enter เพื่อปิด...")
        except EOFError:
            pass
    sys.exit(rc)
