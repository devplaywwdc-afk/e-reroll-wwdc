#!/usr/bin/env python3
"""run.py — one-shot: เตรียม frida-server + attach เกม + เสิร์ฟ web UI (Charles-like).

    python run.py                 # attach เกมที่รันอยู่ + เปิดเว็บ http://127.0.0.1:8770
    python run.py --restart       # รีสตาร์ตเกมก่อน (ล้าง frida agent ที่ค้าง) แล้ว attach
    python run.py --port 9000     # เปลี่ยนพอร์ตเว็บ
    python run.py --no-setup      # ข้ามการตั้ง frida-server/adb forward (ตั้งเองแล้ว)

ต้องมี: adb เห็นเครื่อง arm64 ที่รูท + เกมแพตช์ libdispatcher แล้ว (frida ถึงจะ attach ได้)
"""
import os
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import time
import argparse
import subprocess
import threading
import webbrowser

import uvicorn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from collector import auto_attach_loop, ensure_frida_server, adb_sh   # noqa: E402
from server import app                                        # noqa: E402

PKG = "jp.konami.pesam"
TARGET = "eFootball"
AGENT = os.path.join(HERE, "agent.js")


def restart_game():
    print("[setup] รีสตาร์ตเกม (ล้าง agent ค้าง + เก็บ traffic ตั้งแต่ boot) ...")
    adb_sh(["shell", "am force-stop %s" % PKG])
    adb_sh(["shell", "monkey -p %s -c android.intent.category.LAUNCHER 1" % PKG])
    for i in range(12, 0, -1):
        print("\r[setup] รอเกม boot ... %2ds " % i, end="", flush=True)
        time.sleep(1)
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770, help="พอร์ตเว็บ (ค่าเริ่มต้น 8770)")
    ap.add_argument("--frida-port", type=int, default=27042)
    ap.add_argument("--restart", action="store_true", help="รีสตาร์ตเกมก่อน attach")
    ap.add_argument("--no-setup", action="store_true", help="ข้าม frida-server/forward")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not args.no_setup:
        print("[setup] ตั้ง frida-server (re) + adb forward บนพอร์ต %d ..." % args.frida_port)
        ensure_frida_server(args.frida_port)
        print("[setup] frida-server + adb forward tcp:%d พร้อม" % args.frida_port)

    host = "127.0.0.1:%d" % args.frida_port
    # watcher thread — auto detect + auto attach เกม (เปิด/ปิด/restart กี่รอบก็เกาะเอง)
    # + auto-reconnect เมื่อ adb หลุด/เครื่องต่อใหม่ (ตั้ง frida-server+forward+device ใหม่ให้เอง)
    # start ก่อน restart เกม → จะ attach ตอน boot ก่อน gRPC connection เกิด = HPACK sync ตั้งแต่ต้น
    watcher = threading.Thread(
        target=auto_attach_loop,
        args=(host, AGENT, args.frida_port, not args.no_setup),
        daemon=True,
    )
    watcher.start()

    if args.restart:
        restart_game()

    url = "http://%s:%d" % (args.host, args.port)
    print("\n" + "=" * 60)
    print("  eFootball Traffic Monitor พร้อมแล้ว → เปิด %s" % url)
    print("  🟢 auto-attach: เปิดค้างไว้ได้เลย เปิดเกมเมื่อไหร่ traffic เด้งทันที")
    print("     (เกม restart/ปิด-เปิด กี่รอบก็เกาะให้เอง ไม่ต้องรันซ้ำ)")
    print("=" * 60 + "\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
