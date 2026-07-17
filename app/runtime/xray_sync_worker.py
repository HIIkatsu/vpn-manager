import asyncio
import logging
import grpc
from typing import Dict, Set

from sqlalchemy import select

from app.core.settings import settings
from app.db.database import async_session_maker
from app.db.models import User
from app.grpc.xray_api.app.proxyman.command import command_pb2, command_pb2_grpc
from app.grpc.xray_api.common.protocol import user_pb2
from app.grpc.xray_api.common.serial import typed_message_pb2
from app.services.xray_manager import _build_vless_account_message, XrayManager

logger = logging.getLogger("xray_sync_worker")

class XraySyncWorker:
    def __init__(self, interval: int = 10, concurrency: int = 50):
        self.interval = interval
        self.concurrency = concurrency
        self.local_state: Dict[str, str] = {}  # vless_uuid -> telegram_id (as email)
        self.channel = None
        self.stub = None
        self.target = "127.0.0.1:10085"
        self.tags = ["vless-smart"]

    async def initialize(self):
        manager = XrayManager()
        target, tags = await manager._get_current_config()
        self.target = target
        if tags:
            self.tags = tags
        self._connect()

    def _connect(self):
        if self.channel:
            asyncio.create_task(self.channel.close())
        
        options = [
            ("grpc.keepalive_time_ms", 30000),
            ("grpc.keepalive_timeout_ms", 10000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.max_receive_message_length", 16 * 1024 * 1024),
            ("grpc.max_send_message_length", 16 * 1024 * 1024),
            ("grpc.initial_reconnect_backoff_ms", 500),
            ("grpc.max_reconnect_backoff_ms", 5000),
        ]
        self.channel = grpc.aio.insecure_channel(self.target, options=options)
        self.stub = command_pb2_grpc.HandlerServiceStub(self.channel)
        logger.info(f"Connected to Xray gRPC at {self.target} with tags {self.tags}")

    async def _add_user_to_tag(self, uuid: str, email: str, tag: str) -> bool:
        flow = "xtls-rprx-vision" if "ws" not in tag.lower() else ""
        account_bytes = _build_vless_account_message(uuid, flow=flow)
        typed_account = typed_message_pb2.TypedMessage(
            type="xray.proxy.vless.Account", 
            value=account_bytes
        )
        user = user_pb2.User(email=email, account=typed_account)
        operation = command_pb2.AddUserOperation(user=user)
        op_typed = typed_message_pb2.TypedMessage(
            type="xray.app.proxyman.command.AddUserOperation", 
            value=operation.SerializeToString()
        )
        request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
        try:
            await self.stub.AlterInbound(request, timeout=5.0)
            return True
        except grpc.RpcError as e:
            if "already exists" in str(e.details()).lower():
                return True
            raise e

    async def _remove_user_from_tag(self, email: str, tag: str) -> bool:
        operation = command_pb2.RemoveUserOperation(email=email)
        op_typed = typed_message_pb2.TypedMessage(
            type="xray.app.proxyman.command.RemoveUserOperation", 
            value=operation.SerializeToString()
        )
        request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
        try:
            await self.stub.AlterInbound(request, timeout=5.0)
            return True
        except grpc.RpcError as e:
            if "not found" in str(e.details()).lower():
                return True
            raise e

    async def _check_amnesia(self) -> bool:
        """
        Отправляет тестовый запрос на первого попавшегося юзера из local_state.
        Если Xray возвращает 'already exists' — всё супер, память на месте.
        Если запрос проходит успешно (без ошибки) — значит Xray был пуст, у него амнезия!
        """
        if not self.local_state:
            return True
            
        uuid = next(iter(self.local_state.keys()))
        email = self.local_state[uuid]
        tag = self.tags[0]
        
        flow = "xtls-rprx-vision" if "ws" not in tag.lower() else ""
        account_bytes = _build_vless_account_message(uuid, flow=flow)
        typed_account = typed_message_pb2.TypedMessage(type="xray.proxy.vless.Account", value=account_bytes)
        user = user_pb2.User(email=email, account=typed_account)
        operation = command_pb2.AddUserOperation(user=user)
        op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.AddUserOperation", value=operation.SerializeToString())
        request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
        
        try:
            await self.stub.AlterInbound(request, timeout=2.0)
            return False # Успех = юзера не было! Амнезия ядра!
        except grpc.RpcError as e:
            if "already exists" in str(e.details()).lower():
                return True # Всё хорошо
            raise e

    async def process_add(self, uuid: str, email: str, sem: asyncio.Semaphore):
        async with sem:
            for tag in self.tags:
                await self._add_user_to_tag(uuid, email, tag)

    async def process_remove(self, email: str, sem: asyncio.Semaphore):
        async with sem:
            for tag in self.tags:
                await self._remove_user_from_tag(email, tag)

    async def sync_loop(self):
        await self.initialize()
        logger.info("XraySyncWorker started. Waiting for the first DB sync...")
        
        while True:
            try:
                # 1. Запрашиваем активных пользователей из БД
                async with async_session_maker() as session:
                    users = (
                        await session.execute(select(User).where(User.is_active.is_(True)))
                    ).scalars().all()
                    
                db_state = {user.vless_uuid: str(user.telegram_id) for user in users}
                db_uuids = set(db_state.keys())
                local_uuids = set(self.local_state.keys())
                
                # 2. Вычисляем дельты (Smart Delta Sync)
                to_add_uuids = db_uuids - local_uuids
                to_remove_uuids = local_uuids - db_uuids
                
                if not to_add_uuids and not to_remove_uuids:
                    # Если нет изменений, проверяем ядро на "амнезию" (случайный рестарт Xray)
                    if not await self._check_amnesia():
                        logger.warning("Amnesia detected! Xray core lost its memory. Triggering Full Sync.")
                        self.local_state.clear()
                        continue # Сразу переходим к следующей итерации для Full Sync
                        
                    await asyncio.sleep(self.interval)
                    continue

                if not local_uuids:
                    logger.info("Performing Full Sync. Core state is empty.")
                else:
                    logger.info(f"Delta Sync: {len(to_add_uuids)} to add, {len(to_remove_uuids)} to remove.")

                sem = asyncio.Semaphore(self.concurrency)
                
                # 3. Удаляем старых и измененных (СНАЧАЛА УДАЛЯЕМ)
                if to_remove_uuids:
                    remove_tasks = [self.process_remove(self.local_state[u], sem) for u in to_remove_uuids]
                    await asyncio.gather(*remove_tasks)
                    for u in to_remove_uuids:
                        email = self.local_state.pop(u, None)
                        logger.info(f"[REMOVE] Successfully removed user uuid={u} email={email}")
                        
                # 4. Добавляем новых (ПОТОМ ДОБАВЛЯЕМ)
                if to_add_uuids:
                    add_tasks = [self.process_add(u, db_state[u], sem) for u in to_add_uuids]
                    await asyncio.gather(*add_tasks)
                    for u in to_add_uuids:
                        self.local_state[u] = db_state[u]
                        logger.info(f"[ADD] Successfully added user uuid={u} email={db_state[u]}")

            except grpc.RpcError as e:
                # 5. Защита от амнезии ядра (Crash Recovery)
                logger.error(f"gRPC connection error: {e.code()} - {e.details()}")
                logger.warning("Xray Core connection issue or restart detected. Resetting local_state for Full Sync on next iteration.")
                self.local_state.clear()
                self._connect()  # Пересоздаем канал и stub
                
            except Exception as e:
                logger.exception("Unexpected error in sync loop")

            await asyncio.sleep(self.interval)

async def main():
    worker = XraySyncWorker()
    await worker.sync_loop()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(main())
