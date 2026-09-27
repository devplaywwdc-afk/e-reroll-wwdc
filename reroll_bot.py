"""reroll_bot.py — Automated eFootball Guest ID Reroll Engine

รองรับ:
- การรีเซ็ต Guest ID โดยรักษาไฟล์ดาต้า 2GB+
- การตรวจสอบการ์ดนักเตะแบบเรียลไทม์ (Epic, Show Time, Whitelist)
- การสำรองไอดีที่สุ่มได้ตัวท็อปแยกโฟลเดอร์ตามชื่อนักเตะ
- เมนูโต้ตอบสำหรับ dj ให้เลือกรันทีละสเต็ปหรือรันออโต้เต็มรูปแบบ
"""

import os
import sys
import time
import json
import shutil
import subprocess
from datetime import datetime
from typing import Optional
from PIL import Image

# Reconfigure stdout for UTF-8 on Windows
if sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from card_detector import CardDetector

class EFootballRerollBot:
    def __init__(self, config_path: str = "reroll_config.json"):
        self.config_path = config_path
        self.detector = CardDetector(config_path=config_path)
        self.config = self.detector.config
        self.adb_dev = self._detect_device()
        self.pkg = self.config.get("reroll_settings", {}).get("package_name", "jp.konami.pesam")

    def _adb(self, cmd: str) -> str:
        """รันคำสั่ง ADB และคืนค่าผลลัพธ์เป็น string"""
        full_cmd = f"adb -s {self.adb_dev} {cmd}"
        try:
            res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            return res.stdout.strip()
        except Exception as e:
            return f"ERROR: {e}"

    def _detect_device(self) -> str:
        """ค้นหา Emulator ที่เปิดอยู่"""
        target = self.config.get("reroll_settings", {}).get("emulator_id", "emulator-5556")
        try:
            out = subprocess.run("adb devices", shell=True, capture_output=True, text=True).stdout
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == "device":
                    return parts[0]
        except Exception:
            pass
        return target

    def capture_screen(self, save_path: str = "temp_screen.png") -> str:
        """แคปภาพหน้าจอ Emulator อย่างปลอดภัยโดยไม่ติด encoding bug"""
        self._adb("shell screencap -p /sdcard/screen_temp.png")
        self._adb(f"pull /sdcard/screen_temp.png {save_path}")
        return save_path

    def tap(self, x: int, y: int, delay: float = 1.0):
        """กดตำแหน่งบนหน้าจอ"""
        self._adb(f"shell input tap {x} {y}")
        time.sleep(delay)

    def force_stop_game(self):
        """บังคับปิดเกม"""
        print(f"[*] Stopping {self.pkg}...")
        self._adb(f"shell am force-stop {self.pkg}")
        time.sleep(1.0)

    def launch_game(self):
        """เปิดเกมเข้าหน้า Splash / Title"""
        print(f"[*] Launching {self.pkg}...")
        self._adb(f"shell monkey -p {self.pkg} -c android.intent.category.LAUNCHER 1")
        time.sleep(3.0)

    def get_current_account_info(self) -> dict:
        """อ่านข้อมูล User ID ปัจจุบันจาก SaveData"""
        out = self._adb("shell \"su -c 'cat /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat'\"")
        try:
            return json.loads(out)
        except Exception:
            return {}

    def backup_account(self, tag: str = "WIN", player_name: str = "Unknown") -> Optional[str]:
        """สำรองไอดีที่ชนะ พร้อมเซฟไฟล์เซฟและรูปหน้าจอ"""
        acc_info = self.get_current_account_info()
        user_id = acc_info.get("user_id", "guest")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', player_name)

        dest_dir = os.path.join("saved_accounts", f"{tag}_{clean_name}_{timestamp}_{user_id}")
        os.makedirs(dest_dir, exist_ok=True)

        print(f"\n[+] >>> BACKING UP WINNING ACCOUNT to {dest_dir} <<<")
        # 1. ดึง online_user_id_data.dat
        self._adb(f"shell \"su -c 'cat /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat'\" > \"{dest_dir}/online_user_id_data.dat\"")
        # 2. ดึง SYSTEM000
        self._adb(f"shell \"su -c 'cat /data/user/0/jp.konami.pesam/files/SaveData/SYSTEM/SYSTEM000'\" > \"{dest_dir}/SYSTEM000\"")
        # 3. เซฟรูปหน้าจอผลการสุ่ม
        screen_file = os.path.join(dest_dir, "card_result.png")
        self.capture_screen(screen_file)

        # 4. เขียน account summary
        summary = {
            "timestamp": timestamp,
            "player_winner": player_name,
            "account_data": acc_info,
            "tag": tag
        }
        with open(os.path.join(dest_dir, "info.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"[OK] Account successfully preserved: User ID {user_id}")
        return dest_dir

    def reset_guest_id(self):
        """ลบไฟล์ Auth & System เพื่อเริ่ม Guest ID ใหม่ทันที (รักษาไฟล์ 2GB+)"""
        print("[*] Performing Guest ID Reset...")
        self.force_stop_game()
        self._adb("shell \"su -c 'rm -f /data/user/0/jp.konami.pesam/files/SaveData/AUTH/* /data/user/0/jp.konami.pesam/files/SaveData/SYSTEM/*'\"")
        print("[OK] Guest ID data wiped. Ready for fresh account.")

    def check_screen_card(self, screen_path: str = "temp_screen.png") -> dict:
        """สแกนและตัดสินใจหน้าจอปัจจุบัน"""
        self.capture_screen(screen_path)
        res = self.detector.scan_image(screen_path)
        return res

    def play_win_sound(self):
        """ส่งเสียงเตือนเมื่อสุ่มได้ตัวท็อป"""
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(1200, 300)
                time.sleep(0.1)
                winsound.Beep(1800, 400)
        except Exception:
            pass


def show_menu():
    print("\n" + "=" * 65)
    print("      eFootball Professional Guest ID Reroll Bot Engine")
    print("=" * 65)
    print(" [1] สแกนหน้าจอปัจจุบัน (Test Scan Current Screen)")
    print(" [2] สแกนรายชื่อนักเตะในไอดีนี้ (Scan My Team / Players)")
    print(" [3] รีเซ็ตสร้างไอดีใหม่ทันที (Reset Guest ID Now)")
    print(" [4] สำรองไอดีปัจจุบันนี้เก็บไว้ (Backup Current Account)")
    print(" [5] ตรวจสอบสถานะการเชื่อมต่อ Emulator & User ID")
    print(" [6] แสดงการตั้งค่าตัวกรองนักเตะ (View Filter Rules)")
    print(" [0] ออกจากโปรแกรม (Exit)")
    print("=" * 65)


def interactive_cli():
    bot = EFootballRerollBot()
    print(f"[*] Target Device: {bot.adb_dev}")

    while True:
        show_menu()
        choice = input("\nเลือกคำสั่ง (0-6): ").strip()

        if choice == "1":
            print("\n[*] กำลังจับภาพหน้าจอและรัน OCR วิเคราะห์...")
            res = bot.check_screen_card("current_test.png")
            print(f"-> ผลลัพธ์: {'[WINNER! ตรงเป้าหมาย]' if res['matched'] else '[เกลือ / ไม่ตรงเงื่อนไข]'}")
            print(f"-> เหตุผล:  {res['reason']}")
            if res.get("winner"):
                print(f"-> นักเตะ:  {res['winner']} ({res.get('card_type')})")
            if res.get("detected_players"):
                print("-> นักเตะที่ตรวจพบ:")
                for d in res["detected_players"]:
                    print(f"   * {d['name']} ({d['card_type']}) - ตู้: {d.get('box')}")

        elif choice == "2":
            print("\n[*] แนะนำให้เปิดหน้า 'ทีมของฉัน' -> 'ผู้เล่น' บน Emulator ไว้ก่อน")
            input("กด Enter เมื่อหน้าจอนักเตะพร้อมแล้ว... ")
            res = bot.check_screen_card("players_screen.png")
            print(f"-> ผลลัพธ์: {'[WINNER! มีตัวท็อปในทีม]' if res['matched'] else '[ไม่มีตัวท็อปเป้าหมาย]'}")
            print(f"-> เหตุผล:  {res['reason']}")
            if res.get("winner"):
                print(f"-> ตัวเด่น:  {res['winner']}")

        elif choice == "3":
            confirm = input("\n[!] ยืนยันการรีเซ็ตไอดีนี้เพื่อเริ่มไอดีใหม่ใช่หรือไม่? (y/n): ").strip().lower()
            if confirm == "y":
                bot.reset_guest_id()
                print("\n[+] เคลียร์ไอดีเรียบร้อย สามารถเปิดเกมเพื่อสร้างไอดีใหม่ได้ทันที")

        elif choice == "4":
            tag = input("ใส่ชื่อหรือแท็กสำหรับไอดีนี้ (เช่น Kluivert, Epic, Test): ").strip() or "MANUAL"
            bot.backup_account(tag=tag, player_name=tag)

        elif choice == "5":
            print(f"\n[*] Device: {bot.adb_dev}")
            acc = bot.get_current_account_info()
            if acc:
                print(f"[*] Active User ID:   {acc.get('user_id')}")
                print(f"[*] Active User Code: {acc.get('user_code')}")
                print(f"[*] Auth Version:     {acc.get('version')}")
            else:
                print("[!] ไม่พบข้อมูล Auth (อาจยังไม่ได้สร้างไอดีใหม่ หรือยังไม่ได้เข้าเกม)")

        elif choice == "6":
            rules = bot.config.get("filter_rules", {})
            print("\n--- การตั้งค่าตัวกรองปัจจุบัน (reroll_config.json) ---")
            print(f"รับ Epic ทุกตัว:       {rules.get('accept_any_epic')}")
            print(f"รับ Show Time ทุกตัว:  {rules.get('accept_any_showtime')}")
            print(f"รายชื่อนักเตะเป้าหมาย: {', '.join(rules.get('target_player_whitelist', []))}")
            print(f"เรตติ้งขั้นต่ำ:        {rules.get('min_overall_rating')}")

        elif choice == "0":
            print("[*] ปิดโปรแกรม.")
            break
        else:
            print("[!] ตัวเลือกไม่ถูกต้อง")


if __name__ == "__main__":
    interactive_cli()
