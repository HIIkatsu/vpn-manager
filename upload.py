import paramiko
import sys

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)
    except Exception as e:
        print("Failed to connect:", e)
        return

    sftp = ssh.open_sftp()
    sftp.put('subscription_router.py', '/root/vpn-manager-v2/app/api/routers/subscription_router.py')
    sftp.close()
    ssh.close()
    print("Upload complete")

if __name__ == "__main__":
    main()
