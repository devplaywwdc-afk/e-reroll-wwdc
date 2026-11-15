"""eFootball Reroll — customer GUI (Tkinter, stdlib only).

Flow: Login with Discord (only) -> server resolves rental -> Start. No key field;
if the Discord has no active rental the user is sent to the web shop to rent.
Thai-safe: everything uses Tahoma (Segoe UI / Consolas + emoji do NOT render
Thai in Tkinter). No emoji anywhere.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from loader import config
from loader.loader import VALID_MODULES, run_module

FONT = ("Tahoma", 10)
FONT_B = ("Tahoma", 10, "bold")
FONT_MONO = ("Tahoma", 9)   # Tahoma (not Consolas) so Thai in logs renders


def _bundled_build_id() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "build_id.txt"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return os.environ.get("REROLL_BUILD", "")


def _locked_module() -> str | None:
    """A per-function exe (eFootballGacha.exe) locks the GUI to that one module.
    Returns None for the generic dev build (gui.py / run.py)."""
    stem = os.path.splitext(os.path.basename(
        sys.executable if getattr(sys, "frozen", False) else sys.argv[0]))[0].lower()
    for m in VALID_MODULES:
        if m in stem:
            return m
    return None


_MODULE_TH = {"reroll": "รีโรล", "gacha": "กาชา", "daily": "เช็คอินรายวัน"}


class _QueueWriter:
    """Redirect engine print() into the GUI log queue."""
    def __init__(self, q): self.q = q
    def write(self, s):
        if s:
            self.q.put(s)
    def flush(self): pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.logq: queue.Queue = queue.Queue()
        self.discord_token: str | None = None
        self.discord_name: str | None = None
        self.entitlement: dict | None = None
        self.build_id = _bundled_build_id()
        self.locked_module = _locked_module()
        self.worker: threading.Thread | None = None
        self.out_dir: str | None = None
        self.wanted_ids: list[int] = []
        self.merge_dirs: list[str] = []
        self.fmt_var = tk.StringVar(value="txt")        # .txt / .dat
        self.daily_show_var = tk.BooleanVar(value=False)  # daily: show player in name
        self._imgrefs: list = []   # keep PhotoImage refs alive

        _title = "eFootball Reroll"
        if self.locked_module:
            _title += f" — {_MODULE_TH.get(self.locked_module, self.locked_module)}"
        root.title(_title)
        root.geometry("640x520")
        root.minsize(560, 460)
        root.option_add("*Font", FONT)
        self._iconimg = None
        self._set_icon(root)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=FONT)
        style.configure("TButton", font=FONT, padding=6)
        style.configure("Accent.TButton", font=FONT_B, padding=7)
        style.configure("TLabel", font=FONT)
        style.configure("Status.TLabel", font=FONT_B)

        self._build_ui()
        self.root.after(80, self._drain_log)
        self.root.after(400, lambda: self._check_update(manual=False))  # notice on start

    def _set_icon(self, root):
        """Window / taskbar icon from the bundled logo (png via iconphoto; ico
        fallback on Windows). Silent no-op if the asset isn't present."""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        png = next((p for p in (os.path.join(base, "logo.png"),
                                os.path.join(base, "assets", "logo.png"))
                    if os.path.exists(p)), None)
        if png:
            try:
                self._iconimg = tk.PhotoImage(file=png)
                root.iconphoto(True, self._iconimg)
            except Exception:
                pass
        ico = next((p for p in (os.path.join(base, "logo.ico"),
                                os.path.join(base, "assets", "logo.ico"))
                    if os.path.exists(p)), None)
        if ico:
            try:
                root.iconbitmap(ico)
            except Exception:
                pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        head = ttk.Frame(self.root)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="eFootball Reroll", font=("Tahoma", 14, "bold")).pack(side="left")
        ttk.Button(head, text="ตรวจอัปเดต",
                   command=lambda: self._check_update(manual=True)).pack(side="right")
        ttk.Label(head, text=f"v{config.APP_VERSION}", foreground="#888").pack(side="right", padx=8)

        # Discord row
        dfr = ttk.LabelFrame(self.root, text="บัญชี Discord")
        dfr.pack(fill="x", **pad)
        self.discord_status = ttk.Label(dfr, text="ยังไม่ได้เข้าสู่ระบบ",
                                        style="Status.TLabel", foreground="#c0392b")
        self.discord_status.pack(side="left", padx=10, pady=8)
        self.btn_login = ttk.Button(dfr, text="เข้าสู่ระบบด้วย Discord",
                                    command=self._on_login, style="Accent.TButton")
        self.btn_login.pack(side="right", padx=10, pady=8)

        # Key + options
        cfr = ttk.LabelFrame(self.root, text="ตั้งค่า")
        cfr.pack(fill="x", **pad)
        # Discord-first: no key field — the server resolves the rental from Discord.
        ttk.Label(cfr, text="สิทธิ์การเช่า").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        self.ent_lbl = ttk.Label(cfr, text="เข้าสู่ระบบ Discord เพื่อตรวจสิทธิ์",
                                 foreground="#888")
        self.ent_lbl.grid(row=0, column=1, columnspan=2, sticky="w", padx=6, pady=6)
        self.btn_buy = ttk.Button(cfr, text="ไปเช่าที่เว็บ", command=self._open_shop)
        self.btn_buy.grid(row=0, column=3, sticky="e", padx=6, pady=6)
        self.btn_buy.grid_remove()

        ttk.Label(cfr, text="โหมด").grid(row=1, column=0, sticky="w", padx=10, pady=6)
        self.module_var = tk.StringVar(value=self.locked_module or "reroll")
        self.module_cb = ttk.Combobox(cfr, textvariable=self.module_var, values=list(VALID_MODULES),
                     state=("disabled" if self.locked_module else "readonly"),
                     width=12, font=FONT)
        self.module_cb.grid(row=1, column=1, sticky="w", padx=6, pady=6)
        self.module_cb.bind("<<ComboboxSelected>>", lambda e: self._on_module_change())
        ttk.Label(cfr, text="จำนวน").grid(row=1, column=2, sticky="e", padx=6, pady=6)
        self.count_var = tk.IntVar(value=5)
        ttk.Spinbox(cfr, from_=1, to=9999, textvariable=self.count_var, width=8,
                    font=FONT).grid(row=1, column=3, sticky="w", padx=6, pady=6)

        # account folder — reroll: where files are SAVED; gacha/daily: SOURCE folder.
        self.folder_cap = ttk.Label(cfr, text="โฟลเดอร์เก็บผล")
        self.folder_cap.grid(row=2, column=0, sticky="w", padx=10, pady=6)
        self.folder_lbl = ttk.Label(cfr, text="(ค่าเริ่มต้น — ข้างตัวโปรแกรม)", foreground="#888")
        self.folder_lbl.grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=6)
        ttk.Button(cfr, text="เลือก...", command=self._pick_folder).grid(
            row=2, column=3, sticky="w", padx=6, pady=6)

        # file format + daily show-player toggle
        ttk.Label(cfr, text="รูปแบบไฟล์").grid(row=3, column=0, sticky="w", padx=10, pady=6)
        ttk.Combobox(cfr, textvariable=self.fmt_var, values=["txt", "dat"],
                     state="readonly", width=6, font=FONT).grid(
                         row=3, column=1, sticky="w", padx=6, pady=6)
        self.daily_chk = ttk.Checkbutton(
            cfr, text="เช็คอิน: ใส่ชื่อนักเตะในชื่อไฟล์", variable=self.daily_show_var)
        self.daily_chk.grid(row=3, column=2, columnspan=2, sticky="w", padx=6, pady=6)
        cfr.columnconfigure(1, weight=1)

        # Buttons
        bfr = ttk.Frame(self.root)
        bfr.pack(fill="x", **pad)
        self.btn_start = ttk.Button(bfr, text="เริ่ม", command=self._on_start,
                                    style="Accent.TButton")
        self.btn_start.pack(side="left")
        # pack picker (gacha): scan free packs -> tick wanted players.
        # Created always; shown/hidden by _on_module_change (combined exe picks mode).
        self.btn_scan = ttk.Button(bfr, text="สแกนแพ็ค / เลือกนักเตะ",
                                   command=self._on_scan_packs)
        self.want_lbl = ttk.Label(bfr, text="ยังไม่เลือกนักเตะ", foreground="#888")
        ttk.Button(bfr, text="ล้าง log", command=self._clear_log).pack(side="right")
        ttk.Button(bfr, text="รวมไฟล์ (Merge)", command=self._on_merge).pack(
            side="right", padx=6)
        self._on_module_change()

        # Log
        lfr = ttk.LabelFrame(self.root, text="บันทึก")
        lfr.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(lfr, height=12, font=FONT_MONO, wrap="word",
                           bg="#1e1e1e", fg="#e0e0e0", insertbackground="#e0e0e0")
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(lfr, command=self.log.yview)
        sb.pack(side="right", fill="y", pady=8)
        self.log.configure(yscrollcommand=sb.set, state="disabled")

        # credit / contact (clickable)
        cred = ttk.Frame(self.root)
        cred.pack(fill="x", padx=12, pady=(0, 8))
        tg = ttk.Label(cred, text="ติดต่อ / อัปเดต: t.me/j3xdr", foreground="#4a90d9",
                       cursor="hand2")
        tg.pack(side="left")
        tg.bind("<Button-1>", lambda e: webbrowser.open("https://t.me/j3xdr"))
        shop = ttk.Label(cred, text="สั่งซื้อ: crgwwdc.shop", foreground="#4a90d9",
                         cursor="hand2")
        shop.pack(side="right")
        shop.bind("<Button-1>", lambda e: webbrowser.open("https://crgwwdc.shop"))

    # ------------------------------------------------------------------ log
    def _append(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _pick_folder(self):
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์ (เก็บผล / บัญชีต้นทาง)")
        if d:
            self.out_dir = d
            n = len([f for f in os.listdir(d) if f.lower().endswith((".txt", ".dat"))])
            self.folder_lbl.configure(text=f"{d}  ({n} บัญชี)", foreground="#1e7e34")
            self._log(f"โฟลเดอร์: {d} ({n} ไฟล์บัญชี)")

    def _drain_log(self):
        try:
            while True:
                self._append(self.logq.get_nowait())
        except queue.Empty:
            pass
        self.root.after(80, self._drain_log)

    def _log(self, msg: str):
        self.logq.put(msg + "\n")

    def _on_module_change(self):
        """Show the gacha pack-picker controls only when gacha is the active mode,
        and re-verify entitlement with the server on every mode switch."""
        mode = self.locked_module or self.module_var.get()
        is_gacha = mode == "gacha"
        if is_gacha:
            self.btn_scan.pack(side="left", padx=8)
            self.want_lbl.pack(side="left", padx=4)
        else:
            self.btn_scan.pack_forget()
            self.want_lbl.pack_forget()
        # folder means "save results here" for reroll, "source accounts" otherwise
        self.folder_cap.configure(
            text="โฟลเดอร์เก็บผล" if mode == "reroll" else "โฟลเดอร์บัญชีต้นทาง")
        self.daily_chk.configure(state=("normal" if mode == "daily" else "disabled"))
        if getattr(self, "discord_token", None):
            self._refresh_entitlement()   # fresh server check each switch

    # ------------------------------------------------------------------ pack picker (gacha)
    def _on_scan_packs(self):
        if not self.discord_token:
            self._log("ต้องเข้าสู่ระบบ Discord ก่อน"); return
        if not (self.entitlement and self.entitlement.get("has_access")):
            self._log("ยังไม่มีสิทธิ์ใช้งาน — กด 'ไปเช่าที่เว็บ' ก่อน"); return
        if not self.out_dir:
            self._log("เลือกโฟลเดอร์บัญชี (.txt) ก่อนสแกนแพ็ค"); return
        self.btn_scan.configure(state="disabled", text="กำลังสแกน...")
        self._log("กำลังสแกนแพ็คจากบัญชีแรกในโฟลเดอร์...")

        def work():
            try:
                res = run_module("gacha", self.build_id,
                                 discord_token=self.discord_token,
                                 params={"action": "scan", "out_dir": self.out_dir,
                                         "src_dir": self.out_dir})
                packs = (res or {}).get("packs", [])
            except Exception as e:  # noqa: BLE001
                packs = []
                self.logq.put(f"สแกนแพ็คล้มเหลว: {e}\n")
            self.root.after(0, lambda: self._scan_done(packs))

        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self, packs):
        self.btn_scan.configure(state="normal", text="สแกนแพ็ค / เลือกนักเตะ")
        if not packs:
            self._log("ไม่พบแพ็คฟรี (หรือบัญชียังไม่พร้อม)"); return
        self._show_pack_picker(packs)

    def _load_thumb(self, url, label):
        def work():
            try:
                from io import BytesIO
                from PIL import Image, ImageTk
                import requests as rq
                data = rq.get(url, timeout=8).content
                im = Image.open(BytesIO(data)).convert("RGBA")
                im.thumbnail((56, 56))
                ph = ImageTk.PhotoImage(im)
                self._imgrefs.append(ph)
                self.root.after(0, lambda: label.configure(image=ph, text=""))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _show_pack_picker(self, packs):
        win = tk.Toplevel(self.root)
        win.title("เลือกนักเตะที่ต้องการ")
        win.geometry("520x560")
        win.transient(self.root)
        vars_by_id: dict[int, tk.BooleanVar] = {}

        canvas = tk.Canvas(win, highlightthickness=0)
        sb = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        for pack in packs:
            tag = "  [ฟรี]" if pack.get("free") else ""
            ttk.Label(inner, text=f"— {pack.get('title') or pack['agent_id']} —{tag}",
                      font=FONT_B,
                      foreground=("#1e7e34" if pack.get("free") else "#4a90d9")).pack(
                          anchor="w", padx=8, pady=(10, 2))
            for pl in pack["players"]:
                pid = pl["id"]
                row = ttk.Frame(inner)
                row.pack(fill="x", padx=10, pady=2)
                img = tk.Label(row, width=56, height=56, background="#2a2a2a")
                img.pack(side="left")
                if pl.get("art"):
                    self._load_thumb(pl["art"], img)
                var = tk.BooleanVar(value=(pid in self.wanted_ids))
                vars_by_id[pid] = var
                ov = pl.get("overall") or "?"
                nm = pl.get("name") or f"#{pid}"
                pos = pl.get("position") or ""
                ttk.Checkbutton(row, text=f"  {nm}   {ov} {pos}", variable=var).pack(
                    side="left", padx=8)

        bar = ttk.Frame(win)
        bar.pack(side="bottom", fill="x")

        def done():
            self.wanted_ids = [pid for pid, v in vars_by_id.items() if v.get()]
            self.want_lbl.configure(
                text=(f"เลือก {len(self.wanted_ids)} คน" if self.wanted_ids
                      else "ยังไม่เลือกนักเตะ"),
                foreground=("#1e7e34" if self.wanted_ids else "#888"))
            self._log(f"เลือกนักเตะ {len(self.wanted_ids)} คน")
            canvas.unbind_all("<MouseWheel>")
            win.destroy()

        ttk.Button(bar, text="เลือกเสร็จ", command=done,
                   style="Accent.TButton").pack(side="right", padx=10, pady=8)

    # ------------------------------------------------------------------ merge
    def _on_merge(self):
        if not self.discord_token:
            self._log("ต้องเข้าสู่ระบบ Discord ก่อน"); return
        if not (self.entitlement and self.entitlement.get("has_access")):
            self._log("ยังไม่มีสิทธิ์ใช้งาน — กด 'ไปเช่าที่เว็บ' ก่อน"); self._open_shop(); return

        win = tk.Toplevel(self.root)
        win.title("รวมไฟล์ (Merge)")
        win.geometry("560x420")
        win.transient(self.root)
        self.merge_dirs = []

        ttk.Label(win, text="เลือกหลายโฟลเดอร์ที่จะรวม (จับคู่ด้วย user_code เดียวกัน)",
                  font=FONT_B).pack(anchor="w", padx=12, pady=(12, 4))
        lb = tk.Listbox(win, height=8, font=FONT_MONO)
        lb.pack(fill="both", expand=True, padx=12, pady=4)

        out_var = tk.StringVar(value="")

        def add_dir():
            d = filedialog.askdirectory(title="เพิ่มโฟลเดอร์ต้นทาง", parent=win)
            if d and d not in self.merge_dirs:
                self.merge_dirs.append(d)
                n = len([f for f in os.listdir(d) if f.lower().endswith((".txt", ".dat"))])
                lb.insert("end", f"{d}  ({n})")

        def pick_out():
            d = filedialog.askdirectory(title="โฟลเดอร์ปลายทาง (ผลลัพธ์ merge)", parent=win)
            if d:
                out_var.set(d)

        row = ttk.Frame(win); row.pack(fill="x", padx=12, pady=4)
        ttk.Button(row, text="เพิ่มโฟลเดอร์...", command=add_dir).pack(side="left")
        ttk.Button(row, text="โฟลเดอร์ปลายทาง...", command=pick_out).pack(side="left", padx=6)
        ttk.Label(row, textvariable=out_var, foreground="#1e7e34").pack(side="left", padx=6)

        def start_merge():
            if not self.merge_dirs:
                self._log("เลือกอย่างน้อย 1 โฟลเดอร์ก่อน merge"); return
            dest = out_var.get() or self.merge_dirs[0]
            dirs = list(self.merge_dirs)
            fmt = self.fmt_var.get()
            win.destroy()
            self._log(f"เริ่ม merge {len(dirs)} โฟลเดอร์ -> {dest}")

            def work():
                old = sys.stdout
                sys.stdout = _QueueWriter(self.logq)
                try:
                    gate = (self.entitlement.get("modules")
                            or [self.module_var.get()])[0]
                    run_module(gate, self.build_id, discord_token=self.discord_token,
                               params={"action": "merge", "module": gate,
                                       "merge_dirs": dirs, "out_dir": dest, "fmt": fmt})
                    self.logq.put("\n[merge เสร็จ]\n")
                except Exception as e:  # noqa: BLE001
                    self.logq.put(f"\n[merge ผิดพลาด] {e}\n")
                finally:
                    sys.stdout = old

            threading.Thread(target=work, daemon=True).start()

        ttk.Button(win, text="เริ่มรวม", command=start_merge,
                   style="Accent.TButton").pack(side="bottom", pady=10)

    # ------------------------------------------------------------------ update
    def _check_update(self, manual: bool):
        def work():
            from loader import updater
            res = updater.check_latest()
            self.root.after(0, lambda: self._update_result(res, manual))
        threading.Thread(target=work, daemon=True).start()

    def _update_result(self, res: dict, manual: bool):
        if res.get("newer"):
            self._log(f"มีเวอร์ชันใหม่: {res['latest']} (ปัจจุบัน v{res['current']})")
            if messagebox.askyesno(
                    "มีอัปเดตใหม่",
                    f"เวอร์ชันใหม่ {res['latest']} พร้อมแล้ว "
                    f"(คุณใช้ v{res['current']})\n\nเปิดหน้าดาวน์โหลดเลยไหม?"):
                webbrowser.open(res["url"])
        elif manual:
            if res.get("error"):
                messagebox.showinfo("ตรวจอัปเดต",
                                    "ตรวจสอบไม่สำเร็จ (ออฟไลน์ หรือยังไม่มี release)")
            else:
                messagebox.showinfo("ตรวจอัปเดต",
                                    f"คุณใช้เวอร์ชันล่าสุดแล้ว (v{res['current']})")

    # ------------------------------------------------------------------ Discord
    def _on_login(self):
        self.btn_login.configure(state="disabled", text="กำลังเปิดเบราว์เซอร์...")
        self._log("กำลังเข้าสู่ระบบ Discord — อนุญาต (Authorize) ในเบราว์เซอร์ที่เปิดขึ้น")

        def work():
            try:
                from loader import discord_auth
                info = discord_auth.login()
                self.discord_token = info.get("prod_access_token")
                self.discord_name = info.get("username")
                self.root.after(0, self._login_ok)
            except Exception as e:  # noqa: BLE001
                self.root.after(0, lambda: self._login_fail(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _login_ok(self):
        self.discord_status.configure(
            text=f"เข้าสู่ระบบแล้ว: {self.discord_name}", foreground="#1e7e34")
        self.btn_login.configure(state="normal", text="เปลี่ยนบัญชี Discord")
        self._log(f"เข้าสู่ระบบ Discord สำเร็จ: {self.discord_name}")
        self._refresh_entitlement()

    def _login_fail(self, err: str):
        self.btn_login.configure(state="normal", text="เข้าสู่ระบบด้วย Discord")
        self._log(f"เข้าสู่ระบบ Discord ไม่สำเร็จ: {err}")

    def _open_shop(self):
        import webbrowser
        webbrowser.open(config.SHOP_URL)

    def _refresh_entitlement(self):
        if not self.discord_token:
            return
        self.ent_lbl.configure(text="กำลังตรวจสิทธิ์...", foreground="#888")

        def work():
            try:
                from loader import session
                ent = session.check_entitlement(self.discord_token)
            except Exception as e:  # noqa: BLE001
                ent = {"has_access": False, "reason": str(e)}
            self.root.after(0, lambda: self._ent_done(ent))

        threading.Thread(target=work, daemon=True).start()

    def _ent_done(self, ent: dict):
        self.entitlement = ent
        if ent.get("has_access"):
            mods = ",".join(ent.get("modules", [])) or "ครบทุกโมดูล"
            exp = ent.get("expires_at")
            tail = "ถาวร" if not exp else f"หมดอายุ {str(exp)[:10]}"
            self.ent_lbl.configure(text=f"✓ มีสิทธิ์ ({mods}) · {tail}", foreground="#1e7e34")
            self.btn_buy.grid_remove()
        else:
            reason = {"no active rental": "ยังไม่ได้เช่า",
                      "account banned": "บัญชีถูกแบน"}.get(ent.get("reason"), ent.get("reason") or "ไม่มีสิทธิ์")
            self.ent_lbl.configure(text=f"✗ {reason}", foreground="#c0392b")
            self.btn_buy.grid()

    # ------------------------------------------------------------------ run
    def _on_start(self):
        if self.worker and self.worker.is_alive():
            self._log("กำลังทำงานอยู่ รอให้เสร็จก่อน")
            return
        if not self.discord_token:
            self._log("ต้องเข้าสู่ระบบ Discord ก่อน")
            return
        if not (self.entitlement and self.entitlement.get("has_access")):
            self._log("ยังไม่มีสิทธิ์ใช้งาน — กด 'ไปเช่าที่เว็บ' เพื่อเช่าก่อน")
            self._open_shop()
            return
        if not self.build_id:
            self._log("ไม่พบ build id (ไฟล์เสียหาย?)")
            return

        module = self.locked_module or self.module_var.get()
        count = max(1, int(self.count_var.get() or 1))
        params = {"count": count, "module": module, "fmt": self.fmt_var.get()}
        if self.out_dir:
            params["out_dir"] = self.out_dir   # reroll output
            params["src_dir"] = self.out_dir   # gacha/daily source
        if module == "daily":
            params["daily_show_players"] = bool(self.daily_show_var.get())
        if module == "gacha" and self.wanted_ids:
            params["wanted_ids"] = self.wanted_ids
        self.btn_start.configure(state="disabled", text="กำลังทำงาน...")
        self._log(f"เริ่ม {module} จำนวน {count} — build {self.build_id}")

        def work():
            old = sys.stdout
            sys.stdout = _QueueWriter(self.logq)
            try:
                # re-verify entitlement with the server right before running (not
                # just the cached value) — extra friction for a patched client.
                from loader import session
                ent = session.check_entitlement(self.discord_token)
                self.entitlement = ent
                if not (ent.get("has_access") and module in (ent.get("modules") or [])):
                    self.logq.put("\n[ตรวจสิทธิ์ไม่ผ่าน] ไม่มีสิทธิ์โหมดนี้ — ไปเช่าที่เว็บ\n")
                    self.root.after(0, self._ent_done, ent)
                    self.root.after(0, self._open_shop)
                    return
                run_module(module, self.build_id, discord_token=self.discord_token,
                           params=params)
                self.logq.put("\n[เสร็จ]\n")
            except Exception as e:  # noqa: BLE001
                from loader.session import NoRental
                if isinstance(e, NoRental):
                    self.logq.put("\n[ยังไม่มีสิทธิ์] กรุณาเช่าที่เว็บก่อน\n")
                    self.root.after(0, self._open_shop)
                else:
                    self.logq.put(f"\n[ผิดพลาด] {e}\n")
            finally:
                sys.stdout = old
                self.root.after(0, lambda: self.btn_start.configure(
                    state="normal", text="เริ่ม"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
