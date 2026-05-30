import asyncio
from datetime import datetime
from sqlalchemy import update
from app.db.database import async_session_maker
from app.db.models import User

# Точные данные, вытащенные из vpn_db_backup_before_v2.sql
dates_map = {
    1632881013: "2026-06-19T19:23:18.308024+00:00",
    840649258: "2026-06-01T00:00:00+00:00",
    1078937205: "2026-06-19T20:45:28.226556+00:00",
    1778580693: "2026-06-20T10:37:35.509413+00:00",
    1780466329: "2026-06-20T11:09:45.135216+00:00",
    1445959170: "2026-09-17T13:46:55.465655+00:00",
    1480942640: "2027-05-23T16:36:18.545440+00:00"
}

async def restore_dates():
    async with async_session_maker() as session:
        for tg_id, date_str in dates_map.items():
            dt = datetime.fromisoformat(date_str)
            await session.execute(update(User).where(User.telegram_id == tg_id).values(sub_end_date=dt))
        await session.commit()
        print("✅ Даты успешно восстановлены из SQL-дампа.")

if __name__ == "__main__":
    asyncio.run(restore_dates())
