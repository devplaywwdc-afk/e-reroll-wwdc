"""eFootball Reroll — customer GUI (Tkinter, stdlib only).

Flow: Login with Discord (mandatory) -> enter License key -> Start.
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
        self.build_id = _bundled_build_id()
        self.locked_module = _locked_module()
        self.worker: threading.Thread | None = None
        self.out_dir: str | None = None

        _title = "eFootball Reroll"
        if self.locked_module:
            _title += f" — {_MODULE_TH.get(self.locked_module, self.locked_module)}"
        root.title(_title)
        root.geometry("640x520")
        root.minsize(560, 460)
        root.option_add("*Font", FONT)

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
        ttk.Label(cfr, text="License Key").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        self.key_var = tk.StringVar()
        ttk.Entry(cfr, textvariable=self.key_var, width=34, font=FONT).grid(
            row=0, column=1, columnspan=3, sticky="we", padx=6, pady=6)

        ttk.Label(cfr, text="โหมด").grid(row=1, column=0, sticky="w", padx=10, pady=6)
        self.module_var = tk.StringVar(value=self.locked_module or "reroll")
        ttk.Combobox(cfr, textvariable=self.module_var, values=list(VALID_MODULES),
                     state=("disabled" if self.locked_module else "readonly"),
                     width=12, font=FONT).grid(row=1, column=1, sticky="w", padx=6, pady=6)
        ttk.Label(cfr, text="จำนวน").grid(row=1, column=2, sticky="e", padx=6, pady=6)
        self.count_var = tk.IntVar(value=5)
        ttk.Spinbox(cfr, from_=1, to=9999, textvariable=self.count_var, width=8,
                    font=FONT).grid(row=1, column=3, sticky="w", padx=6, pady=6)

        # account folder (gacha/daily read saved .txt from here; reroll saves here)
        ttk.Label(cfr, text="โฟลเดอร์บัญชี").grid(row=2, column=0, sticky="w", padx=10, pady=6)
        self.folder_lbl = ttk.Label(cfr, text="(ค่าเริ่มต้น — ข้างตัวโปรแกรม)", foreground="#888")
        self.folder_lbl.grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=6)
        ttk.Button(cfr, text="เลือก...", command=self._pick_folder).grid(
            row=2, column=3, sticky="w", padx=6, pady=6)
        cfr.columnconfigure(1, weight=1)

        # Buttons
        bfr = ttk.Frame(self.root)
        bfr.pack(fill="x", **pad)
        self.btn_start = ttk.Button(bfr, text="เริ่ม", command=self._on_start,
                                    style="Accent.TButton")
        self.btn_start.pack(side="left")
        ttk.Button(bfr, text="ล้าง log", command=self._clear_log).pack(side="right")

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
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์ที่เก็บไฟล์บัญชี (.txt)")
        if d:
            self.out_dir = d
            n = len([f for f in os.listdir(d) if f.endswith(".txt")])
            self.folder_lbl.configure(text=f"{d}  ({n} บัญชี)", foreground="#1e7e34")
            self._log(f"โฟลเดอร์บัญชี: {d} ({n} ไฟล์ .txt)")

    def _drain_log(self):
        try:
            while True:
                self._append(self.logq.get_nowait())
        except queue.Empty:
            pass
        self.root.after(80, self._drain_log)

    def _log(self, msg: str):
        self.logq.put(msg + "\n")

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

    def _login_fail(self, err: str):
        self.btn_login.configure(state="normal", text="เข้าสู่ระบบด้วย Discord")
        self._log(f"เข้าสู่ระบบ Discord ไม่สำเร็จ: {err}")

    # ------------------------------------------------------------------ run
    def _on_start(self):
        if self.worker and self.worker.is_alive():
            self._log("กำลังทำงานอยู่ รอให้เสร็จก่อน")
            return
        if not self.discord_token:
            self._log("ต้องเข้าสู่ระบบ Discord ก่อน")
            return
        key = self.key_var.get().strip()
        if not key:
            self._log("ใส่ License Key ก่อน")
            return
        if not self.build_id:
            self._log("ไม่พบ build id (ไฟล์เสียหาย?)")
            return

        module = self.locked_module or self.module_var.get()
        count = max(1, int(self.count_var.get() or 1))
        params = {"count": count, "module": module}
        if self.out_dir:
            params["out_dir"] = self.out_dir
        self.btn_start.configure(state="disabled", text="กำลังทำงาน...")
        self._log(f"เริ่ม {module} จำนวน {count} — build {self.build_id}")

        def work():
            old = sys.stdout
            sys.stdout = _QueueWriter(self.logq)
            try:
                run_module(module, key, self.build_id, params=params,
                           discord_token=self.discord_token)
                self.logq.put("\n[เสร็จ]\n")
            except Exception as e:  # noqa: BLE001
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
