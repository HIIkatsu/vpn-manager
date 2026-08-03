import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)
stdin, stdout, stderr = ssh.exec_command('journalctl -u xray --no-pager | tail -n 50')
print(stdout.read().decode('utf-8'))
ssh.close()
