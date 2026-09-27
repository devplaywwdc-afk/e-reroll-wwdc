"""export.py — dump flow store เป็นไฟล์ที่ "คน + agent อ่านรู้เรื่อง" (md / json / jsonl).

flow แต่ละตัวมี schema เดียวกับที่ collector สร้าง + web UI ใช้:
    {id, ts, time, proto, host, path, service, rpc, method, status, dur_ms,
     req_size, res_size,
     request : {headers:[[k,v]...], body_text, body_json, body_hex, body_kind},
     response: {headers:[[k,v]...], body_text, body_json, body_hex, body_kind}}

ใช้ได้ 2 ทาง:
  1) import ในตัว server:
        from export import render, EXT, summarize
        text = render(flows, "md")
  2) CLI (ดึงจาก server ที่รันอยู่ แล้วเขียนไฟล์ — สำหรับให้ agent อื่นสั่งเอง):
        python export.py                      # เขียนครบ md+json+jsonl ลง exports/
        python export.py --format md          # เอาเฉพาะ markdown
        python export.py --format json --out flows.json
        python export.py --proto grpc,ds --q Friend   # กรองก่อน export
        python export.py --url http://127.0.0.1:9000  # server คนละพอร์ต

รูปแบบ markdown ออกแบบให้ LLM/agent อ่านต่อได้ทันที: สรุปหัว + สารบัญตาราง +
ต่อ 1 flow มี Request/Response แยก headers กับ body ที่ decode ดีที่สุดแล้ว
(JSON > decoded text > hex) ใส่ code fence กำกับภาษา/ชนิด body ให้เสร็จ.
"""
import io
import os
import re
import sys
import csv
import json
import time
import argparse

EXT = {"md": ".md", "json": ".json", "jsonl": ".jsonl"}
FORMATS = tuple(EXT.keys())
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")


# ---------------------------------------------------------------- helpers ----
def _headers(msg):
    """คืน list ของ (k, v) จาก msg['headers'] ไม่ว่าจะเป็น tuple หรือ list."""
    out = []
    for h in (msg or {}).get("headers") or []:
        if isinstance(h, (list, tuple)) and len(h) >= 2:
            out.append((str(h[0]), str(h[1])))
    return out


def _fence(text, lang=""):
    """ครอบ text ด้วย code fence ที่ยาวพอ (กัน ``` ที่โผล่ในเนื้อ body)."""
    text = text if text is not None else ""
    longest = 0
    for run in re.findall(r"`+", text):
        longest = max(longest, len(run))
    bar = "`" * max(3, longest + 1)
    return "%s%s\n%s\n%s" % (bar, lang, text.rstrip("\n"), bar)


def best_body(msg):
    """เลือก body ที่อ่านรู้เรื่องที่สุด → (fence_lang, kind, text).

    ลำดับ: JSON (body_json) > decoded (body_text) > hex (body_hex) > ว่าง.
    fence_lang ใช้กำกับ syntax ใน markdown ("json"/"") — hex ไม่ใส่ lang.
    """
    msg = msg or {}
    kind = msg.get("body_kind") or "empty"
    bj = msg.get("body_json") or ""
    bt = msg.get("body_text") or ""
    bh = msg.get("body_hex") or ""
    if bj.strip():
        return "json", kind, bj
    if bt.strip():
        # protobuf tree / form / raw text — ไม่มี syntax เฉพาะ
        lang = "json" if bt.lstrip()[:1] in ("{", "[") else ""
        return lang, kind, bt
    if bh.strip():
        return "", kind or "hex", bh
    return "", kind, ""


def _name(f):
    if f.get("service") and f.get("rpc"):
        return "%s.%s" % (f["service"], f["rpc"])
    return f.get("path") or ""


def summarize(flows):
    """คืน dict สรุป: จำนวนรวม + แยกตาม proto (สำหรับหัวรายงาน / API)."""
    by = {}
    for f in flows:
        by[f.get("proto") or "?"] = by.get(f.get("proto") or "?", 0) + 1
    return {"total": len(flows), "by_proto": by}


def filter_flows(flows, protos=None, q=None):
    """กรองแบบเดียวกับ web UI: protos = set/list ของ proto ที่ยอมให้ผ่าน,
    q = substring ค้นใน host/path/service/rpc (case-insensitive)."""
    out = flows
    if protos:
        allow = set(protos)
        out = [f for f in out if (f.get("proto") or "") in allow]
    if q:
        ql = q.strip().lower()
        if ql:
            def hit(f):
                bits = [str(f.get(k) or "") for k in ("host", "path", "service", "rpc")]
                ap = f.get("afterplay") or {}
                if ap.get("play_time") is not None:
                    bits.append(str(ap["play_time"]))
                    bits.append("%ss" % ap["play_time"])
                hay = " ".join(bits)
                return ql in hay.lower()
            out = [f for f in out if hit(f)]
    return out


# --------------------------------------------------------------- markdown ----
def _md_flow(f):
    lines = []
    status = f.get("status") or "–"
    proto = (f.get("proto") or "raw")
    lines.append("## Flow %s · %s · %s %s" % (f.get("id"), proto, f.get("method") or "", status))
    meta = []
    if f.get("host"):
        meta.append("**host** `%s`" % f["host"])
    nm = _name(f)
    if nm:
        meta.append("**name** `%s`" % nm)
    if f.get("time"):
        meta.append("**time** %s" % f["time"])
    if f.get("dur_ms") is not None:
        meta.append("**dur** %s ms" % f["dur_ms"])
    meta.append("**size** %s → %s B" % (f.get("req_size"), f.get("res_size")))
    ap = f.get("afterplay") or {}
    if ap.get("play_time") is not None:
        meta.append("**play_time** %ss" % ap["play_time"])
        if ap.get("earned_coin") is not None:
            meta.append("**coin** %s" % ap["earned_coin"])
        if ap.get("earned_exp") is not None:
            meta.append("**exp** %s" % ap["earned_exp"])
    lines.append(" · ".join(meta))
    lines.append("")

    for side, title in (("request", "Request"), ("response", "Response")):
        msg = f.get(side) or {}
        hdrs = _headers(msg)
        lines.append("### %s" % title)
        if hdrs:
            htext = "\n".join("%s: %s" % (k, v) for k, v in hdrs)
            lines.append("_headers (%d)_" % len(hdrs))
            lines.append(_fence(htext, "http"))
        else:
            lines.append("_headers (0)_ — (none)")
        lang, kind, body = best_body(msg)
        if body.strip():
            lines.append("_body · %s_" % kind)
            lines.append(_fence(body, lang))
        else:
            lines.append("_body_ — (empty)")
        lines.append("")
    lines.append("---")
    return "\n".join(lines)


def render_md(flows, source=""):
    s = summarize(flows)
    by = ", ".join("%s %d" % (k, v) for k, v in sorted(s["by_proto"].items()))
    head = [
        "# EFB Traffic Export",
        "",
        "- **exported**: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "- **flows**: %d  (%s)" % (s["total"], by or "—"),
    ]
    if source:
        head.append("- **source**: %s" % source)
    head.append("")
    head.append("## Index")
    head.append("")
    head.append("| # | time | proto | host | name | method | status | dur(ms) | req→res |")
    head.append("|--:|------|-------|------|------|--------|--------|--------:|---------|")
    for f in flows:
        head.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s→%s |" % (
            f.get("id"), f.get("time") or "", f.get("proto") or "",
            (f.get("host") or "").replace("|", "\\|"),
            (_name(f) or "").replace("|", "\\|"),
            f.get("method") or "", (f.get("status") or "").replace("|", "\\|") or "–",
            f.get("dur_ms") if f.get("dur_ms") is not None else "",
            f.get("req_size"), f.get("res_size"),
        ))
    head.append("")
    head.append("---")
    head.append("")
    body = "\n".join(_md_flow(f) for f in flows)
    return "\n".join(head) + "\n" + body + "\n"


# ------------------------------------------------------------------ json  ----
def render_json(flows, source=""):
    doc = {
        "tool": "ckr-traffic-monitor",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "summary": summarize(flows),
        "flows": flows,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False)


def render_jsonl(flows, source=""):
    return "\n".join(json.dumps(f, ensure_ascii=False) for f in flows) + ("\n" if flows else "")


def render(flows, fmt, source=""):
    fmt = (fmt or "md").lower()
    if fmt == "md":
        return render_md(flows, source)
    if fmt == "json":
        return render_json(flows, source)
    if fmt == "jsonl":
        return render_jsonl(flows, source)
    raise ValueError("format ไม่รองรับ: %r (เลือกได้ %s)" % (fmt, ", ".join(FORMATS)))


def default_name(fmt):
    return "ckr_traffic_%s%s" % (time.strftime("%Y%m%d_%H%M%S"), EXT.get(fmt, ".txt"))


def write_export(flows, fmt, out_dir=EXPORT_DIR, source="", filename=None):
    """render + เขียนไฟล์ลง out_dir แล้วคืน path เต็ม (สร้างโฟลเดอร์ให้เอง)."""
    os.makedirs(out_dir, exist_ok=True)
    text = render(flows, fmt, source)
    path = os.path.join(out_dir, filename or default_name(fmt))
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


# -------------------------------------------------------------------- CLI ----
def _fetch(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("flows", [])


def main(argv=None):
    ap = argparse.ArgumentParser(description="export CKR traffic monitor flows เป็นไฟล์อ่านรู้เรื่อง")
    ap.add_argument("--url", default="http://127.0.0.1:8770",
                    help="base URL ของ traffic monitor ที่รันอยู่ (ดึง /api/flows)")
    ap.add_argument("--format", "-f", choices=FORMATS + ("all",), default="all",
                    help="รูปแบบไฟล์ (ค่าเริ่มต้น all = เขียนครบ md+json+jsonl)")
    ap.add_argument("--out", "-o", default=None,
                    help="path ไฟล์ปลายทาง (ใช้ได้เมื่อระบุ --format เดียว); ไม่ใส่ = เขียนลง exports/")
    ap.add_argument("--proto", default=None,
                    help="กรองเฉพาะ proto (คั่นด้วย comma เช่น grpc,ds)")
    ap.add_argument("--q", default=None, help="กรอง substring ใน host/path/service/rpc")
    ap.add_argument("--file", default=None,
                    help="อ่าน flows จากไฟล์ json (ที่ export ไว้) แทนการดึงจาก server")
    args = ap.parse_args(argv)

    if args.file:
        with io.open(args.file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        flows = data.get("flows", data) if isinstance(data, dict) else data
        source = "file:%s" % args.file
    else:
        try:
            flows = _fetch(args.url.rstrip("/") + "/api/flows")
        except Exception as e:
            print("ดึง flows จาก %s ไม่ได้: %s" % (args.url, e), file=sys.stderr)
            print("  (server รันอยู่ไหม? ลอง: python run.py)", file=sys.stderr)
            return 2
        source = args.url

    protos = [p.strip() for p in args.proto.split(",")] if args.proto else None
    flows = filter_flows(flows, protos, args.q)
    if not flows:
        print("ไม่มี flow ให้ export (กรองแล้วเหลือ 0)", file=sys.stderr)
        return 1

    fmts = list(FORMATS) if args.format == "all" else [args.format]
    written = []
    for fmt in fmts:
        if args.out and len(fmts) == 1:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
            with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(render(flows, fmt, source))
            written.append(args.out)
        else:
            written.append(write_export(flows, fmt, source=source))

    print("export %d flow เรียบร้อย:" % len(flows))
    for p in written:
        print("  %s" % os.path.abspath(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
