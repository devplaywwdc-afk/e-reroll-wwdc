import subprocess

subprocess.run(['adb', 'connect', '127.0.0.1:5557'])
r = subprocess.run(['adb', '-s', '127.0.0.1:5557', 'shell', 'find /data/user/0/jp.konami.pesam/files/SaveData/ -type f'], capture_output=True, text=True)
print("FILES:")
for line in r.stdout.splitlines():
    print(" ", line)
