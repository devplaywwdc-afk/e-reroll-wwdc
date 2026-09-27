import subprocess

subprocess.run(['adb', 'connect', '127.0.0.1:5557'])
cmd = "grep -U -b -o -a -E '.{0,10}eula_version.{0,60}' /proc/4920/mem 2>/dev/null | head -n 20"
r = subprocess.run(['adb', '-s', '127.0.0.1:5557', 'shell', cmd], capture_output=True, text=True, errors='ignore')
print("MEM MATCHES:")
print(r.stdout)
print("ERR:", r.stderr)
