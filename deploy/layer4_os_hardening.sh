#!/usr/bin/env bash
# =============================================================================
# Layer 4: OS Hardening — Kernel, SSH, fail2ban
# Universal script for all server roles
# =============================================================================
set -euo pipefail

echo "========================================="
echo "  Layer 4: OS Hardening"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================="

# =============================================
# 1. Kernel Network Hardening (sysctl)
# =============================================
echo "[1/4] Applying kernel network hardening..."

SYSCTL_FILE="/etc/sysctl.d/99-vpn-hardening.conf"

cat > "$SYSCTL_FILE" << 'EOF'
# === VPN Server Hardening — Applied by deploy script ===

# --- SYN flood protection ---
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 4096
net.ipv4.tcp_synack_retries = 2
net.ipv4.tcp_syn_retries = 3

# --- Reverse path filtering (anti-spoofing) ---
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# --- Disable ICMP redirects ---
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0

# --- Disable source routing ---
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0

# --- Ignore broadcast pings ---
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1

# --- TCP connection optimization ---
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_intvl = 60
net.ipv4.tcp_keepalive_probes = 5

# --- Connection backlog ---
net.core.somaxconn = 4096
net.core.netdev_max_backlog = 4096

# --- Conntrack (increase for VPN workload) ---
net.netfilter.nf_conntrack_max = 131072
net.netfilter.nf_conntrack_tcp_timeout_established = 3600
net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30

# --- Memory tuning for network buffers ---
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216

# --- Disable IPv6 if not needed (optional, uncomment if desired) ---
# net.ipv6.conf.all.disable_ipv6 = 1
# net.ipv6.conf.default.disable_ipv6 = 1
EOF

sysctl -p "$SYSCTL_FILE" 2>/dev/null || {
    echo "  Some sysctl params may not be available, applying what we can..."
    sysctl -p "$SYSCTL_FILE" 2>&1 | grep -v "No such file" || true
}
echo "  ✅ Kernel hardening applied"

# =============================================
# 2. SSH Hardening
# =============================================
echo "[2/4] Hardening SSH configuration..."

SSHD_HARDENING="/etc/ssh/sshd_config.d/99-vpn-hardening.conf"

cat > "$SSHD_HARDENING" << 'EOF'
# === VPN Server SSH Hardening ===

# Reduce brute-force window
MaxAuthTries 3
LoginGraceTime 30

# Session keepalive (detect dead sessions)
ClientAliveInterval 300
ClientAliveCountMax 2

# Disable risky features
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding yes

# Disable empty passwords
PermitEmptyPasswords no

# Log level for security auditing
LogLevel VERBOSE

# Only allow SSH protocol 2
Protocol 2
EOF

# Validate sshd config before restart
if sshd -t 2>/dev/null; then
    systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
    echo "  ✅ SSH hardened and restarted"
else
    echo "  ⚠️  SSH config validation failed, removing hardening file"
    rm -f "$SSHD_HARDENING"
fi

# =============================================
# 3. fail2ban
# =============================================
echo "[3/4] Installing and configuring fail2ban..."

apt-get install -y fail2ban >/dev/null 2>&1 || true

# Configure fail2ban jails
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3
banaction = nftables[type=allports]
backend = systemd

[sshd]
enabled = true
port = ssh
filter = sshd
maxretry = 3
bantime = 3600
findtime = 600
EOF

# Restart fail2ban
systemctl enable fail2ban 2>/dev/null || true
systemctl restart fail2ban 2>/dev/null || true

if systemctl is-active --quiet fail2ban; then
    echo "  ✅ fail2ban active"
    fail2ban-client status sshd 2>/dev/null || echo "  (sshd jail loading...)"
else
    echo "  ⚠️  fail2ban failed to start, check: journalctl -u fail2ban"
fi

# =============================================
# 4. Disable unnecessary services
# =============================================
echo "[4/4] Disabling unnecessary services..."

for svc in ModemManager fwupd udisks2; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
        systemctl mask "$svc" 2>/dev/null || true
        echo "  Disabled: $svc"
    fi
done

echo ""
echo "========================================="
echo "  Layer 4 COMPLETE"
echo "  - Kernel network hardening (sysctl)"
echo "  - SSH: MaxAuthTries=3, LoginGraceTime=30"
echo "  - fail2ban: SSH jail, 3 attempts = 1h ban"
echo "  - Unnecessary services disabled"
echo "========================================="
