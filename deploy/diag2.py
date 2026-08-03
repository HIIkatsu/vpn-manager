import paramiko
import sys

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)
    except Exception as e:
        print("Failed to connect to Helsinki:", e)
        return

    commands = {
        "Nginx Status": "systemctl status nginx --no-pager",
        "Nginx Logs": "journalctl -u nginx --no-pager | tail -n 20",
        "Xray Status": "systemctl status xray --no-pager",
        "Xray Logs": "journalctl -u xray --no-pager | tail -n 20",
        "Iptables NAT": "iptables -t nat -L -n -v",
        "Iptables Filter": "iptables -L -n -v",
        "Dropped Packets": "grep 'NFT-INPUT-DROP' /var/log/syslog | tail -n 20"
    }
    
    for name, cmd in commands.items():
        print(f"\n--- {name} ---")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode('utf-8')
        err = stderr.read().decode('utf-8')
        if out: print(out.encode('ascii', 'replace').decode('ascii').strip())
        if err: print(err.encode('ascii', 'replace').decode('ascii').strip())

    ssh.close()

if __name__ == "__main__":
    main()
