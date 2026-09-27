#!/usr/bin/env python3
"""
capture_create_user.py - Capture real CMD_CREATE_USER payload from the game.
Hooks SSL traffic via Frida, force-stops game, clears auth, relaunches,
then captures the CMD_CREATE_USER payload the real client sends.
"""
import sys, os, json, time, base64, struct, subprocess, threading
try:
    import frida
except ImportError:
    print("pip install frida frida-tools"); sys.exit(1)
try:
    import msgpack
except ImportError:
    print("pip install msgpack"); sys.exit(1)

ADB_SERIAL = "127.0.0.1:5557"
PKG = "jp.konami.pesam"
SAVE_ROOT = f"/data/user/0/{PKG}/files/SaveData"
SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "hook_grpc_traffic.js")

captured_cmds = []
done_event = threading.Event()

def adb(*args, timeout=30):
    r = subprocess.run(["adb", "-s", ADB_SERIAL] + list(args),
                       capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout.strip()

def sh(cmd, timeout=30):
    return adb("shell", cmd, timeout=timeout)

def root(cmd):
    return sh(f'su -c "{cmd}"')

def decode_grpc_hex(hex_str):
    """Parse gRPC frame from hex and extract msgpack payload."""
    data = bytes.fromhex(hex_str)
    if len(data) < 5:
        return None
    msg_len = struct.unpack(">I", data[1:5])[0]
    payload = data[5:5+msg_len]
    
    idx = 0
    cmd_name = ""
    b64_data = b""
    seq = 0
    field4 = ""
    
    while idx < len(payload):
        tag = payload[idx]; idx += 1
        fn = tag >> 3; wt = tag & 0x07
        if wt == 0:  # varint
            val = 0; s = 0
            while idx < len(payload):
                b = payload[idx]; idx += 1
                val |= (b & 0x7f) << s
                if not (b & 0x80): break
                s += 7
            if fn == 2: seq = val
        elif wt == 2:  # length-delimited
            l = 0; s = 0
            while idx < len(payload):
                b = payload[idx]; idx += 1
                l |= (b & 0x7f) << s
                if not (b & 0x80): break
                s += 7
            sub = payload[idx:idx+l]; idx += l
            if fn == 1: cmd_name = sub.decode('utf-8', 'replace')
            elif fn == 3: b64_data = sub
            elif fn == 4: field4 = sub.decode('utf-8', 'replace')
    
    result = {"cmd": cmd_name, "seq": seq, "field4": field4}
    if b64_data:
        try:
            mp_bytes = base64.b64decode(b64_data)
            result["data"] = msgpack.unpackb(mp_bytes, raw=False)
        except Exception as e:
            result["b64_raw"] = b64_data.decode('utf-8', 'replace')
            result["decode_error"] = str(e)
    return result

def on_message(msg, data):
    if msg["type"] == "send":
        payload = msg["payload"]
        ptype = payload.get("type", "")
        
        if ptype == "ready":
            print(f"[*] {payload['msg']}")
            return
        
        cmd = payload.get("cmd", "?")
        direction = ">>>" if ptype == "grpc_send" else "<<<"
        print(f"[{direction}] {cmd} ({payload.get('len', 0)} bytes)")
        
        decoded = decode_grpc_hex(payload["hex"])
        if decoded:
            captured_cmds.append({
                "direction": "send" if ptype == "grpc_send" else "recv",
                "cmd": cmd,
                "decoded": decoded
            })
            
            # Save each captured command immediately
            out_name = f"captured_{cmd.lower()}_{ptype.split('_')[-1]}.json"
            out_path = os.path.join(os.path.dirname(__file__), out_name)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(decoded, f, indent=2, ensure_ascii=False, default=str)
            print(f"    -> saved {out_name}")
            
            # If we got a CREATE_USER send, flag it
            if cmd == "CMD_CREATE_USER" and ptype == "grpc_send":
                print("\n[!!!] CAPTURED CMD_CREATE_USER PAYLOAD!")
                print(json.dumps(decoded.get("data", {}), indent=2, ensure_ascii=False, default=str)[:3000])
                done_event.set()
    elif msg["type"] == "error":
        print(f"[ERROR] {msg.get('description', msg)}")

def main():
    print("="*60)
    print("  eFootball CMD_CREATE_USER Traffic Capture")
    print("="*60)
    
    # Step 1: Force-stop game and clear auth
    print("\n[1] Force-stop game...")
    sh(f"am force-stop {PKG}")
    time.sleep(2)
    
    print("[2] Clearing auth data...")
    root(f"rm -f {SAVE_ROOT}/AUTH/*")
    root(f"rm -f {SAVE_ROOT}/SYSTEM/*")
    print("    Auth cleared.")
    
    # Step 2: Launch game
    print("[3] Launching game...")
    sh(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1")
    time.sleep(3)
    
    # Step 3: Attach Frida
    print("[4] Attaching Frida...")
    try:
        device = frida.get_device_manager().add_remote_device(ADB_SERIAL.split(":")[0] + ":27042")
    except:
        # Try USB device
        device = frida.get_usb_device(timeout=5)
    
    # Wait for game process
    pid = None
    for attempt in range(30):
        try:
            for proc in device.enumerate_processes():
                if PKG in proc.name:
                    pid = proc.pid
                    break
            if pid:
                break
        except:
            pass
        time.sleep(1)
    
    if not pid:
        print("[!] Game process not found!")
        sys.exit(1)
    
    print(f"    Found PID: {pid}")
    session = device.attach(pid)
    
    with open(SCRIPT_PATH, "r") as f:
        script_source = f.read()
    
    script = session.create_script(script_source)
    script.on("message", on_message)
    script.load()
    
    print("\n[*] Hooks active. Waiting for CMD_CREATE_USER...")
    print("[*] You may need to go through the onboarding flow in the game.")
    print("[*] Press Ctrl+C to stop.\n")
    
    # Wait for capture or timeout
    try:
        done_event.wait(timeout=300)  # 5 min timeout
    except KeyboardInterrupt:
        print("\n[*] Interrupted by user.")
    
    # Save all captured commands
    all_path = os.path.join(os.path.dirname(__file__), "captured_all_cmds.json")
    with open(all_path, "w", encoding="utf-8") as f:
        json.dump(captured_cmds, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[*] Saved {len(captured_cmds)} captured commands to {all_path}")
    
    session.detach()

if __name__ == "__main__":
    main()
