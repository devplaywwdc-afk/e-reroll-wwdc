import subprocess

subprocess.run(['adb', 'connect', '127.0.0.1:5557'])
r = subprocess.run(['adb', '-s', '127.0.0.1:5557', 'shell', 'cat /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat'], capture_output=True, text=True)
print("AUTH CONTENT:")
print(r.stdout)
print("ERR:", r.stderr)
