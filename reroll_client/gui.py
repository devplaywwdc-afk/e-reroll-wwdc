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

# ---------------------------------------------------------------- Windows XP "Luna" skin
XP_FACE = "#ECE9D8"       # silver-beige window face
XP_TITLE_HI = "#3E92F0"   # title-bar gradient (top)
XP_TITLE_LO = "#0A57CE"   # title-bar gradient (bottom)
XP_GROUP = "#0A3D91"      # blue group-box captions
XP_ACCENT = "#2f8f2f"     # XP green "go"
XP_FIELD = "#FFFFFF"


def _blend(a, b, t):
    ca = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    cb = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))


def _apply_xp_style(style):
    """Approximate the XP Luna (blue) theme with ttk 'clam' + color overrides."""
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", font=FONT, background=XP_FACE, foreground="#1a1a1a")
    style.configure("TFrame", background=XP_FACE)
    style.configure("TLabel", background=XP_FACE, font=FONT)
    style.configure("Status.TLabel", background=XP_FACE, font=FONT_B)
    style.configure("TCheckbutton", background=XP_FACE, font=FONT)
    style.map("TCheckbutton", background=[("active", XP_FACE)])
    style.configure("TLabelframe", background=XP_FACE, bordercolor="#8aa4d0",
                    relief="groove")
    style.configure("TLabelframe.Label", background=XP_FACE, foreground=XP_GROUP,
                    font=FONT_B)
    style.configure("TButton", font=FONT, padding=5, relief="raised",
                    background=XP_FACE, bordercolor="#7f9db9", focuscolor=XP_FACE)
    style.map("TButton", background=[("active", "#dfe8f7"), ("pressed", "#c9d8ef")])
    style.configure("Accent.TButton", font=FONT_B, padding=6, foreground="#ffffff",
                    background=XP_ACCENT, bordercolor="#1f6b1f")
    style.map("Accent.TButton", background=[("active", "#37a337"), ("pressed", "#277227")])
    style.configure("Stop.TButton", font=FONT_B, padding=6, foreground="#ffffff",
                    background="#c0392b", bordercolor="#8c2820")
    style.map("Stop.TButton", background=[("active", "#d14233"), ("pressed", "#9c2b20")])
    for w in ("TCombobox", "TSpinbox", "TEntry"):
        style.configure(w, fieldbackground=XP_FIELD, background=XP_FIELD,
                        bordercolor="#7f9db9", arrowcolor="#0a3d91", relief="sunken")


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

        # run controls
        self.workers_var = tk.IntVar(value=5)             # parallel workers (reroll)
        self.verbose_var = tk.BooleanVar(value=False)     # raw per-request log
        self.stop_event = None                            # set while a run is live
        self._auth_gen = 0                                # supersede stale Discord logins
        self._iconimg = None
        self._barlogo = None

        _title = "eFootball Reroll"
        if self.locked_module:
            _title += f" — {_MODULE_TH.get(self.locked_module, self.locked_module)}"
        root.title(_title)
        w, h = 648, 610
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 4)}")
        root.minsize(560, 480)
        root.configure(bg=XP_FACE)
        root.option_add("*Font", FONT)
        self._set_icon(root)

        _apply_xp_style(ttk.Style())
        self._init_chrome()      # XP title bar (borderless) — falls back to native
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
                try:
                    self._barlogo = self._iconimg.subsample(13, 13)  # ~20px for the title bar
                except Exception:
                    self._barlogo = None
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

    # ------------------------------------------------------------------ XP window chrome
    def _init_chrome(self):
        """Borderless window + a drawn XP Luna title bar. Any failure falls back
        to the native window frame (content stays XP-themed either way)."""
        self.xp_chrome = False
        self._chrome_hwnd = None
        self._maxed = None
        try:
            self.root.overrideredirect(True)
            self.root.update_idletasks()
            self._build_titlebar()
            self._add_resize_grips()
            self.root.after(14, self._enable_taskbar)
            self.root.bind("<Map>", lambda e: self.root.after(10, self._enable_taskbar))
            self.xp_chrome = True
        except Exception:
            try:
                self.root.overrideredirect(False)
                self.root.resizable(True, True)
            except Exception:
                pass

    def _add_resize_grips(self):
        """Drag-to-resize handles on a borderless window (right/left/bottom +
        the two bottom corners), drawn as a thin themed frame so the window edge
        matches the title bar. Double-click the title bar to maximize/restore."""
        root = self.root
        BORD = "#1f66c8"
        MINW, MINH = 560, 480
        rs = {}

        def mk(cursor):
            try:
                return tk.Frame(root, bg=BORD, cursor=cursor)
            except tk.TclError:
                return tk.Frame(root, bg=BORD)

        def start(e, mode):
            rs.update(x=e.x_root, y=e.y_root, w=root.winfo_width(),
                      h=root.winfo_height(), ox=root.winfo_x(), oy=root.winfo_y(),
                      mode=mode)

        def do(e):
            dx, dy, m = e.x_root - rs["x"], e.y_root - rs["y"], rs["mode"]
            w, h, ox, oy = rs["w"], rs["h"], rs["ox"], rs["oy"]
            if "r" in m:
                w = max(MINW, rs["w"] + dx)
            if "b" in m:
                h = max(MINH, rs["h"] + dy)
            if "l" in m:
                w = max(MINW, rs["w"] - dx); ox = rs["ox"] + (rs["w"] - w)
            root.geometry(f"{int(w)}x{int(h)}+{int(ox)}+{int(oy)}")

        def grip(cursor, mode, **place):
            g = mk(cursor)
            g.place(**place)
            g.bind("<Button-1>", lambda e: start(e, mode))
            g.bind("<B1-Motion>", do)

        grip("size_we", "r", relx=1, x=-3, y=30, width=3, relheight=1.0, height=-33, anchor="ne")
        grip("size_we", "l", relx=0, x=0, y=30, width=3, relheight=1.0, height=-33, anchor="nw")
        grip("size_ns", "b", relx=0, rely=1, x=3, y=-3, relwidth=1.0, width=-6, height=3, anchor="sw")
        grip("size_nw_se", "rb", relx=1, rely=1, x=-3, y=-3, width=3, height=3, anchor="se")
        grip("size_ne_sw", "lb", relx=0, rely=1, x=0, y=-3, width=3, height=3, anchor="sw")

    def _toggle_max(self, e=None):
        if self._maxed:
            self.root.geometry(self._maxed)
            self._maxed = None
        else:
            self._maxed = self.root.geometry()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.root.geometry(f"{sw}x{sh - 48}+0+0")

    def _enable_taskbar(self):
        """Make a borderless window behave like a normal app: taskbar button +
        Alt-Tab + minimize/restore (Windows only; no-op elsewhere)."""
        if self._chrome_hwnd:
            return
        try:
            from ctypes import windll
            GWL_EXSTYLE, WS_APPWINDOW, WS_TOOLWINDOW = -20, 0x00040000, 0x00000080
            hwnd = windll.user32.GetParent(self.root.winfo_id())
            st = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, (st & ~WS_TOOLWINDOW) | WS_APPWINDOW)
            self._chrome_hwnd = hwnd
            self.root.withdraw()
            self.root.after(10, self.root.deiconify)
        except Exception:
            self._chrome_hwnd = None

    def _minimize(self):
        try:
            from ctypes import windll
            hwnd = self._chrome_hwnd or windll.user32.GetParent(self.root.winfo_id())
            windll.user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
        except Exception:
            try:
                self.root.iconify()
            except Exception:
                pass

    def _build_titlebar(self):
        W = 648
        bar = tk.Canvas(self.root, height=30, highlightthickness=0, bd=0)
        bar.pack(fill="x", side="top")
        self._titlebar = bar
        cur = {"w": W}

        def paint(w):
            w = max(200, int(w))
            bar.delete("all")
            bar.configure(width=w)
            for y in range(30):
                bar.create_line(0, y, w, y, fill=_blend(XP_TITLE_HI, XP_TITLE_LO, y / 29.0))
            bar.create_line(0, 29, w, 29, fill="#06409a")
            x0 = 9
            if self._barlogo is not None:
                bar.create_image(x0, 15, image=self._barlogo, anchor="w"); x0 += 24
            bar.create_text(x0 + 2, 15, text="eFootball Reroll", anchor="w",
                            fill="#ffffff", font=("Tahoma", 10, "bold"))
            bar.create_rectangle(w - 27, 5, w - 7, 25, fill="#d14d3a", outline="#7a2418")
            bar.create_text(w - 17, 14, text="x", fill="#ffffff", font=("Tahoma", 11, "bold"))
            bar.create_rectangle(w - 52, 5, w - 32, 25, fill="#3f82e6", outline="#1a4f94")
            bar.create_text(w - 42, 12, text="_", fill="#ffffff", font=("Tahoma", 11, "bold"))
            cur["w"] = w

        paint(W)
        self.root.update_idletasks()
        paint(self.root.winfo_width() or W)
        self._drag = {"btn": None, "dx": 0, "dy": 0}

        def press(e):
            w = cur["w"]
            if 5 <= e.y <= 25 and w - 27 <= e.x <= w - 7:
                self._drag["btn"] = "close"; return
            if 5 <= e.y <= 25 and w - 52 <= e.x <= w - 32:
                self._drag["btn"] = "min"; return
            self._drag = {"btn": None,
                          "dx": e.x_root - self.root.winfo_x(),
                          "dy": e.y_root - self.root.winfo_y()}

        def drag(e):
            if self._drag.get("btn"):
                return
            self.root.geometry(f"+{e.x_root - self._drag['dx']}+{e.y_root - self._drag['dy']}")

        def release(e):
            if self._drag.get("btn") == "close":
                self.root.destroy()
            elif self._drag.get("btn") == "min":
                self._minimize()
            self._drag["btn"] = None

        bar.bind("<Button-1>", press)
        bar.bind("<B1-Motion>", drag)
        bar.bind("<ButtonRelease-1>", release)
        bar.bind("<Double-Button-1>", self._toggle_max)
        self.root.bind("<Configure>",
                       lambda e: paint(self.root.winfo_width()) if self.xp_chrome else None)

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

        # workers (parallel) + raw-log switch
        ttk.Label(cfr, text="ตัวรันพร้อมกัน").grid(row=4, column=0, sticky="w", padx=10, pady=6)
        self.workers_sb = ttk.Spinbox(cfr, from_=1, to=64, textvariable=self.workers_var,
                                      width=6, font=FONT)
        self.workers_sb.grid(row=4, column=1, sticky="w", padx=6, pady=6)
        ttk.Checkbutton(cfr, text="Log ดิบ (แสดงรัว ๆ ทุก request)",
                        variable=self.verbose_var).grid(
                            row=4, column=2, columnspan=2, sticky="w", padx=6, pady=6)
        cfr.columnconfigure(1, weight=1)

        # Buttons
        bfr = ttk.Frame(self.root)
        bfr.pack(fill="x", **pad)
        self.btn_start = ttk.Button(bfr, text="เริ่ม", command=self._on_start,
                                    style="Accent.TButton")
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bfr, text="หยุด", command=self._on_stop,
                                   style="Stop.TButton", state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        # pack picker (gacha): scan free packs -> tick wanted players.
        # Created always; shown/hidden by _on_module_change (combined exe picks mode).
        self.btn_scan = ttk.Button(bfr, text="สแกนแพ็ค / เลือกนักเตะ",
                                   command=self._on_scan_packs)
        self.want_lbl = ttk.Label(bfr, text="ยังไม่เลือกนักเตะ", foreground="#888")
        ttk.Button(bfr, text="ล้าง log", command=self._clear_log).pack(side="right")
        ttk.Button(bfr, text="รวมไฟล์ (Merge)", command=self._on_merge).pack(
            side="right", padx=6)
        self._on_module_change()

        # live result summary (updated during a run by the engine progress hook)
        self.summary_var = tk.StringVar(value="สรุปผล: สำเร็จ 0 · ล้มเหลว 0")
        self.summary_lbl = ttk.Label(self.root, textvariable=self.summary_var,
                                     style="Status.TLabel", foreground=XP_GROUP)
        self.summary_lbl.pack(fill="x", padx=14, pady=(0, 2))

        # credit / contact (clickable) — packed BOTTOM first so the log never
        # covers it (otherwise it needs the window stretched to show).
        cred = ttk.Frame(self.root)
        cred.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        tg = ttk.Label(cred, text="ติดต่อ / อัปเดต: t.me/j3xdr", foreground="#1f66c8",
                       cursor="hand2")
        tg.pack(side="left")
        tg.bind("<Button-1>", lambda e: webbrowser.open("https://t.me/j3xdr"))
        shop = ttk.Label(cred, text="สั่งซื้อ: crgwwdc.shop", foreground="#1f66c8",
                         cursor="hand2")
        shop.pack(side="right")
        shop.bind("<Button-1>", lambda e: webbrowser.open("https://crgwwdc.shop"))

        # Log (fills the space between the buttons and the footer)
        lfr = ttk.LabelFrame(self.root, text="บันทึก")
        lfr.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(lfr, height=10, font=FONT_MONO, wrap="word",
                           bg="#1e1e1e", fg="#e0e0e0", insertbackground="#e0e0e0")
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(lfr, command=self.log.yview)
        sb.pack(side="right", fill="y", pady=8)
        self.log.configure(yscrollcommand=sb.set, state="disabled")

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
            tag = ("  [ฟรี]" if pack.get("free")
                   else "  [" + (pack.get("reason") or "ซื้อไม่ได้") + "]")
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
        # button stays clickable: clicking again cancels the stuck attempt and
        # retries (covers "opened the wrong browser / hung on กำลังเปิด...").
        self._auth_gen += 1
        gen = self._auth_gen
        self.btn_login.configure(text="กำลังรอ... (กดซ้ำเพื่อลองใหม่)")
        self._log("กำลังเข้าสู่ระบบ Discord — อนุญาต (Authorize) ในเบราว์เซอร์ที่เปิดขึ้น")
        self._log("ถ้าเปิดผิดเบราว์เซอร์หรือค้าง กดปุ่มนี้ซ้ำได้เลย (หรือก๊อปลิงก์ด้านล่างไปเปิดเอง)")

        def work():
            try:
                from loader import discord_auth
                info = discord_auth.login(
                    on_url=lambda u: self.logq.put("ลิงก์ login: " + u + "\n"))
                if gen != self._auth_gen:
                    return   # a newer click superseded this attempt
                self.discord_token = info.get("prod_access_token")
                self.discord_name = info.get("username")
                self.root.after(0, self._login_ok)
            except Exception as e:  # noqa: BLE001
                if gen != self._auth_gen:
                    return
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
        self.stop_event = threading.Event()
        self.summary_var.set("สรุปผล: สำเร็จ 0 · ล้มเหลว 0  (กำลังเริ่ม...)")
        params = {"count": count, "module": module, "fmt": self.fmt_var.get(),
                  "workers": max(1, int(self.workers_var.get() or 1)),
                  "verbose": bool(self.verbose_var.get()),
                  "stop": self.stop_event,
                  "progress": self._progress_cb}
        if self.out_dir:
            params["out_dir"] = self.out_dir   # reroll output
            params["src_dir"] = self.out_dir   # gacha/daily source
        if module == "daily":
            params["daily_show_players"] = bool(self.daily_show_var.get())
        if module == "gacha" and self.wanted_ids:
            params["wanted_ids"] = self.wanted_ids
        self.btn_start.configure(state="disabled", text="กำลังทำงาน...")
        self.btn_stop.configure(state="normal", text="หยุด")
        self._log(f"เริ่ม {module} จำนวน {count} · {params['workers']} workers — build {self.build_id}")

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
                self.root.after(0, self._run_done)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _on_stop(self):
        if self.stop_event:
            self.stop_event.set()
            self._log("กำลังหยุด... (รอรอบที่ค้างอยู่ให้จบก่อน)")
        self.btn_stop.configure(state="disabled", text="กำลังหยุด...")

    def _run_done(self):
        self.btn_start.configure(state="normal", text="เริ่ม")
        self.btn_stop.configure(state="disabled", text="หยุด")
        self.stop_event = None

    def _progress_cb(self, ok, fail, total):
        # called from engine worker threads → marshal to the Tk thread
        self.root.after(0, lambda: self.summary_var.set(
            f"สรุปผล: สำเร็จ {ok} · ล้มเหลว {fail}   (จากทั้งหมด {total})"))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
