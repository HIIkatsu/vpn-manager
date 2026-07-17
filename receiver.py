import json, os
from fastapi import FastAPI, Header, HTTPException, Request
import uvicorn

app = FastAPI()

@app.post("/internal/xray/client")
async def update_client(request: Request, authorization: str = Header(None)):
    if authorization != "Bearer vpn_sync_super_secret_token_2026":
        raise HTTPException(status_code=403)
    data = await request.json()
    with open('/usr/local/etc/xray/config.json', 'r') as f:
        c = json.load(f)
    for i in c.get('inbounds', []):
        if i.get('tag') == 'vless-smart':
            clients = [cl for cl in i['settings']['clients'] if cl['id'] != data.get('uuid')]
            if data.get('action') == 'add':
                clients.append({"id": data['uuid'], "email": data.get('telegram_id', 'user'), "flow": "xtls-rprx-vision"})
            i['settings']['clients'] = clients
    with open('/usr/local/etc/xray/config.json', 'w') as f:
        json.dump(c, f, indent=2)
    os.system("systemctl restart xray")
    return {"detail": "xray mutation success"}

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8090)