import os
import sys
import subprocess

# Force stdout to utf-8 to handle emojis
sys.stdout.reconfigure(encoding='utf-8')

try:
    import paramiko
except ImportError:
    print("Installing paramiko...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
    import paramiko

NODES = [
    {
        "name": "Helsinki",
        "ip": "150.251.152.174",
        "user": "root",
        "pass": "Mn52YqRYF88Aa",
        "scripts": [
            "layer1_anti_spam.sh",
            "layer2_firewall_helsinki.sh",
            "layer3_xray_hardening.sh",
            "layer4_os_hardening.sh",
            "layer5_monitoring.sh",
            "verify_hardening.sh"
        ]
    },
    {
        "name": "Moscow",
        "ip": "132.243.230.173",
        "user": "root",
        "pass": "gwYCjg3WSTvNj",
        "scripts": [
            "layer1_anti_spam.sh",
            "layer2_firewall_moscow.sh",
            "layer3_xray_hardening.sh",
            "layer4_os_hardening.sh",
            "layer5_monitoring.sh",
            "verify_hardening.sh"
        ]
    }
]

def deploy():
    for node in NODES:
        print(f"\n========== DEPLOYING TO {node['name']} ({node['ip']}) ==========")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            print(f"[*] Connecting to {node['name']}...")
            ssh.connect(node['ip'], username=node['user'], password=node['pass'], timeout=10)
            sftp = ssh.open_sftp()
            
            for script in node['scripts']:
                local_path = os.path.join(os.path.dirname(__file__), script)
                remote_path = f"/root/{script}"
                
                print(f"\n[*] Uploading {script}...")
                sftp.put(local_path, remote_path)
                ssh.exec_command(f"chmod +x {remote_path}")
                
                print(f"[*] Executing {script}...")
                stdin, stdout, stderr = ssh.exec_command(f"{remote_path}")
                exit_status = stdout.channel.recv_exit_status()
                
                out = stdout.read().decode('utf-8', errors='replace')
                err = stderr.read().decode('utf-8', errors='replace')
                
                # Safely print to avoid Windows charmap errors
                if out:
                    print(out.encode('ascii', 'replace').decode('ascii').strip('\n'))
                if err:
                    print(err.encode('ascii', 'replace').decode('ascii').strip('\n'))
                
                if exit_status != 0:
                    print(f"[!] Script {script} failed with exit code {exit_status}")
                else:
                    print(f"[+] Script {script} executed successfully.")
            
            sftp.close()
        except Exception as e:
            print(f"Failed to connect or deploy to {node['name']}: {e}")
        finally:
            ssh.close()

if __name__ == "__main__":
    deploy()
