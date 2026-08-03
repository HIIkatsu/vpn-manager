import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# Add the project root to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import select
from app.db.session import async_session_maker
from app.db.models import User
from app.services.node_sync import ActivePushDispatcher
from app.core.settings import settings
from aiogram import Bot

async def check_expiry():
    dispatcher = ActivePushDispatcher()
    bot = Bot(token=settings.BOT_TOKEN)
    
    async with async_session_maker() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        
        now = datetime.now(timezone.utc)
        
        for user in users:
            if not user.sub_end_date:
                continue
                
            # Make sure sub_end_date is aware
            if user.sub_end_date.tzinfo is None:
                user.sub_end_date = user.sub_end_date.replace(tzinfo=timezone.utc)
                
            time_left = user.sub_end_date - now
            days_left = time_left.days
            
            # 1. Notify 3 days before expiry
            if 2 <= days_left <= 3 and user.is_active:
                # We need a way to track if we already sent the notification to avoid spamming every hour.
                # Since we don't have a specific column for that, we could check if time_left is exactly between 3.0 and 2.95 days.
                # Better: check if time_left is between 71 and 72 hours
                hours_left = time_left.total_seconds() / 3600
                if 71 <= hours_left < 72:
                    try:
                        await bot.send_message(
                            chat_id=user.telegram_id, 
                            text="⏰ <b>Напоминание:</b> Ваша подписка на AnKo VPN закончится через 3 дня!\nПожалуйста, продлите подписку в меню бота.",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        print(f"Failed to notify user {user.telegram_id}: {e}")
            
            # 2. Notify on the day of expiry (last 24 hours)
            elif 0 <= days_left == 0 and user.is_active:
                hours_left = time_left.total_seconds() / 3600
                if 23 <= hours_left < 24:
                    try:
                        await bot.send_message(
                            chat_id=user.telegram_id, 
                            text="⚠️ <b>Внимание:</b> Ваша подписка на AnKo VPN закончится сегодня!\nПродлите подписку, чтобы не потерять доступ к интернету.",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        print(f"Failed to notify user {user.telegram_id}: {e}")
            
            # 3. Disconnect when expired
            elif time_left.total_seconds() < 0 and user.is_active:
                print(f"User {user.telegram_id} expired. Disconnecting.")
                user.is_active = False
                # Remove from Xray
                success, error = await dispatcher.remove_client(telegram_id=user.telegram_id, event_id=f"expire:{user.id}")
                if error:
                    print(f"Failed to remove user {user.telegram_id} from Xray: {error}")
                
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id, 
                        text="❌ <b>Ваша подписка закончилась.</b>\nДоступ к VPN приостановлен. Ваш профиль будет храниться еще 7 дней. Вы можете продлить подписку в любой момент в меню бота, и профиль снова заработает!",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"Failed to notify user {user.telegram_id} about expiration: {e}")
                    
            # 4. Delete completely if expired > 7 days
            elif time_left.total_seconds() < 0 and not user.is_active:
                expired_days = -days_left
                if expired_days >= 7:
                    print(f"User {user.telegram_id} expired > 7 days ago. Deleting from DB.")
                    await session.delete(user)
                    try:
                        await bot.send_message(
                            chat_id=user.telegram_id, 
                            text="🗑 <b>Ваш профиль был удален</b>, так как подписка закончилась более 7 дней назад.\nЕсли захотите вернуться, просто сгенерируйте новый профиль в меню бота!",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        pass
        
        await session.commit()
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(check_expiry())
