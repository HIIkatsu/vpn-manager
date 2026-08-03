import subprocess
import base64

script = """
with open('/root/vpn-manager-v2/app/runtime/workers.py', 'r') as f:
    c = f.read()
c = c.replace('async def expiry_loop(interval_seconds: int = 900) -> None:', 'async def expiry_loop(interval_seconds: int = 60) -> None:')
with open('/root/vpn-manager-v2/app/runtime/workers.py', 'w') as f:
    f.write(c)
print('Updated workers.py on remote server')
"""

b64 = base64.b64encode(script.encode('utf-8')).decode('ascii')
cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64}'))\""

result = subprocess.run(["python", "ssh_run.py", cmd], capture_output=True, text=True)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
