import logging
import grpc

from app.core.settings import settings
from app.grpc.xray_api.app.proxyman.command import command_pb2, command_pb2_grpc
from app.grpc.xray_api.common.protocol import user_pb2
from app.grpc.xray_api.proxy.vless import account_pb2 as vless_account_pb2
from app.grpc.xray_api.common.serial import typed_message_pb2

logger = logging.getLogger(__name__)

class XrayManager:
    async def add_client(self, email: str, uuid: str) -> bool:
        target = f"{settings.XRAY_GRPC_HOST}:{settings.XRAY_GRPC_PORT}"
        try:
            async with grpc.aio.insecure_channel(target) as channel:
                stub = command_pb2_grpc.HandlerServiceStub(channel)
                
                # 1. Создаем VLESS аккаунт. flow="xtls-rprx-vision" нужен, если используешь Reality
                vless_account = vless_account_pb2.Account(
                    id=uuid,
                    flow="xtls-rprx-vision" 
                )

                # 2. Упаковываем аккаунт в TypedMessage
                account_msg = typed_message_pb2.TypedMessage(
                    type="xray.proxy.vless.Account",
                    value=vless_account.SerializeToString()
                )

                # 3. Создаем юзера
                user = user_pb2.User(
                    email=email,
                    level=0,
                    account=account_msg
                )

                # 4. Создаем операцию добавления
                add_op = command_pb2.AddUserOperation(user=user)

                # 5. Упаковываем операцию в TypedMessage
                op_msg = typed_message_pb2.TypedMessage(
                    type="xray.app.proxyman.command.AddUserOperation",
                    value=add_op.SerializeToString()
                )

                # 6. Формируем финальный запрос к Inbound
                # ВАЖНО: tag="vless-in" должен совпадать с тегом твоего входящего соединения в config.json сервера Xray!
                request = command_pb2.AlterInboundRequest(
                    tag="vless-reality",
                    operation=op_msg
                )

                await stub.AlterInbound(request)
                return True
                
        except grpc.RpcError as e:
            logger.error(f"Failed to add client to Xray via gRPC. Details: {e.details()}")
            return False

    def generate_vless_link(self, uuid: str) -> str:
        return (
            f"vless://{uuid}@{settings.WEBHOOK_URL_DOMAIN}:443"
            "?type=tcp&security=reality&encryption=none&sni=example.com&fp=chrome&pbk=placeholder#VPN"
        )
