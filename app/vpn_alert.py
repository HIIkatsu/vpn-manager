#!/usr/bin/env python3
import html
import json
import os
import subprocess
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(os.environ.get("VPN_MANAGER_HOME", "/root/vpn-manager"))
CFG = BASE / "bot_config.json"
STATE = BASE / ".vpn_alert_state.json"
HEALTH = BASE / "app" / "vpn_health.py"

MAX_TEXT = 3200
RESEND_FAIL_AFTER = 6 * 60 * 60  # repeat fail alert every 6h if still broken


def esc(value):
    return html.escape(str(value), quote=False)


def send(token, chat_id, text):
    data = urllib.parse.urlencode({
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    with urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        timeout=20,
    ) as r:
        r.read()


def load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def run_health_json():
    env = os.environ.copy()
    env["VPN_MANAGER_HOME"] = str(BASE)

    p = subprocess.run(
        ["python3", str(HEALTH), "--json"],
        env=env,
        text=True,
        capture_output=True,
        timeout=80,
    )

    if p.returncode != 0:
        return False, {
            "error": "vpn_health.py --json failed",
            "returncode": p.returncode,
            "stdout": p.stdout[-1800:],
            "stderr": p.stderr[-1800:],
        }

    try:
        report = json.loads(p.stdout)
        return bool(report.get("ok")), report
    except Exception as e:
        return False, {
            "error": f"Cannot parse health JSON: {e}",
            "stdout": p.stdout[-1800:],
            "stderr": p.stderr[-1800:],
        }


def run_health_text():
    env = os.environ.copy()
    env["VPN_MANAGER_HOME"] = str(BASE)

    p = subprocess.run(
        ["python3", str(HEALTH)],
        env=env,
        text=True,
        capture_output=True,
        timeout=80,
    )

    text = (p.stdout or "").strip()
    err = (p.stderr or "").strip()

    if err:
        text = text + "\n\nSTDERR:\n" + err

    return text.strip() or f"vpn_health.py returned code {p.returncode} with no output"


def compact_report(report):
    try:
        return json.dumps(report, ensure_ascii=False, indent=2)
    except Exception:
        return str(report)


def main():
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    token = cfg["bot_token"]
    admin_ids = cfg.get("admin_ids", [])

    ok, report = run_health_json()

    now = int(time.time())
    prev = load_json(STATE, {})
    prev_ok = prev.get("ok")
    last_alert = int(prev.get("last_alert", 0) or 0)

    should_send = False
    title = ""

    if ok and prev_ok is False:
        should_send = True
        title = "✅ <b>NeuroVPN recovered</b>"
    elif not ok and prev_ok is not False:
        should_send = True
        title = "❌ <b>NeuroVPN health alert</b>"
    elif not ok and now - last_alert > RESEND_FAIL_AFTER:
        should_send = True
        title = "❌ <b>NeuroVPN still unhealthy</b>"

    if should_send:
        if ok:
            body = "System looks healthy again."
        else:
            human = run_health_text()
            raw = compact_report(report)
            body = (
                "<b>Health output</b>\n"
                f"<pre>{esc(human[:MAX_TEXT])}</pre>\n\n"
                "<b>Raw summary</b>\n"
                f"<pre>{esc(raw[:1600])}</pre>"
            )

        text = f"{title}\n\n{body}"

        for chat_id in admin_ids:
            send(token, chat_id, text)

        last_alert = now

    STATE.write_text(
        json.dumps(
            {
                "ok": ok,
                "last_check": now,
                "last_alert": last_alert,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
