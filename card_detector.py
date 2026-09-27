"""card_detector.py — ระบบตรวจจับและประเมินนักเตะจากการสุ่ม eFootball

ทำงานแบบ Hybrid:
1. OCR ดึงชื่อนักเตะและเรตติ้ง (WinOCR ความเร็วสูง)
2. เทียบกับฐานข้อมูล players_db.json + กรองตาม reroll_config.json
3. ตรวจจับการ์ดเกรดพิเศษ (Epic, Show Time, Big Time)
4. คัดกรองตัวแจกเริ่มต้น (Messi 100, Rice 96) ออกจากการตัดสินใจ
"""

import os
import re
import json
import asyncio
from typing import Dict, Any, List, Optional
from PIL import Image
import winocr

# รายชื่อนักเตะแจกเริ่มต้นที่ทุกไอดีมีเหมือนกัน (ห้ามเอามาตัดสินว่าชนะ)
STARTER_GIFT_PLAYERS = {
    "lionel messi",
    "declan rice",
    "joaquin krkic",
    "roberto laserna",
    "sandro callejo",
    "zuhaitz andreu",
    "bert malmfors",
    "lyuben stoykov",
    "iulian moldovanu",
    "raquel lombardi",
    "rasmus bissen",
    "gregoire resnais",
    "berker avsar"
}

class CardDetector:
    def __init__(self, db_path: str = "players_db.json", config_path: str = "reroll_config.json"):
        self.db_path = db_path
        self.config_path = config_path
        self.db = self._load_json(db_path)
        self.config = self._load_json(config_path)
        self.target_map = self._build_target_map()

    def _load_json(self, path: str) -> Dict[str, Any]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[!] Warning: Cannot load {path}: {e}")
        return {}

    def reload_config(self):
        """โหลดคอนฟิกใหม่เพื่อรับค่าที่ dj เพิ่งแก้"""
        self.config = self._load_json(self.config_path)
        self.db = self._load_json(self.db_path)
        self.target_map = self._build_target_map()

    def _build_target_map(self) -> Dict[str, Dict[str, Any]]:
        """สร้าง lookup table สำหรับค้นหาชื่อนักเตะจาก alias"""
        tmap = {}
        for box in self.db.get("featured_boxes", []):
            for p in box.get("top_players", []):
                card_data = {
                    "official_name": p["name"],
                    "card_type": p.get("card_type", "Epic"),
                    "max_rating": p.get("max_rating", 0),
                    "base_rating": p.get("base_rating", 0),
                    "team": p.get("team", ""),
                    "box": box.get("box_name", "")
                }
                # map official name
                tmap[p["name"].lower()] = card_data
                # map aliases
                for alias in p.get("aliases", []):
                    tmap[alias.lower()] = card_data
        return tmap

    async def scan_image_async(self, image_path: str) -> Dict[str, Any]:
        """สแกนรูปภาพหน้าจอและประเมินผลตาม config"""
        if not os.path.exists(image_path):
            return {"matched": False, "reason": f"File not found: {image_path}", "detected_players": []}

        try:
            img = Image.open(image_path)
        except Exception as e:
            return {"matched": False, "reason": f"Cannot open image: {e}", "detected_players": []}

        # 1. รัน OCR ด้วย winocr
        ocr_result = await winocr.recognize_pil(img, "en-US")
        raw_text_lines = [line.text.strip() for line in ocr_result.lines if line.text.strip()]
        full_text = " ".join(raw_text_lines)

        rules = self.config.get("filter_rules", {})
        whitelist = [w.lower() for w in rules.get("target_player_whitelist", [])]
        target_card_types = [t.lower() for t in rules.get("target_card_types", ["epic", "show time"])]
        accept_any_epic = rules.get("accept_any_epic", True)
        accept_any_showtime = rules.get("accept_any_showtime", True)
        min_ovr = rules.get("min_overall_rating", 85)

        detected_matches = []
        is_epic_screen = "epic" in full_text.lower()
        is_showtime_screen = "show time" in full_text.lower() or "showtime" in full_text.lower()

        # 2. ตรวจสอบชื่อนักเตะจาก OCR เทียบกับ Database
        for line in raw_text_lines:
            line_clean = line.lower()
            # ข้ามตัวแจกเริ่มต้น
            if any(starter in line_clean for starter in STARTER_GIFT_PLAYERS):
                continue

            # ตรวจสอบกับ target_map ใน database
            for target_name, card_meta in self.target_map.items():
                if target_name in line_clean:
                    # ตรงกับนักเตะระดับท็อปใน DB!
                    detected_matches.append({
                        "name": card_meta["official_name"],
                        "matched_by": "database_alias",
                        "card_type": card_meta["card_type"],
                        "max_rating": card_meta["max_rating"],
                        "box": card_meta["box"]
                    })

            # ตรวจสอบกับ whitelist ใน reroll_config.json
            for w in whitelist:
                if w in line_clean and w not in [m["name"].lower() for m in detected_matches]:
                    detected_matches.append({
                        "name": w.title(),
                        "matched_by": "whitelist",
                        "card_type": "Whitelisted Player",
                        "max_rating": 0,
                        "box": "Custom"
                    })

        # 3. ตัดสินใจตามเงื่อนไข (Evaluation Logic)
        if detected_matches:
            top_hit = detected_matches[0]
            return {
                "matched": True,
                "winner": top_hit["name"],
                "card_type": top_hit["card_type"],
                "reason": f"ตรงเงื่อนไขเป้าหมาย: ได้ {top_hit['name']} ({top_hit['card_type']})",
                "detected_players": detected_matches,
                "raw_lines_sample": raw_text_lines[:15]
            }

        # ถ้าหน้าจอเปิดการ์ดแสดงคำว่า Epic หรือ Show Time ชัดเจน (เช่น หน้าผลลัพธ์กาชา)
        if accept_any_epic and is_epic_screen and "advertisement reward" not in full_text.lower():
            return {
                "matched": True,
                "winner": "Unknown Epic (Screen Detected)",
                "card_type": "Epic",
                "reason": "ตรวจพบการ์ด Epic บนหน้าจอผลลัพธ์กาชา",
                "detected_players": [],
                "raw_lines_sample": raw_text_lines[:15]
            }

        if accept_any_showtime and is_showtime_screen and "anticipated" not in full_text.lower():
            return {
                "matched": True,
                "winner": "Unknown Show Time (Screen Detected)",
                "card_type": "Show Time",
                "reason": "ตรวจพบการ์ด Show Time บนหน้าจอผลลัพธ์กาชา",
                "detected_players": [],
                "raw_lines_sample": raw_text_lines[:15]
            }

        return {
            "matched": False,
            "winner": None,
            "card_type": "Normal / Fodder",
            "reason": "ไม่พบการ์ดเป้าหมาย (เกลือ)",
            "detected_players": [],
            "raw_lines_sample": raw_text_lines[:10]
        }

    def scan_image(self, image_path: str) -> Dict[str, Any]:
        """Sync wrapper สำหรับเรียกใช้ง่ายๆ"""
        return asyncio.run(self.scan_image_async(image_path))


if __name__ == "__main__":
    import sys
    # Reconfigure UTF-8 stdout on Windows
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    detector = CardDetector()
    print("[*] CardDetector initialized with", len(detector.target_map), "player aliases.")

    # ทดสอบกับรูป dj ล่าสุด (ควรได้ False เพราะเป็นเกลือ)
    test_img = "C:/Users/jaras/.gemini/antigravity-ide/brain/934c42f3-dc1b-4b87-9992-56eeb6e3604e/.user_uploaded/media_1790168931463.png"
    if os.path.exists(test_img):
        res = detector.scan_image(test_img)
        print("\n--- Test Account Check ---")
        print(f"Matched: {res['matched']}")
        print(f"Reason:  {res['reason']}")
