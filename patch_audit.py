import re

# --- 1. Патчим XrayManager (Убираем утечку дескрипторов и синхронный open) ---
xray_path = '/root/vpn-manager-v2/app/services/xray_manager.py'
with open(xray_path, 'r') as f:
    xray_code = f.read()

# Выносим чтение файла из __init__ в кэшируемый синглтон
new_xray_class = """class XrayManager:
    _channel = None
    _inbound_tags = None
    _target = "127.0.0.1:8080"

    @classmethod
    def _init_config(cls):
        if cls._inbound_tags is not None:
            return
        cls._inbound_tags = []
        try:
            with open("/usr/local/etc/xray/config.json", "r") as f:
                conf = json.load(f)
                for ib in conf.get("inbounds", []):
                    if ib.get("protocol") == "dokodemo-door":
                        cls._target = f"127.0.0.1:{ib.get('port')}"
                    if ib.get("protocol") == "vless":
                        tag = ib.get("tag")
                        if tag:
                            cls._inbound_tags.append(tag)
        except Exception:
            pass
        if not cls._inbound_tags:
            cls._inbound_tags = ["vless"]

    @classmethod
    def get_channel(cls):
        cls._init_config()
        if cls._channel is None:
            cls._channel = grpc.aio.insecure_channel(cls._target)
        return cls._channel

    def __init__(self):
        self._init_config()
        self.target = self._target
        self.inbound_tags = self._inbound_tags

    async def add_client(self, email: str, uuid: str) -> bool:
        success_overall = True
        try:
            account_bytes = _build_vless_account_message(uuid)
            typed_account = typed_message_pb2.TypedMessage(type="xray.proxy.vless.Account", value=account_bytes)
            user = user_pb2.User(email=email, account=typed_account)
            operation = command_pb2.AddUserOperation(user=user)
            op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.AddUserOperation", value=operation.SerializeToString())
            
            channel = self.get_channel()
            stub = command_pb2_grpc.HandlerServiceStub(channel)
            for tag in self.inbound_tags:
                request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
                try:
                    await stub.AlterInbound(request)
                except grpc.RpcError as e:
                    if "already exists" not in str(e.details()):
                        success_overall = False
        except Exception as e:
            logger.error(f"Xray add_client failed: {e}")
            return False
        return success_overall

    async def remove_client(self, email: str) -> bool:
        success_overall = True
        try:
            operation = command_pb2.RemoveUserOperation(email=email)
            op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.RemoveUserOperation", value=operation.SerializeToString())
            
            channel = self.get_channel()
            stub = command_pb2_grpc.HandlerServiceStub(channel)
            for tag in self.inbound_tags:
                request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
                try:
                    await stub.AlterInbound(request)
                except grpc.RpcError as e:
                    if "not found" not in str(e.details()).lower():
                        success_overall = False
        except Exception as e:
            logger.error(f"Xray remove_client failed: {e}")
            return False
        return success_overall"""

# Заменяем старый класс (от __init__ до get_live_traffic_stats)
xray_code = re.sub(r'class XrayManager:.*?def get_live_traffic_stats', new_xray_class + '\n\n    async def get_live_traffic_stats', xray_code, flags=re.DOTALL)

with open(xray_path, 'w') as f:
    f.write(xray_code)


# --- 2. Патчим BillingRouter (Безопасность и логи) ---
billing_path = '/root/vpn-manager-v2/app/api/routers/billing_router.py'
with open(billing_path, 'r') as f:
    billing_code = f.read()

# Убираем хакерскую дыру if False:
billing_code = billing_code.replace(
"""    if False:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook authorization")""",
"""    if not yookassa.is_valid_webhook_auth(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook authorization")"""
)

# Убираем слепой print
billing_code = billing_code.replace(
"""    except Exception as e:
        print(f"Failed to send message: {e}")""",
"""    except Exception as e:
        logger.error("Failed to send payment confirmation to Telegram", extra=log_context(error=str(e), user_id=payment.user_id, payment_id=payment_obj.id))"""
)

with open(billing_path, 'w') as f:
    f.write(billing_code)

print("✅ Аудит-патч успешно применен!")
