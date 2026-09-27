import urllib.request
import urllib.parse
import json

url = "http://ntl.service.konami.net/ntl/api/GateInfo.php"
params = {
    "app": "PES2022",
    "region": "AS",
    "version": "9.1.1",
    "platform": "ANDROID"
}

# Test GET
try:
    req_url = url + "?" + urllib.parse.urlencode(params)
    print("Testing GET:", req_url)
    req = urllib.request.Request(req_url, headers={"User-Agent": "Dalvik/2.1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        print("GET Status:", r.status)
        data = r.read()
        print("GET Response:", data[:500])
except Exception as e:
    print("GET Error:", e)

# Test POST form
try:
    data_enc = urllib.parse.urlencode(params).encode('utf-8')
    print("Testing POST form...")
    req = urllib.request.Request(url, data=data_enc, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Dalvik/2.1.0"
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        print("POST Status:", r.status)
        data = r.read()
        print("POST Response:", data[:500])
except Exception as e:
    print("POST Error:", e)
