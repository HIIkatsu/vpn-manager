import sqlite3
import subprocess
from datetime import datetime

DB_PATH = "/root/vpn-manager/config/database.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Ищем юзеров, которые активны, но у которыхexpires_at уже в прошлом
    cursor.execute("""
        SELECT uuid FROM users 
        WHERE enabled = 1 
          AND expires_at IS NOT NULL 
          AND expires_at < ?
    """, (now_str,))
    
    expired_users = cursor.fetchall()
    
    if expired_users:
        print(f"[{now_str}] Найдено истекших подписок: {len(expired_users)}")
        for (user_uuid,) in expired_users:
            cursor.execute("UPDATE users SET enabled = 0 WHERE uuid = ?", (user_uuid,))
            print(f"[-] Отключен юзер: {user_uuid}")
        
        conn.commit()
        
        # Синхронизируем базу с конфигами xray через твой менеджер
        subprocess.run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"])
    
    conn.close()

if __name__ == "__main__":
    main()
