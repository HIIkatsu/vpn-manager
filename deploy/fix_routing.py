import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)

print('Applying Nginx fix...')
ssh.exec_command('sed -i "s/127.0.0.1:10446/127.0.0.1:40443/g" /etc/nginx/nginx.conf')
ssh.exec_command('sed -i "s/127.0.0.1:8446/127.0.0.1:20443/g" /etc/nginx/nginx.conf')
ssh.exec_command('systemctl reload nginx')

print('Applying Iptables REDIRECT fix...')
ssh.exec_command('iptables -t nat -A PREROUTING -p tcp -m tcp --dport 20443 -j REDIRECT --to-ports 443')
ssh.exec_command('iptables -t nat -A PREROUTING -p udp -m udp --dport 20443 -j REDIRECT --to-ports 443')
ssh.exec_command('iptables-save > /etc/iptables/rules.v4')

print('Done.')
ssh.close()
