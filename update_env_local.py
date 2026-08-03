import subprocess
import base64

script = """
with open('/root/vpn-manager-v2/.env', 'r') as f:
    lines = f.readlines()
with open('/root/vpn-manager-v2/.env', 'w') as f:
    for line in lines:
        if line.startswith('SYNC_PUSH_NODES='):
            f.write('SYNC_PUSH_NODES=\\'[{"name": "Russia", "url": "http://132.243.230.173:8090/internal/xray/client"}]\\'\\n')
        else:
            f.write(line)
print('Successfully updated SYNC_PUSH_NODES')
"""

b64 = base64.b64encode(script.encode('utf-8')).decode('ascii')
cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64}'))\""

result = subprocess.run(["python", "ssh_run.py", cmd], capture_output=True, text=True)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
