#!/usr/bin/env python3
"""
api_reroll.py - eFootball Headless ADB Reroller
Pure Python + ADB. No emulator UI required.

Flow per roll:
  1. force-stop game
  2. delete AUTH SaveData
  3. launch game
  4. auto-tap first-boot dialogs (language / birthday / username)
  5. pull online_user_id_data.dat (plain JSON - no encryption)
  6. wait lobby + tap Free Daily Deal
  7. read pack cards from monitor API (localhost:8770)
  8. save good accounts; loop
"""
import os, sys, json, time, subprocess, argparse, urllib.request

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PKG         = "jp.konami.pesam"
SAVE_ROOT   = f"/data/user/0/{PKG}/files/SaveData"
AUTH_FILE   = f"{SAVE_ROOT}/AUTH/online_user_id_data.dat"
MONITOR_URL = "http://127.0.0.1:8770"

# Landscape 1280x720 coords
C_LANG_OK  = (640, 660)
C_BDAY_OK  = (640, 660)
C_USER_OK  = (640, 660)
C_TITLE    = (640, 360)
C_DEAL     = (640, 540)

TARGET_TYPES = {"epic","big time","showtime","show time","legendary","featured"}

_ALIASES: set = set()
_DB = os.path.join(os.path.dirname(__file__), "players_db.json")
try:
    for box in json.load(open(_DB, encoding="utf-8")).get("featured_boxes", []):
        for p in box.get("top_players", []):
            _ALIASES.update(a.lower() for a in p.get("aliases", []))
            _ALIASES.add(p["name"].lower())
except Exception as e:
    print(f"[warn] players_db: {e}")

def adb(dev, *a, timeout=30):
    r = subprocess.run(["adb","-s",dev]+list(a), capture_output=True,
                        encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, ((r.stdout or "")+(r.stderr or "")).strip()

def sh(dev, cmd, timeout=30):
    return adb(dev, "shell", cmd, timeout=timeout)

def root(dev, cmd):
    return sh(dev, f'su -c "{cmd}"')

def tap(dev, xy):
    sh(dev, f"input tap {xy[0]} {xy[1]}")
    time.sleep(0.5)

def screencap(dev, local):
    sh(dev, "screencap -p /sdcard/_rc.png")
    adb(dev, "pull", "/sdcard/_rc.png", local)

def ui_xml(dev):
    sh(dev, "uiautomator dump /sdcard/_ui.xml 2>/dev/null")
    _, x = sh(dev, "cat /sdcard/_ui.xml")
    return x or ""

def read_auth(dev):
    root(dev, f"cp {AUTH_FILE} /sdcard/_auth.dat 2>/dev/null")
    adb(dev, "pull", "/sdcard/_auth.dat", "_auth_tmp.dat")
    try:
        with open("_auth_tmp.dat","rb") as f:
            d = json.loads(f.read())
        return d if d.get("user_id") else None
    except Exception:
        return None

def delete_auth(dev):
    root(dev, f"rm -f {SAVE_ROOT}/AUTH/*")
    root(dev, f"rm -f {SAVE_ROOT}/SYSTEM/*")
    print("  deleted AUTH+SYSTEM")

def save_account(auth, base):
    uid = auth["user_id"]
    d = os.path.join(base, str(uid))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d,"online_user_id_data.dat"),"w",encoding="utf-8") as f:
        json.dump(auth,f,ensure_ascii=False)
    with open(os.path.join(d,"meta.json"),"w",encoding="utf-8") as f:
        json.dump({"saved_at":time.strftime("%Y-%m-%d %H:%M:%S"),**auth},f,indent=2,ensure_ascii=False)
    print(f"  saved -> {d}/")

def handle_first_boot(dev, timeout=120):
    print("  waiting first-boot dialogs...")
    deadline = time.time() + timeout
    phase = ""
    while time.time() < deadline:
        xml = ui_xml(dev)
        if any(k in xml for k in ["phasa","Language","language","ภาษา"]):
            if phase != "lang":
                print("   -> language: tap")
                tap(dev, C_LANG_OK); phase = "lang"
        elif any(k in xml for k in ["เดือนและปีเกิด","Birthday","birthday"]):
            print("   -> birthday: tap"); tap(dev, C_BDAY_OK); phase = "bday"
        elif any(k in xml for k in ["ยืนยันชื่อผู้ใช้","User Name","username"]):
            print("   -> username: tap"); tap(dev, C_USER_OK); time.sleep(1); tap(dev, C_USER_OK); phase = "user"
        elif phase == "user":
            print("  first-boot done"); return True
        time.sleep(3)
    return False

def wait_auth(dev, timeout=90):
    print("  waiting auth...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = read_auth(dev)
        if a: print(f" uid={a['user_id']}"); return a
        time.sleep(3); print(".", end="", flush=True)
    print(" timeout"); return None

def clear_monitor():
    try: urllib.request.urlopen(f"{MONITOR_URL}/api/clear", data=b"", timeout=3)
    except Exception: pass

def get_cards():
    try:
        res = urllib.request.urlopen(f"{MONITOR_URL}/api/flows", timeout=5)
        flows = json.loads(res.read().decode()).get("flows", [])
        cards = []
        for f in flows:
            body = f.get("response",{}).get("body_json",[])
            if isinstance(body,str):
                try: body = json.loads(body)
                except Exception: continue
            if not isinstance(body, list): continue
            for msg in body:
                if not isinstance(msg,dict): continue
                if any(k in str(msg.get("1","")) for k in ["SCOUT","DEAL","GACHA","DRAW","REWARD"]):
                    p = msg.get("3",{})
                    if isinstance(p,dict):
                        for key in ("player_list","reward","players","card_list"):
                            if key in p and isinstance(p[key],list): cards.extend(p[key])
        return cards
    except Exception: return []

def is_target(card, ft=""):
    ct   = str(card.get("card_type", card.get("rarity",""))).lower()
    name = str(card.get("player_name", card.get("name",""))).lower()
    if ft: return ft.lower() in ct
    return name in _ALIASES or any(t in ct for t in TARGET_TYPES)

def do_deal(dev):
    print("  waiting Free Deal...")
    deadline = time.time() + 90
    while time.time() < deadline:
        xml = ui_xml(dev)
        if any(k in xml for k in ["ดีลฟรี","Free Deal","Free_Deal"]):
            print("   -> tap free deal"); tap(dev, C_DEAL); return True
        if any(k in xml for k in ["eFootball","ID","FOOTBALL"]):
            tap(dev, C_TITLE)
        time.sleep(4)
    return False

def roll_once(dev, n, ft):
    print(f"\n{'─'*50}\n  Roll #{n}\n{'─'*50}")
    sh(dev, f"am force-stop {PKG}"); time.sleep(2)
    delete_auth(dev)
    sh(dev, f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1")
    print("  launched"); time.sleep(6)
    if not handle_first_boot(dev): print("  skip (first-boot timeout)"); return False, None
    auth = wait_auth(dev)
    if not auth: return False, None
    print(f"  user_id={auth['user_id']} code={auth.get('user_code')}")
    time.sleep(15); tap(dev, C_TITLE); time.sleep(8)
    clear_monitor()
    if not do_deal(dev): screencap(dev, f"_roll_{n:04d}_nodeal.png"); return False, None
    time.sleep(10); screencap(dev, f"_roll_{n:04d}.png")
    cards = get_cards()
    print(f"  cards={len(cards)}")
    for c in cards:
        nm = c.get("player_name", c.get("name","?")); ct = c.get("card_type", c.get("rarity","?"))
        print(f"     {nm} [{ct}]" + (" <- TARGET" if is_target(c,ft) else ""))
    if any(is_target(c,ft) for c in cards): print("  HIT!"); return True, auth
    print("  miss"); return False, None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="emulator-5556")
    ap.add_argument("--max-rolls", type=int, default=100)
    ap.add_argument("--filter", default="", help="Epic / Big Time / etc.")
    ap.add_argument("--accounts-dir", default="accounts")
    args = ap.parse_args()
    os.makedirs(args.accounts_dir, exist_ok=True)
    print("="*55+f"\n  eFootball Reroller | device={args.device} | filter={args.filter or 'auto'}\n"+"="*55)
    found = 0
    for n in range(1, args.max_rolls+1):
        try:
            hit, auth = roll_once(args.device, n, args.filter)
            if hit and auth: save_account(auth, args.accounts_dir); found += 1
        except KeyboardInterrupt: print("\n  stopped"); break
        except Exception as e: print(f"  error roll #{n}: {e}"); time.sleep(5)
    print(f"\n  done: {found} accounts in {n} rolls")

if __name__ == "__main__": main()
