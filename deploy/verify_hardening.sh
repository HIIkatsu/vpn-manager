#!/usr/bin/env bash
# =============================================================================
# Post-deployment verification script
# Run on each server after all layers are deployed
# =============================================================================
set -euo pipefail

echo "============================================================"
echo "  VPN HARDENING — POST-DEPLOYMENT VERIFICATION"
echo "  Server: $(hostname) / $(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')"
echo "  Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "============================================================"
echo ""

PASS=0
FAIL=0
WARN=0

check_pass() { echo "  ✅ PASS: $1"; ((PASS++)); }
check_fail() { echo "  ❌ FAIL: $1"; ((FAIL++)); }
check_warn() { echo "  ⚠️  WARN: $1"; ((WARN++)); }

# === Layer 1: Anti-Spam ===
echo "--- Layer 1: Anti-Spam ---"

# Check iptables SMTP rules
if iptables -L OUTPUT -n 2>/dev/null | grep -q "25,465,587,2525"; then
    check_pass "iptables OUTPUT SMTP block present"
else
    check_fail "iptables OUTPUT SMTP block MISSING"
fi

if iptables -L FORWARD -n 2>/dev/null | grep -q "25,465,587,2525"; then
    check_pass "iptables FORWARD SMTP block present"
else
    check_fail "iptables FORWARD SMTP block MISSING"
fi

# SMTP connectivity test
if timeout 3 bash -c 'echo QUIT | nc -w2 smtp.google.com 25' 2>/dev/null; then
    check_fail "SMTP port 25 is STILL reachable — rules not working!"
else
    check_pass "SMTP port 25 is blocked"
fi

# Check persistence
if [ -f /etc/iptables/rules.v4 ]; then
    if grep -q "25,465,587,2525" /etc/iptables/rules.v4; then
        check_pass "iptables rules persisted to disk"
    else
        check_warn "iptables rules file exists but may not contain SMTP blocks"
    fi
else
    check_warn "iptables persistence not confirmed"
fi

echo ""

# === Layer 2: Firewall ===
echo "--- Layer 2: Firewall ---"

if nft list ruleset 2>/dev/null | grep -q "vpn_firewall"; then
    check_pass "nftables vpn_firewall table active"
else
    check_fail "nftables vpn_firewall table NOT found"
fi

if nft list ruleset 2>/dev/null | grep -q "policy drop"; then
    check_pass "Default DROP policy active on input chain"
else
    check_fail "Default DROP policy NOT active"
fi

if nft list ruleset 2>/dev/null | grep -q "rate_vpn_v4\|rate_ssh_v4"; then
    check_pass "Rate limiting sets configured"
else
    check_warn "Rate limiting sets not found"
fi

# Check that management ports are not exposed
EXPOSED_MGMT=$(ss -tlnp 2>/dev/null | grep -E "0\.0\.0\.0:(8090|3001|5433|10085)" || true)
if [ -z "$EXPOSED_MGMT" ]; then
    check_pass "Management ports not exposed on 0.0.0.0 (or protected by firewall)"
else
    check_warn "Management ports on 0.0.0.0 (protected by nftables DROP): $EXPOSED_MGMT"
fi

echo ""

# === Layer 3: Xray Hardening ===
echo "--- Layer 3: Xray Hardening ---"

XRAY_CONFIG="/usr/local/etc/xray/config.json"

if [ -f "$XRAY_CONFIG" ]; then
    # Check SMTP routing block
    if python3 -c "
import json
with open('$XRAY_CONFIG') as f:
    c = json.load(f)
rules = c.get('routing',{}).get('rules',[])
smtp = [r for r in rules if '25' in str(r.get('port',''))]
exit(0 if smtp else 1)
" 2>/dev/null; then
        check_pass "Xray routing: SMTP ports blocked"
    else
        check_fail "Xray routing: SMTP block rule MISSING"
    fi

    # Check BitTorrent block
    if python3 -c "
import json
with open('$XRAY_CONFIG') as f:
    c = json.load(f)
rules = c.get('routing',{}).get('rules',[])
bt = [r for r in rules if 'bittorrent' in str(r.get('protocol',[]))]
exit(0 if bt else 1)
" 2>/dev/null; then
        check_pass "Xray routing: BitTorrent blocked"
    else
        check_fail "Xray routing: BitTorrent block MISSING"
    fi

    # Check policy timeouts
    if python3 -c "
import json
with open('$XRAY_CONFIG') as f:
    c = json.load(f)
p = c.get('policy',{}).get('levels',{}).get('0',{})
exit(0 if p.get('handshake') == 4 and p.get('connIdle') == 300 else 1)
" 2>/dev/null; then
        check_pass "Xray policy: Timeouts hardened (handshake=4s, idle=300s)"
    else
        check_fail "Xray policy: Timeouts NOT hardened"
    fi
else
    check_fail "Xray config file not found"
fi

# Check Xray is running
if systemctl is-active --quiet xray; then
    check_pass "Xray service is running"
else
    check_fail "Xray service is NOT running!"
fi

echo ""

# === Layer 4: OS Hardening ===
echo "--- Layer 4: OS Hardening ---"

# Check sysctl
if [ "$(sysctl -n net.ipv4.tcp_syncookies 2>/dev/null)" = "1" ]; then
    check_pass "SYN cookies enabled"
else
    check_fail "SYN cookies NOT enabled"
fi

if [ "$(sysctl -n net.ipv4.conf.all.rp_filter 2>/dev/null)" = "1" ]; then
    check_pass "Reverse path filtering enabled"
else
    check_warn "Reverse path filtering not set"
fi

# Check fail2ban
if systemctl is-active --quiet fail2ban; then
    check_pass "fail2ban is running"
    BANNED=$(fail2ban-client status sshd 2>/dev/null | grep "Currently banned" | awk '{print $NF}' || echo "0")
    echo "         Currently banned IPs (SSH): ${BANNED:-0}"
else
    check_fail "fail2ban is NOT running"
fi

# Check SSH hardening
if [ -f /etc/ssh/sshd_config.d/99-vpn-hardening.conf ]; then
    check_pass "SSH hardening config present"
else
    check_warn "SSH hardening config not found"
fi

echo ""

# === Layer 5: Monitoring ===
echo "--- Layer 5: Monitoring ---"

for script in vpn-monitor-smtp vpn-monitor-connections vpn-check-spamhaus; do
    if [ -x "/usr/local/bin/$script" ]; then
        check_pass "Monitor script installed: $script"
    else
        check_fail "Monitor script MISSING: $script"
    fi
done

if crontab -l 2>/dev/null | grep -q "vpn-monitor"; then
    check_pass "Monitoring cron jobs configured"
else
    check_fail "Monitoring cron jobs NOT configured"
fi

echo ""

# === Summary ===
echo "============================================================"
echo "  VERIFICATION SUMMARY"
echo "  ✅ Passed: $PASS"
echo "  ❌ Failed: $FAIL"
echo "  ⚠️  Warnings: $WARN"
echo "============================================================"

if [ "$FAIL" -gt 0 ]; then
    echo "  RESULT: ❌ SOME CHECKS FAILED — review above"
    exit 1
else
    echo "  RESULT: ✅ ALL CRITICAL CHECKS PASSED"
    exit 0
fi
