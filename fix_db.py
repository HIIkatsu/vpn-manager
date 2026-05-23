filepath = 'app/api/routers/admin_router.py'
with open(filepath, 'r') as f:
    text = f.read()

# Вырезаем плохие асинхронные таски
text = text.replace('users_task = asyncio.create_task(session.execute(stmt))', '')
text = text.replace('pending_task = asyncio.create_task(session.execute(select(PendingAction).options(joinedload(PendingAction.user))))', '')

# Меняем их на строгие последовательные запросы
text = text.replace('users_db = (await users_task).scalars().all()', 'users_db = (await session.execute(stmt)).scalars().all()')
text = text.replace('pending_actions = (await pending_task).scalars().all()', 'pending_actions = (await session.execute(select(PendingAction).options(joinedload(PendingAction.user)))).scalars().all()')

with open(filepath, 'w') as f:
    f.write(text)
print("✅ Баг с базой успешно вырезан!")
