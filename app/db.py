import sqlite3
import os

DB_PATH = '/root/vpn-manager/config/database.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    # Включаем WAL-режим. Без него конкурентная запись от бота и веб-панели будет ловить блокировки
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        # 1. Таблица пользователей
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                uuid TEXT UNIQUE NOT NULL,
                token TEXT UNIQUE NOT NULL, -- Токен для ссылки на подписку /sub/<token>
                enabled INTEGER DEFAULT 1,  -- 0 = выключен, 1 = активен
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,       -- NULL = вечный, или дата окончания
                traffic_limit_gb INTEGER DEFAULT 0, -- 0 = безлимит
                comment TEXT
            )
        ''')

        # 2. Таблица сессий (для админки и юзер-панели)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,        -- Сюда пишем UUID сессии (cookie token)
                username TEXT NOT NULL,
                role TEXT NOT NULL,         -- admin / user
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 3. Таблица исторической статистики трафика (чтобы не парсить логи в реальном времени)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS traffic_stats (
                username TEXT PRIMARY KEY,
                u_bytes INTEGER DEFAULT 0,  -- Upload
                d_bytes INTEGER DEFAULT 0,  -- Download
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 4. Системные настройки (сюда зашьем прокси, ключи REALITY, домены воркеров)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        
        conn.commit()

if __name__ == '__main__':
    init_db()
    print("✅ База данных SQLite успешно инициализирована в режиме WAL.")
