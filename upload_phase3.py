import paramiko
import os

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)

    sftp = ssh.open_sftp()
    
    # Upload router
    local_router = 'c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py'
    remote_router = '/root/vpn-manager-v2/app/api/routers/subscription_router.py'
    sftp.put(local_router, remote_router)
    
    # Upload template
    local_html = 'c:/Users/HIIkatsu/Desktop/vpn/bootstrap.html'
    remote_html = '/root/vpn-manager-v2/app/templates/bootstrap.html'
    sftp.put(local_html, remote_html)
    
    sftp.close()
    
    # Restart the api service
    stdin, stdout, stderr = ssh.exec_command('systemctl restart vpn-api.service')
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    print("Restart Out:", out)
    print("Restart Err:", err)
    
    ssh.close()
    print("Upload complete!")

if __name__ == "__main__":
    main()
