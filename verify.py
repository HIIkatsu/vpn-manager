import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)

sftp = ssh.open_sftp()
with sftp.file('/root/vpn-manager-v2/app/api/routers/subscription_router.py', 'r') as f:
    content = f.read().decode('utf-8')
    
for line in content.split('\n'):
    if 'Германия' in line or 'Нидерланды' in line:
        print(line.strip())
