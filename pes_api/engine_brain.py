"""
Local `brain` for engine_core.run().

engine_core.py is the E-WWDC v1.0.18 engine with every constant externalized to a
`brain` dict (the main app releases it per-session, decrypted in RAM). This pure-API
browser tool is the owner's own — it ships the constants in the clear, assembled here
from the already-reversed gate_crypto + flows values.
"""
import os
import json

HERE = os.path.dirname(__file__)
import gate_crypto as gc

TEMPLATES = json.load(open(os.path.join(HERE, "templates.json"), encoding="utf-8"))
ENTRY_TPL = json.load(open(os.path.join(HERE, "entry_barcelona.json"), encoding="utf-8"))

# device/eula constants recovered from a real create/login (see flows.py)
DEVICE_ID = "ae2efa4abb162b4f353a2cbb3b06cd84345726ebcd129c1b92000e79f638ef38"
DEVICE_HASH = "097679ed613607ef3d17b04e51784568"   # CREATE_USER hash (device hash)
LOGIN_HASH = "e8bbc34f90e46bbbbb6ef7a7a74df3ba"     # static hash: LOGIN + authed cmds
S_KEYWORD = TEMPLATES["CMD_LOGIN"]["s_keyword"]

EULA_INFO = {
    "eula_ip_country_a3": "THA",
    "eula_select_country_a3": "THA",
    "eula_ip_state": "NONE",
    "eula_select_state": "",
    "eula_version": {
        "eula": "8.00",
        "eula_privacy_notice": "4.00",
        "eula_myclub": "6.00",
        "eula_japan_version": "8.00",
        "eula_myclub_japan": "7.00",
    },
    "eula_country_age_kind": "BIRTH_YEAR_MONTH",
    "eula_privacy_action": "CONFIRM",
}

SURVEY = [{"survey_id": 35, "answer_id": 2}, {"survey_id": 36, "answer_id": 4}]

DEVICE_PROFILES = [
    ["SM-A526B", "Adreno (TM) 619", "SM7225"],
    ["SM-G991B", "Adreno (TM) 660", "SM8350"],
    ["SM-A546E", "Mali-G68", "s5e8835"],
    ["Pixel 7", "Mali-G710", "Tensor G2"],
    ["SM-S918B", "Adreno (TM) 740", "SM8550"],
    ["M2101K6G", "Mali-G96", "mt6877"],
    ["CPH2451", "Adreno (TM) 730", "SM8475"],
    ["2201116SG", "Adreno (TM) 730", "SM8450"],
]


def build_brain():
    return {
        "aes_key": gc.AES_KEY.hex(),
        "sign_seed": gc.SIGN_SEED.decode(),
        "gate_host": gc.GATE_HOST,
        "gate_url": gc.GATE_URL,
        "gate_client_version": "6.1.1",
        "default_prefix": "j3xdr",
        "device_id": DEVICE_ID,
        "device_hash": DEVICE_HASH,
        "login_hash": LOGIN_HASH,
        "s_keyword": S_KEYWORD,
        "eula_info": EULA_INFO,
        "survey": SURVEY,
        "device_profiles": DEVICE_PROFILES,
        "templates": TEMPLATES,
        "entry_tpl": ENTRY_TPL,
    }


BRAIN = build_brain()
