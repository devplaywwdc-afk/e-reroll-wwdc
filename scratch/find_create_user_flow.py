import json
data = json.load(open('all_flows_clean.json','r',encoding='utf-8'))
for f in data:
    rpc = f.get('rpc','')
    if 'CREATE_USER' in rpc or 'LOGIN' in rpc or 'EULA' in rpc:
        fid = f.get("id","?")
        status = f.get("status","?")
        print(f"ID={fid} rpc={rpc} status={status}")
        req_body = f.get('request',{}).get('body','')
        if req_body:
            s = json.dumps(req_body, ensure_ascii=False, default=str)[:1500]
            print(f"  REQ: {s}")
        resp_body = f.get('response',{}).get('body','')
        if resp_body:
            s = json.dumps(resp_body, ensure_ascii=False, default=str)[:500]
            print(f"  RESP: {s}")
        print()
