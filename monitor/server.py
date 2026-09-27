"""server.py — FastAPI + WebSocket ให้ web UI. เก็บ flow store + broadcast realtime.

flow ถูก push จาก collector (thread ของ frida) ผ่าน store.emit() ซึ่ง thread-safe
(schedule เข้า event loop ของ uvicorn). flow id ซ้ำ = อัปเดตแถวเดิม.
"""
import os
import json
import time
import asyncio

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response

import export

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_FLOWS = 3000

_MEDIA = {"md": "text/markdown; charset=utf-8",
          "json": "application/json; charset=utf-8",
          "jsonl": "application/x-ndjson; charset=utf-8"}


class Store:
    def __init__(self):
        self.flows = {}          # id -> flow(dict)
        self.order = []          # ลำดับ id
        self.clients = set()     # WebSocket ที่ต่ออยู่
        self.loop = None
        self.meta = {
            "pid": None, "name": None, "bytes_w": 0, "bytes_r": 0,
            "last_ts": None, "feeds": 0,
        }

    def bind_loop(self, loop):
        self.loop = loop

    # ---- เรียกจาก thread ไหนก็ได้ (collector) ----
    def emit(self, flow: dict):
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._emit_main, flow)
        else:
            self._store(flow)

    def _emit_main(self, flow):
        self._store(flow)
        msg = json.dumps({"type": "flow", "flow": flow}, ensure_ascii=False)
        for ws in list(self.clients):
            asyncio.create_task(self._safe_send(ws, msg))

    def _store(self, flow):
        fid = flow["id"]
        if fid not in self.flows:
            self.order.append(fid)
        self.flows[fid] = flow
        while len(self.order) > MAX_FLOWS:
            old = self.order.pop(0)
            self.flows.pop(old, None)

    async def _safe_send(self, ws, msg):
        try:
            await ws.send_text(msg)
        except Exception:
            self.clients.discard(ws)

    def snapshot(self):
        return [self.flows[i] for i in self.order if i in self.flows]

    def clear(self):
        self.flows.clear()
        self.order.clear()

    def note_attach(self, pid, name):
        self.meta["pid"] = pid
        self.meta["name"] = name

    def note_bytes(self, direction, n):
        if direction == "w":
            self.meta["bytes_w"] += n
        else:
            self.meta["bytes_r"] += n
        self.meta["feeds"] += 1
        self.meta["last_ts"] = time.time()


store = Store()
app = FastAPI(title="eFootball Traffic Monitor")


@app.on_event("startup")
async def _startup():
    store.bind_loop(asyncio.get_running_loop())


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


@app.get("/app.js")
def appjs():
    return FileResponse(
        os.path.join(WEB, "app.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/style.css")
def stylecss():
    return FileResponse(
        os.path.join(WEB, "style.css"),
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/status")
def api_status():
    return dict(store.meta, flows=len(store.order))


@app.get("/api/flows")
def api_flows():
    return {"flows": store.snapshot()}


@app.get("/api/flow/{fid}")
def api_flow(fid: int):
    f = store.flows.get(fid)
    return JSONResponse(f if f else {}, status_code=200 if f else 404)


@app.post("/api/clear")
def api_clear():
    store.clear()
    return {"ok": True}


@app.get("/api/export")
def api_export(format: str = "md", proto: str = "", q: str = "", download: int = 1):
    """export flow ทั้งหมด (หรือที่กรอง) เป็นไฟล์อ่านรู้เรื่อง.

    query:
      format = md | json | jsonl   (ค่าเริ่มต้น md)
      proto  = csv กรอง proto เช่น "grpc,ds"   (ว่าง = ทุก proto)
      q      = substring กรอง host/path/service/rpc
      download = 1 ให้ browser โหลดไฟล์ (Content-Disposition); 0 = แสดงในหน้า

    เขียนสำเนาลง exports/ เสมอ แล้วบอก path กลับผ่าน header X-Export-Path
    (ให้ agent อื่นไปเปิดอ่านได้จาก disk).
    """
    fmt = (format or "md").lower()
    if fmt not in export.FORMATS:
        return JSONResponse({"error": "format ไม่รองรับ", "allow": list(export.FORMATS)}, status_code=400)

    flows = store.snapshot()
    protos = [p.strip() for p in proto.split(",") if p.strip()] if proto else None
    flows = export.filter_flows(flows, protos, q or None)

    src = "http://127.0.0.1 (live store)"
    fname = export.default_name(fmt)
    try:
        saved = export.write_export(flows, fmt, source=src, filename=fname)
    except Exception as e:
        return JSONResponse({"error": "เขียนไฟล์ไม่ได้: %s" % e}, status_code=500)

    text = export.render(flows, fmt, src)
    headers = {"X-Export-Path": os.path.abspath(saved),
               "X-Export-Count": str(len(flows))}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="%s"' % fname
    return Response(content=text, media_type=_MEDIA[fmt], headers=headers)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    store.clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "snapshot", "flows": store.snapshot()}, ensure_ascii=False))
        while True:
            await ws.receive_text()      # ไม่ได้ใช้ข้อความจาก client แค่คง connection
    except Exception:
        pass
    finally:
        store.clients.discard(ws)
