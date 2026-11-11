"""
eFootball Account Injector — standalone Tkinter GUI (stdlib only).
Detect emulators, inject a rerolled guest id so the game logs in as it, batch many
emulators at once. Build to a single .exe with build.bat (PyInstaller).
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import injector_core as core

BG = "#0e1116"; CARD = "#151a22"; FG = "#e6e9ef"; SUB = "#8b93a7"
GREEN = "#00ff88"; RED = "#ff5c6c"; YEL = "#ffd84d"; ACC = "#a855f7"
FONT = "Tahoma"  # renders Thai correctly in Tk (unlike Segoe UI bold / Consolas)


class App:
    def __init__(self, root):
        self.root = root
        root.title("eFootball Account Injector")
        root.geometry("920x680")
        root.configure(bg=BG)
        self.devices = []
        self.accounts = []  # loaded from folder mode
        self.single = None  # loaded from single-file mode

        root.option_add("*Font", (FONT, 10))  # Thai-safe default for all widgets
        self._style()
        self._build()
        self._set_adb(core.ADB)

    # ---- styling ----
    def _style(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                    rowheight=26, borderwidth=0, font=(FONT, 10))
        s.configure("Treeview.Heading", background="#1d2531", foreground=SUB, borderwidth=0,
                    font=(FONT, 9, "bold"))
        s.map("Treeview", background=[("selected", "#1f3b2e")], foreground=[("selected", GREEN)])

    def _hdr(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=SUB, font=("Tahoma", 9, "bold")).pack(anchor="w", pady=(10, 2))

    # ---- layout ----
    def _build(self):
        top = tk.Frame(self.root, bg=BG); top.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(top, text="eFootball Account Injector", bg=BG, fg=FG,
                 font=("Tahoma", 15, "bold")).pack(side="left")
        tk.Label(top, text="emulator only · batch", bg=BG, fg=SUB,
                 font=("Tahoma", 9)).pack(side="left", padx=10)

        # adb path
        arow = tk.Frame(self.root, bg=BG); arow.pack(fill="x", padx=14, pady=(8, 0))
        tk.Label(arow, text="adb:", bg=BG, fg=SUB).pack(side="left")
        self.adb_var = tk.StringVar()
        tk.Entry(arow, textvariable=self.adb_var, bg=CARD, fg=FG, insertbackground=FG,
                 relief="flat", width=60).pack(side="left", padx=6, ipady=3)
        tk.Button(arow, text="…", command=self._pick_adb, bg=CARD, fg=FG, relief="flat").pack(side="left")
        self.btn_scan = tk.Button(arow, text="สแกน emulator", command=self._scan,
                                  bg=ACC, fg="#fff", relief="flat", font=("Tahoma", 9, "bold"))
        self.btn_scan.pack(side="left", padx=10, ipadx=6, ipady=2)
        self.scan_lbl = tk.Label(arow, text="", bg=BG, fg=SUB); self.scan_lbl.pack(side="left")

        # devices table
        self._hdr_frame = tk.Frame(self.root, bg=BG); self._hdr_frame.pack(fill="x", padx=14)
        self._hdr(self._hdr_frame, "เครื่องที่เจอ (เลือกหลายแถวด้วย Ctrl / Shift เพื่อ batch)")
        cols = ("emu", "adb", "game", "root", "ready", "current")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings", height=8,
                                 selectmode="extended")
        for c, t, w in [("emu", "Emulator", 120), ("adb", "adb id", 160), ("game", "เกม", 55),
                        ("root", "root", 55), ("ready", "พร้อม", 90), ("current", "ไอดีปัจจุบัน", 160)]:
            self.tree.heading(c, text=t); self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x", padx=14)

        # account source
        acc = tk.Frame(self.root, bg=BG); acc.pack(fill="x", padx=14)
        self._hdr(acc, "ไอดีที่จะ inject")
        self.mode = tk.StringVar(value="folder")
        tk.Radiobutton(acc, text="โฟลเดอร์ → 1 ไอดี/เครื่อง (เรียงชื่อ)", variable=self.mode, value="folder",
                       bg=BG, fg=FG, selectcolor=CARD, activebackground=BG, command=self._mode_changed).pack(anchor="w")
        tk.Radiobutton(acc, text="ไฟล์เดียว → ยิงทุกเครื่องที่เลือก", variable=self.mode, value="single",
                       bg=BG, fg=FG, selectcolor=CARD, activebackground=BG, command=self._mode_changed).pack(anchor="w")
        prow = tk.Frame(acc, bg=BG); prow.pack(fill="x", pady=4)
        self.path_var = tk.StringVar()
        tk.Entry(prow, textvariable=self.path_var, bg=CARD, fg=FG, insertbackground=FG,
                 relief="flat", width=64).pack(side="left", ipady=3)
        tk.Button(prow, text="เลือก", command=self._browse, bg=CARD, fg=FG, relief="flat").pack(side="left", padx=6)
        self.acc_lbl = tk.Label(prow, text="", bg=BG, fg=GREEN); self.acc_lbl.pack(side="left", padx=6)

        # options + run
        opt = tk.Frame(self.root, bg=BG); opt.pack(fill="x", padx=14, pady=(6, 0))
        self.v_backup = tk.BooleanVar(value=True); self.v_clear = tk.BooleanVar(value=True); self.v_launch = tk.BooleanVar(value=True)
        for txt, var in [("backup ไอดีเดิม", self.v_backup), ("ล้าง device-bind (SYSTEM)", self.v_clear), ("เปิดเกมหลัง inject", self.v_launch)]:
            tk.Checkbutton(opt, text=txt, variable=var, bg=BG, fg=FG, selectcolor=CARD,
                           activebackground=BG).pack(side="left", padx=(0, 14))
        self.btn_run = tk.Button(opt, text="INJECT", command=self._run, bg=GREEN, fg="#04120a",
                                 relief="flat", font=("Tahoma", 10, "bold"))
        self.btn_run.pack(side="right", ipadx=12, ipady=3)

        # log
        self._hdr_f2 = tk.Frame(self.root, bg=BG); self._hdr_f2.pack(fill="x", padx=14)
        self._hdr(self._hdr_f2, "log")
        self.log = tk.Text(self.root, bg="#0a0d13", fg=FG, insertbackground=FG, relief="flat",
                           font=("Tahoma", 9), height=12)
        self.log.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        for tag, col in [("ok", GREEN), ("err", RED), ("info", SUB), ("hi", YEL)]:
            self.log.tag_config(tag, foreground=col)

    # ---- helpers ----
    def _set_adb(self, p):
        self.adb_var.set(p); core.set_adb(p)

    def _pick_adb(self):
        p = filedialog.askopenfilename(title="เลือก adb.exe", filetypes=[("adb", "adb*.exe"), ("exe", "*.exe")])
        if p:
            self._set_adb(p)

    def _mode_changed(self):
        self.path_var.set(""); self.acc_lbl.config(text=""); self.accounts = []; self.single = None

    def _browse(self):
        if self.mode.get() == "folder":
            d = filedialog.askdirectory(title="เลือกโฟลเดอร์ที่มีไฟล์ไอดี")
            if d:
                self.path_var.set(d); self.accounts = core.load_folder(d)
                self.acc_lbl.config(text=f"โหลด {len(self.accounts)} ไอดี")
        else:
            f = filedialog.askopenfilename(title="เลือกไฟล์ไอดี", filetypes=[("account", "*.txt *.dat"), ("all", "*.*")])
            if f:
                try:
                    self.single = core.load_account(f); self.path_var.set(f)
                    self.acc_lbl.config(text="✓ " + self.single["user_code"])
                except Exception as e:
                    messagebox.showerror("ไฟล์ผิด", str(e))

    def _log(self, msg, tag=None):
        self.root.after(0, lambda: (self.log.insert("end", msg + "\n", tag or ()), self.log.see("end")))

    # ---- scan ----
    def _scan(self):
        core.set_adb(self.adb_var.get() or "adb")
        self.btn_scan.config(state="disabled"); self.scan_lbl.config(text="กำลังสแกน...")
        self.tree.delete(*self.tree.get_children())
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            devs = core.list_devices()
        except Exception as e:
            self.root.after(0, lambda: self.scan_lbl.config(text="ผิดพลาด: " + str(e)))
            self.root.after(0, lambda: self.btn_scan.config(state="normal")); return
        self.devices = devs

        def fill():
            usable = 0
            for d in devs:
                if d["state"] != "device":
                    self.tree.insert("", "end", iid=d["device"],
                                     values=(d["label"], d["device"], d["state"], "—", "—", "—"))
                    continue
                ready = "✓ ใช้ได้" if d["usable"] else ("ยังไม่เปิดเกม" if not d["provisioned"] else "ไม่พร้อม")
                usable += d["usable"]
                self.tree.insert("", "end", iid=d["device"],
                                 values=(d["label"], d["device"], "✓" if d["game"] else "✗",
                                         "✓" if d["root"] else "✗", ready, d["current_id"] or "—"))
            self.scan_lbl.config(text=f"พบ {len(devs)} เครื่อง · ใช้ได้ {usable}")
            self.btn_scan.config(state="normal")
        self.root.after(0, fill)

    # ---- inject ----
    def _run(self):
        sel = list(self.tree.selection())
        sel = [d for d in sel if any(x["device"] == d and x.get("usable") for x in self.devices)]
        if not sel:
            messagebox.showwarning("เลือกเครื่อง", "เลือกเครื่องที่ 'ใช้ได้' อย่างน้อย 1 เครื่อง"); return
        if self.mode.get() == "single":
            if not self.single:
                messagebox.showwarning("เลือกไฟล์", "ยังไม่ได้เลือกไฟล์ไอดี"); return
            pairs = [(d, self.single) for d in sel]
        else:
            if not self.accounts:
                messagebox.showwarning("เลือกโฟลเดอร์", "ยังไม่ได้โหลดไอดีจากโฟลเดอร์"); return
            if len(self.accounts) < len(sel):
                if not messagebox.askyesno("ไอดีไม่พอ",
                        f"มี {len(self.accounts)} ไอดี แต่เลือก {len(sel)} เครื่อง — inject เท่าที่มีไหม?"):
                    return
            pairs = list(zip(sel, self.accounts))
        self.btn_run.config(state="disabled")
        self.log.delete("1.0", "end")
        self._log(f"เริ่ม inject {len(pairs)} เครื่อง", "info")
        for dev, acct in pairs:
            self._log(f"  {dev}  ←  {acct['user_code']}", "hi")
        threading.Thread(target=self._run_worker, args=(pairs,), daemon=True).start()

    def _run_worker(self, pairs):
        opt = dict(backup=self.v_backup.get(), clear_system=self.v_clear.get(), launch=self.v_launch.get())
        bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
        done = fail = 0
        for dev, acct in pairs:
            self._log(f"[{dev}] {acct['user_code']} ...", "info")
            try:
                ok, _ = core.inject_one(dev, acct, log=lambda m: self._log(m, "ok" if "✓" in m else ("err" if "✗" in m else None)),
                                        backup_dir=bdir, **opt)
            except Exception as e:
                ok = False; self._log(f"  ✗ {type(e).__name__}: {e}", "err")
            done += ok; fail += (not ok)
        self._log(f"=== เสร็จ: สำเร็จ {done} / ล้มเหลว {fail} ===", "ok" if not fail else "hi")
        self.root.after(0, lambda: self.btn_run.config(state="normal"))
        self.root.after(500, self._scan)  # refresh current ids


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
