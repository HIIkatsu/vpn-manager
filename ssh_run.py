import paramiko
import sys

def main():
    if len(sys.argv) < 2:
        print("Usage: python ssh_run.py <command>")
        return
    
    # Reconfigure stdout to utf-8
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    
    cmd = " ".join(sys.argv[1:])
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect('150.251.152.174', username='root', password='Mn52YqRYF88Aa', timeout=5)
    except Exception as e:
        print("Failed to connect to Helsinki:", e)
        return

    stdin, stdout, stderr = ssh.exec_command(cmd)
    
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    if out: print(out)
    if err: print(err, file=sys.stderr)

    ssh.close()

if __name__ == "__main__":
    main()
