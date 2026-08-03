import paramiko
import sys

NODES = [
    {"name": "Helsinki", "ip": "150.251.152.174", "user": "root", "pass": "Mn52YqRYF88Aa"},
    {"name": "Moscow", "ip": "132.243.230.173", "user": "root", "pass": "gwYCjg3WSTvNj"}
]

def check():
    for node in NODES:
        print(f"\n=== Diagnostics for {node['name']} ===")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(node['ip'], username=node['user'], password=node['pass'], timeout=5)
            
            print("[*] Xray status:")
            stdin, stdout, stderr = ssh.exec_command("systemctl status xray --no-pager | grep Active")
            print(stdout.read().decode('utf-8').strip())
            
            print("[*] Xray recent errors (last 15 lines):")
            stdin, stdout, stderr = ssh.exec_command("journalctl -u xray -n 15 --no-pager")
            print(stdout.read().decode('utf-8').strip())

            print("[*] Nftables dropped packets on VPN ports:")
            stdin, stdout, stderr = ssh.exec_command("nft list ruleset | grep -i drop | grep -v 'policy drop'")
            print(stdout.read().decode('utf-8').strip())
            
            print("[*] Docker containers (Helsinki only):")
            if node['name'] == 'Helsinki':
                stdin, stdout, stderr = ssh.exec_command("docker ps --format '{{.Names}} - {{.Status}}'")
                print(stdout.read().decode('utf-8').strip())

            ssh.close()
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    check()
