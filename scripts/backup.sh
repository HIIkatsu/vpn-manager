#!/bin/bash
# VPN Manager V2 — FULL Backup Script
# Backs up: PostgreSQL database, Xray config, .env, Source Code, Nginx, Systemd

set -euo pipefail

BACKUP_DIR=/root/backups
DATE=$(date +%F_%H-%M)
BACKUP_NAME="vpn_v2_full_backup_${DATE}"
RETENTION_DAYS=14

echo "[$(date)] Starting FULL backup..."

# 1. PostgreSQL dump via Docker
docker exec vpn_v2_db pg_dump -U vpn_user -d vpn_v2 | gzip > "${BACKUP_DIR}/${BACKUP_NAME}_db.sql.gz"

# 2. Xray config
cp /usr/local/etc/xray/config.json "${BACKUP_DIR}/${BACKUP_NAME}_xray.json"

# 3. Create a temporary staging directory to collect everything safely
STAGING_DIR="${BACKUP_DIR}/staging_${BACKUP_NAME}"
mkdir -p "${STAGING_DIR}"

# Move the DB and Xray config to staging
mv "${BACKUP_DIR}/${BACKUP_NAME}_db.sql.gz" "${STAGING_DIR}/"
mv "${BACKUP_DIR}/${BACKUP_NAME}_xray.json" "${STAGING_DIR}/"

# 4. Backup the VPN Manager Source Code (excluding venv, .git, and cache)
echo "Archiving source code..."
rsync -a --exclude 'venv' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' /root/vpn-manager-v2 "${STAGING_DIR}/"

# 5. Backup Nginx Configurations
echo "Archiving Nginx configs..."
rsync -a /etc/nginx "${STAGING_DIR}/"

# 6. Backup Systemd Services
echo "Archiving Systemd services..."
mkdir -p "${STAGING_DIR}/systemd_services"
cp /etc/systemd/system/vpn-* /etc/systemd/system/xray* "${STAGING_DIR}/systemd_services/" 2>/dev/null || true

# 7. Create final tar archive from staging
cd ${BACKUP_DIR}
echo "Creating final archive..."
tar czf "${BACKUP_NAME}.tar.gz" -C "${STAGING_DIR}" .

# 8. Cleanup staging directory
rm -rf "${STAGING_DIR}"

# 9. Cleanup old backups
find ${BACKUP_DIR} -name 'vpn_v2_full_backup_*.tar.gz' -mtime +${RETENTION_DAYS} -delete
# Also cleanup old V1 backups if any
find ${BACKUP_DIR} -name 'vpn_v2_backup_*.tar.gz' -mtime +${RETENTION_DAYS} -delete

echo "[$(date)] Backup complete: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
ls -lh "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
