import paramiko
import sys

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)

    sftp = ssh.open_sftp()
    remote_path = '/root/vpn-manager-v2/app/api/routers/subscription_router.py'
    
    with sftp.file(remote_path, 'r') as f:
        content = f.read().decode('utf-8')
    
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        if 'Германия 1' in line or 'Германия 2' in line or 'Нидерланды 1' in line or 'Нидерланды 2' in line:
            continue
        new_lines.append(line)
        
    with sftp.file(remote_path, 'w') as f:
        f.write('\n'.join(new_lines).encode('utf-8'))

    sftp.close()
    
    # Reload the service
    stdin, stdout, stderr = ssh.exec_command('systemctl restart vpn-manager-v2')
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    print("Out:", out)
    print("Err:", err)
    
    ssh.close()
    print("Nodes removed successfully.")

if __name__ == "__main__":
    main()
