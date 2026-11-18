"""Slip2Go REST client + PromptPay EMVCo QR payload.

Secret stays server-side (SLIP2GO_API_SECRET). The shop receives PromptPay
E-Wallet transfers (account type 02004). After the customer pays, we verify
the bank-slip QR (or slip image) with Slip2Go — not client OCR.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

import httpx

def _base_url() -> str:
    return os.environ.get("SLIP2GO_BASE_URL", "https://connect.slip2go.com").rstrip("/")


def _secret() -> str:
    return os.environ.get("SLIP2GO_API_SECRET", "").strip()

# PromptPay E-Wallet (รหัส E-Wallet) — shop destination.
PROMPTPAY_TYPE = "e_wallet"
PROMPTPAY_ACCOUNT_TYPE = "02004"
KBANK_ACCOUNT_TYPE = "01004"
PROMPTPAY_CODE_DEFAULT = "004999204111842"
PROMPTPAY_NAME_DEFAULT = "จรัสรวี ภัทรพรพันธ์"
PROMPTPAY_NAME_TH_FULL = "นาย จรัสรวี ภัทรพรพันธ์"
PROMPTPAY_LABEL_TH = "พร้อมเพย์ / E-Wallet"
_SHOP_NAME_NEEDLES = ("จรัสรวี", "ภัทรพรพันธ์")

# PromptPay AID for credit-transfer (tag 29).
_PP_AID = "A000000677010111"
# Sub-tag 03 = e-wallet id.
_PP_EWALLET_TAG = "03"


class Slip2GoError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def configured() -> bool:
    return bool(_secret())


def _auth_headers() -> dict[str, str]:
    secret = _secret()
    if not secret:
        raise Slip2GoError("slip2go_not_configured", "ยังไม่ได้ตั้งค่า Slip2Go Secret Key", 503)
    return {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for ch in data.encode("ascii"):
        crc ^= ch << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def generate_promptpay_payload(ewallet_id: str, amount_baht: float) -> str:
    """Build an EMVCo PromptPay QR payload for an E-Wallet id + amount."""
    ident = re.sub(r"\D", "", str(ewallet_id or ""))
    if len(ident) < 13:
        raise Slip2GoError("promptpay_id_invalid", "รหัสพร้อมเพย์ไม่ถูกต้อง", 500)
    merchant = _tlv("00", _PP_AID) + _tlv(_PP_EWALLET_TAG, ident)
    amount = f"{float(amount_baht):.2f}"
    payload = (
        _tlv("00", "01")
        + _tlv("01", "12")
        + _tlv("29", merchant)
        + _tlv("53", "764")
        + _tlv("54", amount)
        + _tlv("58", "TH")
        + "6304"
    )
    return payload + _crc16_ccitt(payload)


def _ok_code(code: Any) -> bool:
    """Success codes for account/QR endpoints (not verify-slip)."""
    s = str(code or "").strip()
    return s in {"200", "200000", "200001", "0"}


# Verify-slip result codes: https://slip2go.com/guide/response
# 200000 Slip found · 200001 get-info · 200200 Slip is valid (checkCondition passed)
# 200401 receiver · 200402 amount · 200403 date · 200404 not found
# 200500 fraud · 200501 duplicated · 200502 bank error
_VERIFY_OK_CODES = {"200", "200000", "200001", "200200", "0"}
_VERIFY_DUP_CODES = {"200501"}
_VERIFY_FAIL_CODES = {
    "200401",
    "200402",
    "200403",
    "200404",
    "200500",
    "200502",
    "400002",
    "400003",
    "400004",
}


def _norm_code(code: Any) -> str:
    return str(code or "").strip()


def _norm_msg(message: Any) -> str:
    return str(message or "").strip().lower()


def _is_fraud(code: str, message: str) -> bool:
    msg = _norm_msg(message)
    return code == "200500" or "fraud" in msg or "ปลอม" in str(message or "")


def _is_duplicate(code: str, message: str) -> bool:
    msg = _norm_msg(message)
    return code in _VERIFY_DUP_CODES or "duplicat" in msg or "สลิปซ้ำ" in str(message or "")


def _is_valid_message(message: str) -> bool:
    msg = _norm_msg(message)
    if any(x in msg for x in ("not match", "duplicat", "fraud", "not found", "ไม่ตรง", "ปลอม")):
        return False
    return (
        "slip is valid" in msg
        or "slip found" in msg
        or msg in {"valid", "success", "get info success"}
        or "สลิปถูกต้อง" in str(message or "")
    )


def _as_baht(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _find_first(obj: Any, keys: set[str], *, depth: int = 0) -> Any:
    if depth > 5 or obj is None:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).replace("-", "_").lower() in keys and v not in (None, ""):
                return v
        for v in obj.values():
            found = _find_first(v, keys, depth=depth + 1)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for item in obj[:8]:
            found = _find_first(item, keys, depth=depth + 1)
            if found not in (None, ""):
                return found
    return None


def _verify_payload(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("data")
    if isinstance(raw, dict):
        inner = raw.get("data")
        if isinstance(inner, dict) and (
            inner.get("transRef") or inner.get("amount") is not None
        ):
            return inner
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return {}


def _amount_token(amount_baht: float) -> str:
    """Slip2Go checkAmount: no leading zeros / commas. Keep 2 decimals if needed."""
    n = round(float(amount_baht), 2)
    if abs(n - int(n)) < 1e-9:
        return str(int(n))
    return f"{n:.2f}"


async def account_info() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        r = await http.get(f"{_base_url()}/api/account/info", headers=_auth_headers())
    try:
        data = r.json()
    except Exception:
        data = {"raw": (r.text or "")[:400]}
    if r.status_code >= 400 or not _ok_code((data or {}).get("code")):
        raise Slip2GoError(
            "slip2go_account_failed",
            str((data or {}).get("message") or f"Slip2Go HTTP {r.status_code}"),
            502,
        )
    return data if isinstance(data, dict) else {"data": data}


async def generate_qr_code(
    *,
    promptpay_code: str,
    account_name: str,
    amount_baht: float,
    promptpay_type: str = PROMPTPAY_TYPE,
) -> dict[str, Any]:
    """Ask Slip2Go for a PromptPay QR string; fall back to local EMVCo payload."""
    amount = f"{float(amount_baht):.2f}"
    local_payload = generate_promptpay_payload(promptpay_code, amount_baht)
    body = {
        "promptPayCode": re.sub(r"\D", "", promptpay_code),
        "promptPayType": promptpay_type,
        "accountName": account_name,
        "amount": amount,
    }
    image_url = ""
    remote_payload = ""
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.post(
                f"{_base_url()}/api/qr-payment/generate-qr-code",
                headers=_auth_headers(),
                json=body,
            )
        data = r.json() if r.content else {}
        if r.status_code < 400 and _ok_code(data.get("code")):
            blob = data.get("data") if isinstance(data.get("data"), dict) else {}
            remote_payload = str(
                blob.get("qrCode") or blob.get("qr_code") or blob.get("payload") or ""
            ).strip()
            if remote_payload and not remote_payload.startswith("00020"):
                remote_payload = ""
    except Exception:
        data = {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            r2 = await http.post(
                f"{_base_url()}/api/qr-payment/generate-qr-image-link",
                headers=_auth_headers(),
                json=body,
            )
        d2 = r2.json() if r2.content else {}
        if r2.status_code < 400 and _ok_code(d2.get("code")):
            image_url = str((d2.get("data") or {}).get("qrImageLink") or "").strip()
    except Exception:
        pass
    return {
        "qr_payload": remote_payload or local_payload,
        "qr_image_url": image_url,
        "account_name": account_name,
        "amount": amount,
        "source": "slip2go" if remote_payload else "local",
    }


def shop_name_ok(name: Any) -> bool:
    """Accept full shop name or first-name-only slips (SCB/others often truncate)."""
    text = str(name or "")
    if not text:
        return False
    if all(part in text for part in _SHOP_NAME_NEEDLES):
        return True
    # Many bank e-slips print "นาย จรัสรวี ภ" — surname truncated.
    return _SHOP_NAME_NEEDLES[0] in text


def looks_like_bank_trans_ref(value: Any) -> bool:
    """Reject Slip2Go request UUIDs; keep bank refs from any Thai bank.

    KBank: long digit / date+alnum. Krungthai: mixed Aa… / Ea….
    SCB: 0162…BTF…. Others (BBL/BAY/TTB/GSB/…): shorter digit or alnum refs.
    Also accepts our last-resort idempotency key prefix "s2g:".
    """
    s = str(value or "").strip()
    if s.startswith("s2g:") and len(s) >= 20:
        return True
    if not (8 <= len(s) <= 48):
        return False
    # Slip2Go error blobs often embed a UUID (full or last-group truncated).
    if re.fullmatch(
        r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{8,12}",
        s,
    ):
        return False
    if re.fullmatch(r"[0-9A-Fa-f]{32}", s):
        return False
    digits = re.sub(r"\D", "", s)
    # Pure / mostly-digit bank refs (allow 8–9 digit refs some banks still use).
    if len(digits) >= 8 and len(digits) >= (len(s) - 2):
        return True
    if len(digits) >= 10:
        return True
    # Mixed alnum refs: BBL123…, DM…, Aa367…, REF… (len 10–40, ≥6 digits).
    return bool(
        re.fullmatch(r"[0-9A-Za-z._-]{10,40}", s)
        and re.search(r"[A-Za-z]", s)
        and len(digits) >= 6
    )


def check_condition(
    amount_baht: float,
    promptpay_code: str,
    *,
    check_duplicate: bool = True,
) -> dict[str, Any]:
    """Match either the PromptPay E-Wallet id or the shop name on a KBank slip.

    K+ often prints the merchant bank account (01004) after a PromptPay scan,
    not the e-wallet id (02004). Slip2Go ORs the receiver list.
    """
    code = re.sub(r"\D", "", promptpay_code)
    return {
        "checkDuplicate": bool(check_duplicate),
        "checkReceiver": [
            {"accountType": PROMPTPAY_ACCOUNT_TYPE, "accountNumber": code},
            {"accountNameTH": PROMPTPAY_NAME_TH_FULL},
            {"accountNameTH": PROMPTPAY_NAME_DEFAULT},
        ],
        "checkAmount": {"type": "eq", "amount": _amount_token(amount_baht)},
    }


def _parse_verify(data: dict[str, Any], expected_baht: float, promptpay_code: str) -> dict[str, Any]:
    code = _norm_code(data.get("code"))
    message = str(data.get("message") or "")
    payload = _verify_payload(data)
    _REF_KEYS = {
        "transref",
        "trans_ref",
        "tranref",
        "transactionid",
        "transaction_id",
        "txnref",
        "txn_ref",
        "reference",
        "refno",
        "bankref",
        "slipref",
        "slip_ref",
    }
    candidates = [
        payload.get("transRef"),
        payload.get("trans_ref"),
        payload.get("tranRef"),
        payload.get("transactionId"),
        payload.get("transaction_id"),
        payload.get("txnRef"),
        payload.get("reference"),
        payload.get("refNo"),
        payload.get("bankRef"),
        payload.get("slipRef"),
        _find_first(payload, _REF_KEYS),
        _find_first(data, _REF_KEYS),
    ]
    trans_ref = ""
    rejected_refs: list[str] = []
    for cand in candidates:
        text = str(cand or "").strip()
        if not text:
            continue
        if looks_like_bank_trans_ref(text):
            trans_ref = text
            break
        if text not in rejected_refs and len(rejected_refs) < 4:
            rejected_refs.append(text[:48])
    got_baht = _as_baht(payload.get("amount"))
    if got_baht is None:
        got_baht = _as_baht(_find_first(data, {"amount", "amountbaht", "transamount"}))
    sender_blob = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    sacc = sender_blob.get("account") if isinstance(sender_blob.get("account"), dict) else sender_blob
    if not isinstance(sacc, dict):
        sacc = {}
    sbank = sacc.get("bank") if isinstance(sacc.get("bank"), dict) else {}
    sproxy = sacc.get("proxy") if isinstance(sacc.get("proxy"), dict) else {}
    sender_account = str(
        sbank.get("account")
        or sproxy.get("account")
        or sacc.get("account")
        or sacc.get("accountNumber")
        or sender_blob.get("accountNumber")
        or ""
    ).strip()
    sender_name = str(
        sacc.get("name") or sacc.get("accountName") or sender_blob.get("name") or ""
    ).strip()

    recv = ((payload.get("receiver") or {}).get("account") or {})
    if not isinstance(recv, dict):
        recv = {}
    proxy = recv.get("proxy") or {}
    if not isinstance(proxy, dict):
        proxy = {}
    bank_acct = ((recv.get("bank") or {}) if isinstance(recv.get("bank"), dict) else {}).get(
        "account"
    ) or ""
    dest = str(proxy.get("account") or bank_acct or "")
    dest_digits = re.sub(r"\D", "", dest)
    want = re.sub(r"\D", "", promptpay_code)
    recv_ok = bool(
        want
        and dest_digits
        and (want in dest_digits or dest_digits in want or dest_digits[-4:] == want[-4:])
    )
    amount_ok = got_baht is not None and abs(got_baht - float(expected_baht)) < 0.009
    name_ok = shop_name_ok(recv.get("name"))
    fraud = _is_fraud(code, message)
    cond_fail = code in _VERIFY_FAIL_CODES
    api_ok = (code in _VERIFY_OK_CODES or _is_valid_message(message)) and not fraud and not cond_fail
    # 200501 = Slip2Go already accepted this slip. If we never credited it
    # locally, treat as success so the shop unique voucher_id can insert.
    dup = (not fraud) and (not cond_fail) and _is_duplicate(code, message)
    # Real slip, exact amount, shop name / e-wallet dest — some banks (not only
    # K+) print the linked bank account instead of the e-wallet id → 200401.
    # name_ok now accepts truncated first-name slips; recv_ok covers dest match.
    receiver_override = (
        code == "200401"
        and (not fraud)
        and amount_ok
        and (name_ok or recv_ok)
        and bool(trans_ref)
    )
    # Last resort: Slip2Go validated amount+receiver but bank omitted/obscured
    # transRef (seen as 200200 ok with empty ref while money already arrived).
    reference_id = str(payload.get("referenceId") or "").strip()
    if (
        not trans_ref
        and reference_id
        and amount_ok
        and (not fraud)
        and (api_ok or dup)
        and (recv_ok or name_ok)
    ):
        trans_ref = "s2g:" + reference_id
    ok = bool((api_ok or dup or receiver_override) and trans_ref and amount_ok)
    if not ok and amount_ok and rejected_refs:
        message = (message or "") + f" | rejected_refs={rejected_refs!r}"
    return {
        "ok": ok,
        "code": code,
        "message": message,
        "trans_ref": trans_ref,
        "reference_id": reference_id,
        "amount_baht": got_baht,
        "amount_ok": amount_ok,
        "receiver_ok": recv_ok or api_ok or receiver_override,
        "name_ok": name_ok,
        "dest_account": dest,
        "dest_name": str(recv.get("name") or ""),
        "sender_account": sender_account,
        "sender_name": sender_name,
        "duplicate": dup,
        "rejected_refs": rejected_refs,
        "raw": data,
    }


async def verify_qr_code(
    qr_code: str,
    *,
    amount_baht: float,
    promptpay_code: str,
    check_duplicate: bool = True,
) -> dict[str, Any]:
    qr = re.sub(r"\s+", "", str(qr_code or ""))
    if len(qr) < 10:
        raise Slip2GoError("slip_qr_required", "ไม่พบ QR บนสลิป", 400)
    body = {
        "payload": {
            "qrCode": qr,
            "checkCondition": check_condition(
                amount_baht, promptpay_code, check_duplicate=check_duplicate
            ),
        }
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.post(
            f"{_base_url()}/api/verify-slip/qr-code/info",
            headers=_auth_headers(),
            json=body,
        )
    try:
        data = r.json()
    except Exception as exc:
        raise Slip2GoError("slip2go_bad_response", f"Slip2Go ตอบกลับไม่ถูกต้อง ({r.status_code})", 502) from exc
    if r.status_code >= 500:
        raise Slip2GoError("slip2go_unavailable", str(data.get("message") or "Slip2Go ไม่พร้อมใช้"), 502)
    parsed = _parse_verify(data if isinstance(data, dict) else {}, amount_baht, promptpay_code)
    parsed["http_status"] = r.status_code
    return parsed


async def verify_image_base64(
    image_b64: str,
    *,
    amount_baht: float,
    promptpay_code: str,
    check_duplicate: bool = True,
) -> dict[str, Any]:
    raw = str(image_b64 or "").strip()
    prefix = ""
    if "," in raw and raw.lower().startswith("data:"):
        prefix, raw = raw.split(",", 1)
        prefix = prefix + ","
    raw = re.sub(r"\s+", "", raw)
    if len(raw) < 80:
        raise Slip2GoError("slip_image_required", "ไม่พบรูปสลิป", 400)
    image_value = (prefix or "data:image/jpeg;base64,") + raw
    body = {
        "payload": {
            "imageBase64": image_value,
            "checkCondition": check_condition(
                amount_baht, promptpay_code, check_duplicate=check_duplicate
            ),
        }
    }
    async with httpx.AsyncClient(timeout=25.0) as http:
        r = await http.post(
            f"{_base_url()}/api/verify-slip/qr-base64/info",
            headers=_auth_headers(),
            json=body,
        )
    try:
        data = r.json()
    except Exception as exc:
        raise Slip2GoError("slip2go_bad_response", f"Slip2Go ตอบกลับไม่ถูกต้อง ({r.status_code})", 502) from exc
    if r.status_code >= 500:
        raise Slip2GoError("slip2go_unavailable", str(data.get("message") or "Slip2Go ไม่พร้อมใช้"), 502)
    parsed = _parse_verify(data if isinstance(data, dict) else {}, amount_baht, promptpay_code)
    parsed["http_status"] = r.status_code
    parsed["verify_path"] = "/api/verify-slip/qr-base64/info"
    return parsed
