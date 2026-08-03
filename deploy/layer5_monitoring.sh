#!/usr/bin/env bash
# =============================================================================
# Layer 5: Monitoring & Alerting
# SMTP block monitoring, connection spike detection, Spamhaus self-check
# =============================================================================
set -euo pipefail

echo "========================================="
echo "  Layer 5: Monitoring & Alerting"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================="

# =============================================
# 1. SMTP Block Monitor Script
# =============================================
echo "[1/3] Installing SMTP block monitor..."

cat > /usr/local/bin/vpn-monitor-smtp << 'SCRIPT'
#!/usr/bin/env bash
# Counts SMTP block events in the last hour and logs summary
LOG="/var/log/smtp_block.log"
ALERT_LOG="/var/log/vpn_monitor.log"

if [ ! -f "$LOG" ]; then
    exit 0
fi

# Count events in the last hour
HOUR_AGO=$(date -d '1 hour ago' '+%b %e %H' 2>/dev/null || date '+%b %e %H')
COUNT=$(grep -c "SMTP-BLOCKED" "$LOG" 2>/dev/null || echo 0)

TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

if [ "$COUNT" -gt 0 ]; then
    echo "[$TIMESTAMP] SMTP block events in log: $COUNT total" >> "$ALERT_LOG"
fi

if [ "$COUNT" -gt 100 ]; then
    echo "[$TIMESTAMP] ⚠️  HIGH SMTP BLOCK COUNT: $COUNT — possible active malware on VPN client" >> "$ALERT_LOG"
fi
SCRIPT

chmod +x /usr/local/bin/vpn-monitor-smtp

# =============================================
# 2. Connection Spike Detector
# =============================================
echo "[2/3] Installing connection monitor..."

cat > /usr/local/bin/vpn-monitor-connections << 'SCRIPT'
#!/usr/bin/env bash
# Monitors active connections to port 443 and logs spikes
ALERT_LOG="/var/log/vpn_monitor.log"
TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

# Count established connections to port 443
CONN_443=$(ss -tn state established '( dport = :443 or sport = :443 )' 2>/dev/null | wc -l)

# Count SYN_RECV (potential SYN flood indicator)
SYN_RECV=$(ss -tn state syn-recv '( dport = :443 or sport = :443 )' 2>/dev/null | wc -l)

# Count unique source IPs
UNIQUE_IPS=$(ss -tn state established '( dport = :443 or sport = :443 )' 2>/dev/null | awk '{print $4}' | cut -d: -f1 | sort -u | wc -l)

echo "[$TIMESTAMP] Port 443: established=$CONN_443 syn_recv=$SYN_RECV unique_ips=$UNIQUE_IPS" >> "$ALERT_LOG"

# Alert thresholds
if [ "$SYN_RECV" -gt 50 ]; then
    echo "[$TIMESTAMP] ⚠️  HIGH SYN_RECV on 443: $SYN_RECV — possible SYN flood!" >> "$ALERT_LOG"
fi

if [ "$CONN_443" -gt 1000 ]; then
    echo "[$TIMESTAMP] ⚠️  HIGH CONNECTION COUNT on 443: $CONN_443" >> "$ALERT_LOG"
fi
SCRIPT

chmod +x /usr/local/bin/vpn-monitor-connections

# =============================================
# 3. Spamhaus Self-Check
# =============================================
echo "[3/3] Installing Spamhaus check..."

cat > /usr/local/bin/vpn-check-spamhaus << 'SCRIPT'
#!/usr/bin/env bash
# Checks if this server's IP is listed in Spamhaus XBL/SBL/PBL
ALERT_LOG="/var/log/vpn_monitor.log"
TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

# Get server's public IP
SERVER_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')

if [ -z "$SERVER_IP" ]; then
    echo "[$TIMESTAMP] Could not determine server IP for Spamhaus check" >> "$ALERT_LOG"
    exit 1
fi

# Reverse the IP for DNS lookup
REVERSED=$(echo "$SERVER_IP" | awk -F. '{print $4"."$3"."$2"."$1}')

# Check against Spamhaus ZEN (combined SBL+XBL+PBL)
RESULT=$(dig +short "${REVERSED}.zen.spamhaus.org" 2>/dev/null || echo "")

if [ -n "$RESULT" ] && [ "$RESULT" != "" ]; then
    echo "[$TIMESTAMP] 🔴 SPAMHAUS LISTED! IP=$SERVER_IP result=$RESULT — CHECK https://check.spamhaus.org/listed/?searchterm=$SERVER_IP" >> "$ALERT_LOG"
    echo "SPAMHAUS ALERT: $SERVER_IP is listed ($RESULT)"
else
    echo "[$TIMESTAMP] ✅ Spamhaus clear: IP=$SERVER_IP" >> "$ALERT_LOG"
fi
SCRIPT

chmod +x /usr/local/bin/vpn-check-spamhaus

# =============================================
# 4. Setup cron jobs
# =============================================
echo "Setting up cron jobs..."

# Remove old vpn-monitor cron entries
crontab -l 2>/dev/null | grep -v "vpn-monitor\|vpn-check-spamhaus" > /tmp/cron_clean || true

# Add new entries
cat >> /tmp/cron_clean << 'CRON'
# VPN Security Monitoring (deployed by hardening script)
*/5 * * * * /usr/local/bin/vpn-monitor-smtp
*/2 * * * * /usr/local/bin/vpn-monitor-connections
0 */6 * * * /usr/local/bin/vpn-check-spamhaus
CRON

crontab /tmp/cron_clean
rm -f /tmp/cron_clean

# Setup logrotate for monitor log
cat > /etc/logrotate.d/vpn-monitor << 'EOF'
/var/log/vpn_monitor.log {
    daily
    rotate 30
    missingok
    compress
    delaycompress
    notifempty
    create 0640 root root
}
EOF

# Run initial checks
echo ""
echo "=== Initial Spamhaus Check ==="
/usr/local/bin/vpn-check-spamhaus

echo ""
echo "=== Initial Connection Status ==="
/usr/local/bin/vpn-monitor-connections
tail -2 /var/log/vpn_monitor.log 2>/dev/null || true

echo ""
echo "========================================="
echo "  Layer 5 COMPLETE"
echo "  - SMTP block monitor: every 5 min"
echo "  - Connection monitor: every 2 min"
echo "  - Spamhaus check: every 6 hours"
echo "  - Logs: /var/log/vpn_monitor.log"
echo "========================================="
