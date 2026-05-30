import asyncio
import json
import os
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.db.database import async_session_maker
from app.db.models import User
from app.services.xray_manager import XrayManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
STATE_FILE = "/root/vpn-manager-v2/cluster_state.json"

NODES = [
    {"name": "Финляндия (Local)", "ip": "127.0.0.1", "tags": ["vless-smart", "vless-ru-clean", "vless-ru-whitelist", "vless-uk-ipv6"]},
    {"name": "Германия", "ip": "132.243.194.119", "tags": ["vless-smart", "vless-ru-clean", "vless-ru-whitelist", "vless-uk-ipv6"]},
    {"name": "Нидерланды", "ip": "194.50.94.177", "tags": ["vless-smart", "vless-ru-clean", "vless-ru-whitelist", "vless-uk-ipv6"]},
    {"name": "Транзит (РФ)", "ip": "132.243.230.173", "tags": ["vless-smart-transit", "vless-ru-clean", "vless-ru-whitelist", "vless-uk-ipv6"]}
]

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else []

# Глушим локальный парсер конфига, чтобы тэги не сбились
@classmethod
async def mock_init(cls): pass
XrayManager._init_config = mock_init

async def sync_cluster():
    logging.info("Cluster gRPC Sync Started.")
    while True:
        try:
            async with async_session_maker() as session:
                users = (await session.execute(select(User))).scalars().all()
            
            db_active = {str(u.telegram_id): str(u.vless_uuid) for u in users if u.is_active}
            db_active["transit_node_ru"] = "11111111-1111-1111-1111-111111111111"
            state_active = load_state()

            to_add = {e: u for e, u in db_active.items() if e not in state_active}
            to_remove = [e for e in state_active if e not in db_active]

            if to_add or to_remove:
                logging.info(f"Updates -> Add: {list(to_add.keys())}, Remove: {to_remove}")
                for node in NODES:
                    XrayManager._target = f"{node['ip']}:10085"
                    XrayManager._channel = None 
                    mgr = XrayManager()
                    mgr._inbound_tags = node["tags"]

                    for email, uuid in to_add.items():
                        await mgr.add_client(email, uuid)
                    for email in to_remove:
                        await mgr.remove_client(email)
                
                with open(STATE_FILE, "w") as f:
                    json.dump(list(db_active.keys()), f)
                logging.info("RAM Injection Complete.")
        except Exception as e:
            logging.error(f"Sync error: {e}")
        
        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(sync_cluster())
