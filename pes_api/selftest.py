"""Validate gate_crypto against REAL captured traffic (no server needed)."""
import json, re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import gate_crypto as gc

CAP = r"C:\Users\jaras\AppData\Local\Temp\claude\b2d87a41fc65cc84\a7c7300c-62e8-44f1-9e92-d8b859b3d907\scratchpad\ssl_all.jsonl"


def load_gate_tx(path):
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("tag") != "TX" or "gate_" not in r.get("asc", ""):
            continue
        raw = bytes.fromhex(r["hex"])
        i = raw.find(b"\r\n\r\n")
        if i < 0:
            continue
        head, body = raw[:i].decode("latin1"), raw[i + 4:]
        cmd = (re.search(r"gate_(CMD_[A-Z_]+)", head) or [None, "?"])[1]
        m = re.search(r"sign=([^;]+)", head)
        out.append((cmd, body, m.group(1) if m else None))
    return out


def main():
    tx = load_gate_tx(CAP)
    seen = set()
    ok = fail = 0
    for cmd, body, sign in tx:
        if cmd in seen:
            continue
        seen.add(cmd)
        # 1) decrypt
        try:
            payload = gc.decrypt_body(body)
            dec_ok = payload.get("msgid") == cmd
        except Exception as e:
            dec_ok = False
            payload = {"err": str(e)}
        # 2) verify wire sign
        sig_ok = gc.verify_sign(body, sign) if sign else False
        # 3) round-trip: re-encrypt payload, decrypt, compare msgid + a few fields
        rt_ok = False
        if dec_ok:
            try:
                reb = gc.encrypt_body(payload)
                rp = gc.decrypt_body(reb)
                rt_ok = rp.get("msgid") == cmd and rp.get("rqid") == payload.get("rqid")
                # 4) our sign verifies with our verifier
                our_sign = gc.make_sign(reb)
                rt_ok = rt_ok and gc.verify_sign(reb, our_sign)
            except Exception as e:
                payload["rt_err"] = str(e)
        status = "OK" if (dec_ok and sig_ok and rt_ok) else "FAIL"
        if status == "OK":
            ok += 1
        else:
            fail += 1
        print(f"[{status}] {cmd:28} decrypt={dec_ok} wiresign={sig_ok} roundtrip={rt_ok}")
        if dec_ok and cmd == "CMD_CREATE_USER":
            print("        fields:", list(payload.keys()))
    print(f"\n{ok} ok / {fail} fail")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
