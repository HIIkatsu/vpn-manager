#!/bin/bash
# Пытаемся получить статистику через локальный API Xray
# Если Xray завис или упал, команда завершится с ошибкой
if ! xray api statsquery --server=127.0.0.1:10085 > /dev/null 2>&1; then
    echo "$(date): ⚠️ Xray API не отвечает! Делаем хард-рестарт..." >> /var/log/xray_watchdog.log
    systemctl restart xray
fi
