# eFootball Pure API Headless Reroller — Specification & Technical Blueprint

> **Target:** eFootball Mobile (`jp.konami.pesam`)  
> **Core Objective:** Build a **100% Pure API Headless Reroller** (No ADB / No GUI / No Screen Automation required for account creation, login, claim rewards, gacha pull, and saving).  
> **Document Purpose:** Complete technical reference for AI agents / engineers to continue reverse engineering, live traffic interception, protocol implementation, and solving the current database error (`ERR_DATABASE`) blocker.

---

## 1. Project Goal & Definition

### 1.1 The Ultimate Goal
Create a standalone Python script or CLI tool (`pure_api_reroll.py`) that executes the following lifecycle autonomously:
1. **New Guest Registration:** Generates fresh client identity & device credentials → invokes `CMD_CREATE_USER` via gRPC over HTTP/2.
2. **Account Provisioning:** Extracts `user_id`, `auth_code`, and `user_code` returned by Konami servers.
3. **Session Establishment:** Authenticates with `CMD_LOGIN` → acquires active `session_id`.
4. **Maintenance & Sync:** Runs `CMD_HEARTBEAT_GRPC` every 15s to keep the session alive.
5. **Event / Pack Discovery:** Calls `CMD_GET_MYCLUB_PACK_LIST` to retrieve active promotional gacha banners & free contract tickets.
6. **Execution (Pulling):** Executes pack open requests (`CMD_EXEC_LOTTERY` / `CMD_MYCLUB_PACK_EXEC`).
7. **Evaluation & Storage:** Evaluates obtained player cards against threshold criteria (e.g., Epic/Showtime ratings) → saves credentials to disk (`accounts/`) or discards and repeats.

### 1.2 Comparison: Legacy (ADB) vs Target (Pure API)
| Feature | Legacy Method (`api_reroll.py` / `reroll_bot.py`) | Target Method (Pure API Headless) |
|---|---|---|
| **Speed per loop** | 2 – 5 minutes (boot, UI render, tutorial clicks) | **1 – 3 seconds** (raw HTTP/2 frames) |
| **Resource cost** | 2–4 GB RAM per emulator instance | **< 30 MB RAM**, run hundreds concurrently |
| **Dependencies** | MuMu / BlueStacks, Android UI clicks, OCR | Python 3.10+, `h2`, `msgpack` |
| **Scalability** | 2–4 bots max per host PC | **Unlimited** / cloud-deployable VPS |

---

## 2. Network Protocol Architecture

### 2.1 Protocol Stack
```
+-------------------------------------------------------------+
| MsgPack Payload (Dictionary / Key-Value map)                |
+-------------------------------------------------------------+
| Base64 Encoding                                             |
+-------------------------------------------------------------+
| Protobuf Envelope (Wire format: 0x0a Cmd, 0x10 Seq, 0x1a B64)|
+-------------------------------------------------------------+
| gRPC Message Framing (1-byte compressed flag + 4-byte len)  |
+-------------------------------------------------------------+
| HTTP/2 Framing (DATA frames, multiplexed Stream ID)         |
+-------------------------------------------------------------+
| TLS 1.3 / OpenSSL (Statically linked in libUE4.so)          |
+-------------------------------------------------------------+
```

- **Host:** `pes22-game.cs.konami.net:443`
- **Path:** `/command_service.CommandService/CommandStream`
- **HTTP/2 Headers:**
  ```http
  :scheme: https
  :method: POST
  :authority: pes22-game.cs.konami.net:443
  :path: /command_service.CommandService/CommandStream
  te: trailers
  content-type: application/grpc
  user-agent: grpc-c++/1.26.0 grpc-c/9.0.0 (android; chttp2; gon)
  grpc-accept-encoding: identity,deflate,gzip
  accept-encoding: identity,gzip
  ```

### 2.2 Protobuf Envelope Structure
The gRPC stream exchanges protobuf-encoded packets. Each packet contains:
- **Field 1 (tag `0x0a`):** `string cmd_name` (e.g., `"CMD_CREATE_USER"`, `"CMD_LOGIN"`)
- **Field 2 (tag `0x10`):** `uint64 seq` (incrementing integer starting at 1)
- **Field 3 (tag `0x1a`):** `bytes/string b64_payload` (Base64 string of packed MsgPack data)
- **Field 4 (tag `0x22`):** `string routing_path` (usually `"/"`)

### 2.3 gRPC Frame Construction (Python implementation)
```python
def build_grpc_payload(cmd_name: str, seq: int, msgpack_dict: dict, field4: str = "/") -> bytes:
    packed_mp = msgpack.packb(msgpack_dict)
    b64_str = base64.b64encode(packed_mp)
    
    pb = bytearray()
    # Field 1: Command Name
    cmd_bytes = cmd_name.encode('utf-8')
    pb.append(0x0a)
    pb.extend(encode_varint(len(cmd_bytes)))
    pb.extend(cmd_bytes)
    
    # Field 2: Sequence Number
    pb.append(0x10)
    pb.extend(encode_varint(seq))
    
    # Field 3: Base64 Payload
    pb.append(0x1a)
    pb.extend(encode_varint(len(b64_str)))
    pb.extend(b64_str)
    
    # Field 4: Routing Path
    if field4:
        f4 = field4.encode('utf-8')
        pb.append(0x22)
        pb.extend(encode_varint(len(f4)))
        pb.extend(f4)
        
    # Wrap in gRPC 5-byte frame: 1-byte compression (0) + 4-byte Big-Endian Length
    return b"\x00" + struct.pack(">I", len(pb)) + bytes(pb)
```

---

## 3. Reverse Engineering & IDA Pro Artifacts

The core game logic resides in **Unreal Engine 4** native library:
- **Binary:** `libUE4.so` (arm64-v8a, ~160 MB)
- **Package:** `jp.konami.pesam`
- **Client Version Captured:** `6.0.1`

### 3.1 Key IDA Pro Addresses & Offsets (Base `0x0`)

| Symbol / Function | Offset in `libUE4.so` | Description |
|---|---|---|
| `SSL_write` | `0x7FFA9B4` | OpenSSL export (wraps `SSL_write_ex`). Used to hook outgoing plaintext TLS packets |
| `SSL_read` | `0x7FFA780` | OpenSSL export (wraps `SSL_read_ex`). Used to hook incoming decrypted TLS packets |
| `CmdCreateUser::Serialize` | `0x75F2B40` | Decompiled serializer function for `CMD_CREATE_USER` payload |
| `CmdGetEulaAreaList::Serialize`| `0x75D8510` | Serializer for EULA area list query |
| `CriHttp::Open` | `0x8B0E4B4` | CRI Middleware HTTP open request |
| `CriHttp::Connect` | `0x8B0E854` | CRI Middleware HTTP connect |
| `CriHttp::SetHeader` | `0x8B0E5C0` | CRI Middleware HTTP set header |
| `CriHttp::SetBody` | `0x8B0E70C` | CRI Middleware HTTP write body |

### 3.2 Function Prologue Verification
Both `SSL_write` and `SSL_read` share the identical 28-byte AArch64 prologue:
```hex
ff c3 00 d1 fd 7b 01 a9 f3 13 00 f9 fd 43 00 91 53 d0 3b d5 68 16 40 f9 e8 07 00 f9
```
*Disassembly:*
```arm64
SUB    SP, SP, #0xF0
STP    X29, X30, [SP,#0x10]
STR    X19, [SP,#0x20]
ADD    X29, SP, #0x10
MRS    X19, TPIDR_EL0
LDR    X8, [X19,#0x28]
STR    X8, [SP,#0xE8]
```

### 3.3 Decompiled `CMD_CREATE_USER` Field Schema (`sub_75F2B40`)
From IDA Pro decompilation, the required fields serialized into MsgPack:
```json
{
  "msgid": "CMD_CREATE_USER",
  "rqid": 1,
  "user_id": 0,
  "session_id": "",
  "my_platform": "ANDROID",
  "s_keyword": "",
  "hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba",
  "client_version": "6.0.1",
  "platform": "ANDROID",
  "device": {
    "device_identifier": "<16-char hex string>"
  },
  "lang": "TH",
  "region": "asia",
  "pcspec": "",
  "user_eula_season": "2025",
  "user_eula_info": {
    "eula_ip_country_a3": "THA",
    "eula_select_country_a3": "THA",
    "eula_ip_state": "",
    "eula_select_state": "",
    "eula_version": {
      "eula": "8.00",
      "eula_privacy_notice": "4.00",
      "eula_myclub": "6.00",
      "eula_japan_version": "8.00",
      "eula_myclub_japan": "7.00"
    },
    "eula_country_age_kind": "BIRTH_YEAR_MONTH",
    "eula_privacy_action": "CONFIRM"
  },
  "pes_user_info": {
    "user_id": 0,
    "user_name": "DjTest001",
    "is_deleted": "NO",
    "platform": "ANDROID",
    "psn_user_id": {"account_id": "", "online_id": "", "is_valid_cache": "NO"},
    "xuid": "",
    "steamid": "",
    "nsw_user_id": {"nsa_id": "", "issuer_id": 0},
    "avatar": {
      "is_already_set": "NO",
      "player_id": 0,
      "special_id": 0,
      "background_id": 0,
      "special_id_background": 0,
      "is_enable_player": "NO",
      "is_enable_background": "NO",
      "material_key_player": "",
      "material_key_background": ""
    },
    "fameIdList": [],
    "user_banner": {"banner_id": {"unique_id": 0}, "is_valid": "NO"}
  },
  "cross_platform_option": "ON",
  "age": "1998-05",
  "os_version": "Android OS 12 / API-32",
  "model_name": "SM-F741N",
  "gpu_name": "Adreno (TM) 640",
  "soc_name": "Qualcomm Snapdragon 888",
  "survey_results": [],
  "user_age_kind": "ADULT",
  "is_rooting": "NO",
  "phy_mem_used_mib": 1024,
  "phy_mem_available_mib": 2048,
  "app_storage_used_mib": 500,
  "app_storage_available_mib": 10000,
  "data_storage_used_mib": 500,
  "data_storage_available_mib": 10000,
  "payment_store_link_send_info": {"country_code": "THA"},
  "kid_challenge_id": ""
}
```

---

## 4. Current State & Root Cause Analysis

### 4.1 Command Status Matrix

| Command | Server Response | Analysis |
|---|---|---|
| `CMD_GET_COUNTRY_LIST` | `NOERR` | Returns list of 171 countries. Proves gRPC + protobuf + MsgPack decoding works end-to-end. |
| `CMD_GET_EULA_AREA_LIST` | `ERR` (`ms_eula_area error`) | Sensitive to `lang` / `nation_code` casing (e.g. `out_of_range`). |
| `CMD_GET_USER_EULA_INFO` | `NOERR` | Returns EULA states (`is_deleted_user: YES`). |
| `CMD_CREATE_USER` | `ERR_DATABASE` | Response: `db error tb_user_base:` or `checkEulaVersion error:`. |
| `CMD_LOGIN` | `ERR_DATABASE` | Response: `db error tb_user update:`. |
| `CMD_SET_USER_EULA_INFO` | `ERR_DATABASE` | Response: `no user error:`. |
| `CMD_GET_MYCLUB_PACK_LIST` | `ERR_DATABASE` | Leaked backend path: `/data/pes22/htlocal/pes22/cmd/myclub/API_CMD_GET_MYCLUB_PACK_LIST.php`. |

### 4.2 Why `ERR_DATABASE` Occurs
The Konami PHP backend rejects mutations with `db error tb_user_base:`.
1. **Dynamic Hash / HMAC:** Field `"hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba"` may be a cryptographic digest calculated from timestamp + payload + internal salt, rather than a static string.
2. **Missing Injected Session Fields:** The base class may inject invisible security headers or context variables into the MsgPack array before transmission.
3. **EULA Version Mismatch:** If `user_eula_season` or the sub-version floats (`"8.00"`) do not strictly match current active backend requirements, server aborts the transaction before committing to MySQL.

---

## 5. Live Capture Workflow: Traffic Monitor & Patched APK

To inspect the 100% exact payload generated by the official game client during an actual user creation and login, use the **Frida Gadget Patched APK** combined with the **Traffic Monitor**.

### 5.1 Architecture of the Interception Pipeline

```
+---------------------------------------------------------------------------+
| Emulator (MuMu Player / ARM64 Realm)                                      |
|                                                                           |
|  [jp.konami.pesam Process]                                                |
|    |                                                                      |
|    +--> libfrida-gadget.so (Listens on 0.0.0.0:27042)                    |
|           |                                                               |
|           +--> agent.js injected                                          |
|                  |--> Resolves libUE4.so base from /proc/self/maps        |
|                  +--> Hooks SSL_write (0x7FFA9B4) & SSL_read (0x7FFA780)  |
|                                                                           |
+---------------------------------------------------------------------------+
                                    |
                                    | adb forward tcp:27042 tcp:27042
                                    v
+---------------------------------------------------------------------------+
| Host PC (Windows)                                                         |
|                                                                           |
|  [Traffic Monitor: monitor\run.py]                                        |
|    |                                                                      |
|    +--> collector.py : Auto-attaches to Frida Gadget on 127.0.0.1:27042   |
|    +--> http2.py     : Reassembles HTTP/2 streams & HPACK tables           |
|    +--> decode.py    : Decodes gRPC frames, Protobuf tags, MsgPack JSON    |
|    +--> server.py    : FastAPI backend on http://127.0.0.1:8770           |
|    +--> web/         : Charles-like Web UI for browsing & exporting flows |
+---------------------------------------------------------------------------+
```

### 5.2 Pre-built Artifacts in Workspace
The following files are fully prepared in `c:\Users\jaras\OneDrive\Desktop\eFootball`:
- `pesam_base.final2.apk` (24.4 MB): Patched base APK containing Frida Gadget & smali hook.
- `libfrida-gadget.so` (25.2 MB): ARM64 Frida Gadget library.
- `gadget.config.json`: Configuration instructing gadget to listen on port 27042.
- `SETUP_SWAP.bat` / `do_swap.sh`: Automated scripts to inject patched APK into MuMu via Root.
- `START_MONITOR.bat`: One-click runner for the Traffic Monitor server & web dashboard.
- `monitor/`: Core Python & JS engine (`agent.js`, `collector.py`, `http2.py`, `decode.py`, `server.py`).

---

## 6. How `pesam_base.final2.apk` Was Patched

If the game updates or you need to recreate the patched APK from a clean Play Store dump, follow this exact engineering recipe.

### 6.1 Why Standard Re-signing / Wrap.sh Fails on MuMu
1. **Native Bridge Issue (`wrap.sh`):** MuMu runs an x86_64 Android host with an ARM64 native translation bridge (`libnb.so`). If `wrap.sh` uses `LD_PRELOAD`, the x86_64 `app_process64` crashes immediately because `libfrida-gadget.so` is ELF AArch64.
2. **Play Asset Delivery (PAD) Signature Lock:** eFootball uses dynamic fast-follow asset packs (`pad_ff_00`..`pad_ff_11`). If the APK is uninstalled and reinstalled with a private development certificate (`testkey`), Google Play reports a certificate mismatch (`onError(-15)`), freezing the game before `libUE4.so` unpacks.
3. **Solution — The Root Swap Technique:**
   - Install the clean game from Google Play Store once so that assets download under the valid Play signature.
   - Inject `System.loadLibrary("frida-gadget")` into the Java entrypoint (`GameActivity.smali`).
   - Use root to hot-swap `/data/app/.../base.apk` with the patched version. The OS package manager retains the cached Play signature, allowing normal asset access while loading Frida Gadget in the ARM64 realm.

### 6.2 Step-by-Step Patching Recipe

#### Step 1: Gadget Configuration (`gadget.config.json`)
```json
{
  "interaction": {
    "type": "listen",
    "address": "0.0.0.0",
    "port": 27042,
    "on_port_conflict": "pick-next",
    "on_load": "resume"
  }
}
```

#### Step 2: Decompile Base APK
```bash
apktool d -f -r pesam_base.apk -o work_raw
```
*(Option `-r` preserves binary XML and compiled resources to avoid apktool string-pool corruptions).*

#### Step 3: Inject Smali Hook in `GameActivity`
Locate: `work_raw/smali/com/epicgames/ue4/GameActivity.smali`  
Find the static constructor `.method static constructor <clinit>()V`.  
Verify `.locals` count is at least 1, then insert the library loader at the very top of `<clinit>`:
```smali
.method static constructor <clinit>()V
    .locals 20

    # === INJECTED FRIDA GADGET LOADER ===
    const-string v0, "frida-gadget"
    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    # ====================================

    const/4 v0, 0x3
    new-array v1, v0, [F
    ...
```

#### Step 4: Add Native Gadget Libraries
Create the architecture directory:
```bash
mkdir -p work_raw/lib/arm64-v8a
```
Copy the binaries into place:
- Copy `libfrida-gadget.so` -> `work_raw/lib/arm64-v8a/libfrida-gadget.so`
- Copy `gadget.config.json` -> `work_raw/lib/arm64-v8a/libfrida-gadget.config.so`

#### Step 5: Fix Manifest Malformation
If `apktool` converts null attributes to `@null` in `AndroidManifest.xml`:
Replace:
```xml
android:name="com.google.firebase.messaging.default_notification_icon" android:resource="@null"
```
With:
```xml
android:name="com.google.firebase.messaging.default_notification_icon" android:value=""
```

#### Step 6: Rebuild, Align, and Sign
```bash
# 1. Rebuild with aapt2
apktool b work_raw -o pesam_base.built.apk

# 2. Zipalign (4-byte boundary)
zipalign -p -f 4 pesam_base.built.apk pesam_base.aligned2.apk

# 3. Sign with release or self-signed key
apksigner sign --ks efb.keystore --ks-pass pass:android --key-pass pass:android --out pesam_base.final2.apk pesam_base.aligned2.apk
```

---

## 7. Execution Guide: Step-by-Step Interception

Follow these exact steps to capture live eFootball traffic:

### Step 1: Ensure Emulator is Ready
1. Launch **MuMu Player** (ensure Root permission is toggled **ON** in emulator settings).
2. Confirm ADB detects the device:
   ```powershell
   adb connect 127.0.0.1:5557
   adb devices
   ```

### Step 2: Inject Patched APK & Gadget
Run the automated swap batch script:
```powershell
cd c:\Users\jaras\OneDrive\Desktop\eFootball
.\SETUP_SWAP.bat
```
*What this does under the hood:*
1. Pushes `pesam_base.final2.apk`, `libfrida-gadget.so`, and `libfrida-gadget.config.so` to `/data/local/tmp/`.
2. Locates `pm path jp.konami.pesam`.
3. Overwrites `/data/app/.../base.apk` with `pesam_base.final2.apk` (`chmod 644`).
4. Copies `libfrida-gadget.so` to the app's `lib/arm64/` directory (`chmod 755`).
5. Clears dalvik/oat cache to force bytecode recompilation.

### Step 3: Launch Traffic Monitor
Run the launcher:
```powershell
.\START_MONITOR.bat
```
- Starts local server at `http://127.0.0.1:8770`.
- Establishes `adb forward tcp:27042 tcp:27042`.
- Starts the background watcher loop: as soon as `jp.konami.pesam` starts, it connects to port 27042, injects `agent.js`, and hooks `SSL_write` / `SSL_read`.

### Step 4: Open Game & Trigger Events
1. Open eFootball on the emulator.
2. In `START_MONITOR.bat` console, you will observe:
   ```
   [efb] SSL_write@0x798b... prologue_ok=true  SSL_read@0x798b... prologue_ok=true
   [efb] hooked OK
   ```
3. In browser at `http://127.0.0.1:8770`, watch requests stream in real time.
4. If resetting an existing account to test Guest Creation, run:
   ```powershell
   .\RESET_GUEST_ID.bat
   ```
   This clears `/data/user/0/jp.konami.pesam/files/SaveData/AUTH/*` while preserving the 2GB+ downloaded game resources.
5. Tap Title Screen -> Accept Terms -> Enter Nickname.
6. The Traffic Monitor will capture:
   - `CMD_GET_EULA_AREA_LIST` (Request + Response)
   - `CMD_GET_COUNTRY_LIST` (Request + Response)
   - `CMD_CREATE_USER` (Exact working payload + server hash!)
   - `CMD_LOGIN` (Session tokens + encryption keys)

### Step 5: Exporting Flows
- Click **Export JSON** on the Web UI to dump captured packets to `monitor/exports/` or `exported_flows.json`.

---

## 8. Roadmap & Next Actions for the AI Agent

When picking up this task, execute in the following priority order:

1. **Compare Live vs Python Payload:**
   - Take the captured `CMD_CREATE_USER` payload from `exported_flows.json`.
   - Diff every single field and data type against `scratch/test_create_fresh.py`.
   - Check if values like `phy_mem_used_mib`, `user_eula_season`, or `hash` contain dynamic checksums.
2. **Analyze Hash Derivation (if hash differs):**
   - Look at `libUE4.so` around `0x75F2B40` (or offset `488` in the request struct) in IDA Pro.
   - Determine how `hash` is calculated (is it MD5/SHA256 of specific fields + static salt?).
3. **Finalize `pure_api_reroll.py`:**
   - Integrate the validated `CMD_CREATE_USER` payload.
   - Implement the subsequent chain:
     ```
     CMD_CREATE_USER -> CMD_LOGIN -> CMD_GET_MYCLUB_PACK_LIST -> CMD_EXEC_LOTTERY
     ```
   - Build account output file persistence: save `user_id`, `auth_code`, and high-value player pull records to `accounts/<timestamp>_<user_id>.json`.
