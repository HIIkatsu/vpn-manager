import re

with open('app/services/xray_manager.py', 'r') as f:
    code = f.read()

new_func = """def _build_vless_account_message(uuid: str, flow: str = 'xtls-rprx-vision', encryption: str = 'none') -> bytes:
    from uuid import UUID
    normalized_uuid = str(UUID(uuid)) if len(uuid) == 32 else uuid
    
    payload = b''
    uuid_bytes = normalized_uuid.encode('utf-8')
    payload += b'\\x0A' + bytes([len(uuid_bytes)]) + uuid_bytes
    
    flow_bytes = flow.encode('utf-8')
    payload += b'\\x12' + bytes([len(flow_bytes)]) + flow_bytes
    
    enc_bytes = encryption.encode('utf-8')
    payload += b'\\x1A' + bytes([len(enc_bytes)]) + enc_bytes
    
    return payload

class XrayManager:"""

code = re.sub(r'def _build_vless_account_message.*?class XrayManager:', new_func, code, flags=re.DOTALL)

with open('app/services/xray_manager.py', 'w') as f:
    f.write(code)
