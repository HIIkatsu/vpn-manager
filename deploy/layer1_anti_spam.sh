#!/usr/bin/env bash
# =============================================================================
# Layer 1: Anti-Spam — Block ALL outbound SMTP traffic
# Prevents malware on client devices from using VPN tunnel for spam
# =============================================================================
set -euo pipefail

echo "========================================="
echo "  Layer 1: Anti-Spam Deployment"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================="

# SMTP ports to block: 25 (SMTP), 465 (SMTPS), 587 (Submission), 2525 (Alt-SMTP)
SMTP_PORTS="25,465,587,2525"
# Also block POP3 for good measure: 110 (POP3), 995 (POP3S)
MAIL_FETCH_PORTS="110,995"

# --- Setup logging ---
LOG_DIR="/var/log"
RSYSLOG_CONF="/etc/rsyslog.d/50-smtp-block.conf"

echo "[1/5] Configuring SMTP block logging..."
cat > "$RSYSLOG_CONF" << 'EOF'
# Log SMTP blocked packets to a dedicated file
:msg, contains, "SMTP-BLOCKED" /var/log/smtp_block.log
:msg, contains, "SMTP-FWD-BLOCKED" /var/log/smtp_block.log
& stop
EOF

# Logrotate for SMTP block log
cat > /etc/logrotate.d/smtp-block << 'EOF'
/var/log/smtp_block.log {
    daily
    rotate 30
    missingok
    compress
    delaycompress
    notifempty
    create 0640 root root
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate 2>/dev/null || true
    endscript
}
EOF

systemctl restart rsyslog 2>/dev/null || true

# --- Remove any existing SMTP rules to avoid duplicates ---
echo "[2/5] Cleaning existing SMTP rules (if any)..."
while iptables -D OUTPUT -p tcp -m multiport --dports $SMTP_PORTS -j DROP 2>/dev/null; do :; done
while iptables -D OUTPUT -p tcp -m multiport --dports $SMTP_PORTS -j LOG --log-prefix "[SMTP-BLOCKED] " --log-level 4 2>/dev/null; do :; done
while iptables -D FORWARD -p tcp -m multiport --dports $SMTP_PORTS -j DROP 2>/dev/null; do :; done
while iptables -D FORWARD -p tcp -m multiport --dports $SMTP_PORTS -j LOG --log-prefix "[SMTP-FWD-BLOCKED] " --log-level 4 2>/dev/null; do :; done
while iptables -D OUTPUT -p tcp -m multiport --dports $MAIL_FETCH_PORTS -j DROP 2>/dev/null; do :; done
while iptables -D FORWARD -p tcp -m multiport --dports $MAIL_FETCH_PORTS -j DROP 2>/dev/null; do :; done

# Also clean ip6tables
while ip6tables -D OUTPUT -p tcp -m multiport --dports $SMTP_PORTS -j DROP 2>/dev/null; do :; done
while ip6tables -D OUTPUT -p tcp -m multiport --dports $SMTP_PORTS -j LOG --log-prefix "[SMTP-BLOCKED] " --log-level 4 2>/dev/null; do :; done
while ip6tables -D FORWARD -p tcp -m multiport --dports $SMTP_PORTS -j DROP 2>/dev/null; do :; done
while ip6tables -D FORWARD -p tcp -m multiport --dports $SMTP_PORTS -j LOG --log-prefix "[SMTP-FWD-BLOCKED] " --log-level 4 2>/dev/null; do :; done
while ip6tables -D OUTPUT -p tcp -m multiport --dports $MAIL_FETCH_PORTS -j DROP 2>/dev/null; do :; done
while ip6tables -D FORWARD -p tcp -m multiport --dports $MAIL_FETCH_PORTS -j DROP 2>/dev/null; do :; done

# --- Apply iptables rules ---
echo "[3/5] Applying iptables SMTP block rules..."

# OUTPUT chain — blocks server itself from sending SMTP
# Log first (rate-limited to avoid log flooding), then DROP
iptables -I OUTPUT -p tcp -m multiport --dports $SMTP_PORTS -j LOG \
    --log-prefix "[SMTP-BLOCKED] " --log-level 4 -m limit --limit 10/min --limit-burst 20
iptables -I OUTPUT 2 -p tcp -m multiport --dports $SMTP_PORTS -j DROP

# FORWARD chain — blocks VPN client traffic forwarded through the server
iptables -I FORWARD -p tcp -m multiport --dports $SMTP_PORTS -j LOG \
    --log-prefix "[SMTP-FWD-BLOCKED] " --log-level 4 -m limit --limit 10/min --limit-burst 20
iptables -I FORWARD 2 -p tcp -m multiport --dports $SMTP_PORTS -j DROP

# Block POP3/POP3S (mail fetching — often used by compromised bots)
iptables -I OUTPUT 3 -p tcp -m multiport --dports $MAIL_FETCH_PORTS -j DROP
iptables -I FORWARD 3 -p tcp -m multiport --dports $MAIL_FETCH_PORTS -j DROP

# Same for IPv6
ip6tables -I OUTPUT -p tcp -m multiport --dports $SMTP_PORTS -j LOG \
    --log-prefix "[SMTP-BLOCKED] " --log-level 4 -m limit --limit 10/min --limit-burst 20
ip6tables -I OUTPUT 2 -p tcp -m multiport --dports $SMTP_PORTS -j DROP
ip6tables -I FORWARD -p tcp -m multiport --dports $SMTP_PORTS -j LOG \
    --log-prefix "[SMTP-FWD-BLOCKED] " --log-level 4 -m limit --limit 10/min --limit-burst 20
ip6tables -I FORWARD 2 -p tcp -m multiport --dports $SMTP_PORTS -j DROP
ip6tables -I OUTPUT 3 -p tcp -m multiport --dports $MAIL_FETCH_PORTS -j DROP
ip6tables -I FORWARD 3 -p tcp -m multiport --dports $MAIL_FETCH_PORTS -j DROP

# --- Persist rules across reboots ---
echo "[4/5] Persisting iptables rules..."
DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null 2>&1 || true
mkdir -p /etc/iptables
iptables-save > /etc/iptables/rules.v4
ip6tables-save > /etc/iptables/rules.v6

# --- Verify ---
echo "[5/5] Verifying SMTP block..."
echo ""
echo "=== OUTPUT chain (IPv4) ==="
iptables -L OUTPUT -n --line-numbers | head -8
echo ""
echo "=== FORWARD chain (IPv4) ==="
iptables -L FORWARD -n --line-numbers | head -8
echo ""

# Quick test
if timeout 3 bash -c 'echo QUIT | nc -w2 smtp.google.com 25' 2>/dev/null; then
    echo "⚠️  WARNING: SMTP port 25 is still reachable! Check rules."
else
    echo "✅ SMTP port 25 is BLOCKED successfully."
fi

echo ""
echo "========================================="
echo "  Layer 1 COMPLETE"
echo "  SMTP ports $SMTP_PORTS blocked on OUTPUT + FORWARD"
echo "  Mail fetch ports $MAIL_FETCH_PORTS blocked"
echo "  Logs: /var/log/smtp_block.log"
echo "========================================="
