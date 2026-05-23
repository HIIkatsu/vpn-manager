from app.services.xray_manager import XrayManager


def format_bytes(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} TB"


async def get_dynamic_sub_info(local_vars) -> str:
    try:
        user = next((v for v in local_vars.values() if hasattr(v, "telegram_id") and hasattr(v, "sub_end_date")), None)
        if not user:
            return "upload=0; download=0; total=1099511627776; expire=0"
        xray = XrayManager()
        stats = await xray.get_live_traffic_stats()
        used = stats.get(str(user.telegram_id), 0)
        exp = int(user.sub_end_date.timestamp()) if user.sub_end_date else 0
        return f"upload=0; download={used}; total=1099511627776; expire={exp}"
    except Exception:
        return "upload=0; download=0; total=1099511627776; expire=0"
