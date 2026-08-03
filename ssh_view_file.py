import paramiko
import sys

def main():
    if len(sys.argv) < 2:
        print("Usage: python ssh_view_file.py <remote_path>")
        return
    
    # Reconfigure stdout to utf-8
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    
    remote_path = sys.argv[1]
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)
    except Exception as e:
        print("Failed to connect:", e)
        return

    sftp = ssh.open_sftp()
    try:
        with sftp.file(remote_path, 'r') as f:
            print(f.read().decode('utf-8'))
    except Exception as e:
        print(f"Error reading file {remote_path}: {e}")
    finally:
        sftp.close()
        ssh.close()

if __name__ == "__main__":
    main()
