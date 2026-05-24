#!/usr/bin/env bash
set -euo pipefail

LOCK_FILE="/var/lock/xray_watchdog.lock"
LOG_FILE="/var/log/xray_watchdog.log"
MAX_RESTARTS_PER_WINDOW=3
WINDOW_SECONDS=300
TIMEOUT_SECONDS=10
STATE_DIR="/run/xray-watchdog"
STATE_FILE="${STATE_DIR}/restart_times"

mkdir -p "${STATE_DIR}"
touch "${STATE_FILE}"

exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

now_epoch="$(date +%s)"

mapfile -t recent_restarts < <(
  awk -v now="${now_epoch}" -v window="${WINDOW_SECONDS}" 'now - $1 <= window { print $1 }' "${STATE_FILE}" 2>/dev/null || true
)

if ((${#recent_restarts[@]} >= MAX_RESTARTS_PER_WINDOW)); then
  printf '%s: restart rate-limit reached, skipping restart\n' "$(date --iso-8601=seconds)" >> "${LOG_FILE}"
  exit 0
fi

if ! timeout "${TIMEOUT_SECONDS}" xray api statsquery --server=127.0.0.1:10085 >/dev/null 2>&1; then
  printf '%s: xray API check failed, restarting xray service\n' "$(date --iso-8601=seconds)" >> "${LOG_FILE}"
  timeout "${TIMEOUT_SECONDS}" systemctl restart xray
  {
    for ts in "${recent_restarts[@]}"; do
      printf '%s\n' "${ts}"
    done
    printf '%s\n' "${now_epoch}"
  } > "${STATE_FILE}.tmp"
  mv "${STATE_FILE}.tmp" "${STATE_FILE}"
fi
