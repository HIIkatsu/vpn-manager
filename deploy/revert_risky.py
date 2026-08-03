import paramiko

NODES = [
    {'name': 'Helsinki', 'ip': '150.251.152.174', 'user': 'root', 'pass': 'Mn52YqRYF88Aa'},
    {'name': 'Moscow', 'ip': '132.243.230.173', 'user': 'root', 'pass': 'gwYCjg3WSTvNj'}
]

for node in NODES:
    print(f"Reverting risky settings on {node['name']}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(node['ip'], username=node['user'], password=node['pass'], timeout=5)
    
    # Revert sysctl
    ssh.exec_command('sed -i "s/rp_filter = 1/rp_filter = 2/g" /etc/sysctl.conf')
    ssh.exec_command('sed -i "s/tcp_tw_reuse = 1/tcp_tw_reuse = 0/g" /etc/sysctl.conf')
    ssh.exec_command('sysctl -p')
    
    # Revert Xray sniffing and handshake
    script = """
import json

with open('/usr/local/etc/xray/config.json', 'r') as f:
    config = json.load(f)

for ib in config.get('inbounds', []):
    if 'sniffing' in ib and 'destOverride' in ib['sniffing']:
        del ib['sniffing']['destOverride']
        if not ib['sniffing']:
            del ib['sniffing']
            
    if 'settings' in ib and 'handshake' in ib['settings']:
        del ib['settings']['handshake']
        
with open('/usr/local/etc/xray/config.json', 'w') as f:
    json.dump(config, f, indent=2)
"""
    ssh.exec_command(f"python3 -c \"{script}\"")
    ssh.exec_command('systemctl restart xray')
    ssh.close()
    print('Done.')
