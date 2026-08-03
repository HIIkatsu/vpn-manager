#!/usr/bin/env bash
# =============================================================================
# Layer 2: Firewall — Helsinki (Main Node)
# Default-DROP policy with whitelist for required services
# =============================================================================
set -euo pipefail

SERVER_ROLE="helsinki"
EDGE_MOSCOW="132.243.230.173"
EDGE_FRANKFURT="132.243.194.119"
EDGE_AMSTERDAM="194.50.94.177"

echo "========================================="
echo "  Layer 2: Firewall — Helsinki"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================="

# --- Install nftables if not present ---
echo "[1/4] Installing nftables..."
apt-get install -y nftables >/dev/null 2>&1 || true

# --- Flush existing nftables rules (keep iptables for Docker) ---
echo "[2/4] Configuring nftables firewall..."

cat > /etc/nftables.conf << 'NFTEOF'
#!/usr/sbin/nft -f
# Safely replace only our table
table inet vpn_firewall
delete table inet vpn_firewall

table inet vpn_firewall {
    # Rate limit tracking sets
    set rate_ssh_v4 {
        type ipv4_addr
        flags dynamic,timeout
        timeout 120s
    }

    set rate_vpn_v4 {
        type ipv4_addr
        flags dynamic,timeout
        timeout 10s
    }

    set rate_ssh_v6 {
        type ipv6_addr
        flags dynamic,timeout
        timeout 120s
    }

    set rate_vpn_v6 {
        type ipv6_addr
        flags dynamic,timeout
        timeout 10s
    }

    # Blocked IPs (auto-populated by monitoring)
    set blocklist_v4 {
        type ipv4_addr
        flags dynamic,timeout
        timeout 3600s
    }

    chain input {
        type filter hook input priority 10; policy drop;

        # --- Fast path: established connections ---
        ct state established,related accept
        ct state invalid drop

        # --- Loopback ---
        iif "lo" accept

        # --- Blocked IPs ---
        ip saddr @blocklist_v4 counter drop

        # --- ICMP (rate-limited) ---
        ip protocol icmp limit rate 5/second burst 10 packets accept
        ip6 nexthdr icmpv6 limit rate 5/second burst 10 packets accept

        # --- SSH (rate-limited: 4 new connections per minute per IP) ---
        tcp dport 22 ct state new ip saddr != 127.0.0.0/8 \
            add @rate_ssh_v4 { ip saddr limit rate 4/minute burst 6 packets } accept
        tcp dport 22 ct state new ip6 saddr != ::1 \
            add @rate_ssh_v6 { ip6 saddr limit rate 4/minute burst 6 packets } accept

        # --- HTTP (for Let's Encrypt / redirects) ---
        tcp dport 80 accept

        # --- VPN Main Port 443 ---
        tcp dport 443 accept
        udp dport 443 accept

        # --- WARP/Redirect Xray ports (used by some client configs) ---
        tcp dport 20443 accept
        udp dport 20443 accept
        tcp dport 30443 accept
        udp dport 30443 accept
        tcp dport 40443 accept
        udp dport 40443 accept

        # --- Edge nodes → Helsinki (sync receiver, if any future use) ---
        ip saddr $EDGE_MOSCOW tcp dport 8001 accept
        ip saddr $EDGE_FRANKFURT tcp dport 8001 accept
        ip saddr $EDGE_AMSTERDAM tcp dport 8001 accept

        # --- Docker internal (don't break container networking) ---
        iifname "docker0" accept
        iifname "br-*" accept

        # --- Log & drop everything else (rate-limited logging) ---
        limit rate 5/minute burst 10 packets log prefix "[NFT-INPUT-DROP] " counter drop
        counter drop
    }

    chain forward {
        type filter hook forward priority 10; policy accept;

        # Allow Docker forwarding
        iifname "docker0" accept
        oifname "docker0" accept
        iifname "br-*" accept
        oifname "br-*" accept
    }

    chain output {
        type filter hook output priority 10; policy accept;
        # Output is mostly open (SMTP blocking done via iptables in Layer 1)
    }
}
NFTEOF

# Replace variable references with actual IPs in the nftables config
sed -i "s/\$EDGE_MOSCOW/$EDGE_MOSCOW/g" /etc/nftables.conf
sed -i "s/\$EDGE_FRANKFURT/$EDGE_FRANKFURT/g" /etc/nftables.conf
sed -i "s/\$EDGE_AMSTERDAM/$EDGE_AMSTERDAM/g" /etc/nftables.conf

# --- Apply and enable ---
echo "[3/4] Applying nftables rules..."
nft -f /etc/nftables.conf

systemctl enable nftables 2>/dev/null || true
systemctl restart nftables 2>/dev/null || true

# Restore Docker NAT rules if they were accidentally flushed previously
echo "[+] Ensuring Docker iptables rules are intact..."
systemctl restart docker 2>/dev/null || true
# Restore Layer 1 iptables rules
echo "[+] Restoring Layer 1 iptables rules..."
iptables-restore < /etc/iptables/rules.v4 2>/dev/null || true
ip6tables-restore < /etc/iptables/rules.v6 2>/dev/null || true

# --- Verify ---
echo "[4/4] Verifying firewall..."
echo ""
echo "=== Active nftables ruleset ==="
nft list ruleset | head -40 || true
echo ""
echo "=== Listening ports still exposed ==="
ss -tulpn | grep -E "0\.0\.0\.0|[::]" | grep -v "127\." | grep -v "docker" || echo "(none unexpected)"
echo ""

echo "========================================="
echo "  Layer 2 Helsinki COMPLETE"
echo "  Default DROP policy active"
echo "  Allowed: 22(ssh), 80(http), 443/20443/30443/40443(vpn)"
echo "========================================="
