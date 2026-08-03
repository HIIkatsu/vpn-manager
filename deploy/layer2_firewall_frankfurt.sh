#!/usr/bin/env bash
# =============================================================================
# Layer 2: Firewall — Frankfurt (Edge Node)
# Default-DROP policy with whitelist for required services
# =============================================================================
set -euo pipefail

SERVER_ROLE="frankfurt"
HELSINKI_IP="150.251.152.174"

echo "========================================="
echo "  Layer 2: Firewall — Frankfurt"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================="

# --- Install nftables if not present ---
echo "[1/4] Installing nftables..."
apt-get install -y nftables >/dev/null 2>&1 || true

# --- Configure nftables ---
echo "[2/4] Configuring nftables firewall..."

cat > /etc/nftables.conf << NFTEOF
#!/usr/sbin/nft -f
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

    # Blocked IPs set
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

        # --- SSH (rate-limited) ---
        tcp dport 22 ct state new \
            add @rate_ssh_v4 { ip saddr limit rate 4/minute burst 6 packets } accept

        # --- VPN Port 443 ---
        tcp dport 443 accept
        udp dport 443 accept

        # --- EU-specific Xray ports ---
        tcp dport 8446 accept
        udp dport 8446 accept
        tcp dport 10444 accept
        udp dport 10444 accept
        tcp dport 10445 accept
        udp dport 10445 accept
        tcp dport 20443 accept
        udp dport 20443 accept
        tcp dport 30443 accept
        udp dport 30443 accept
        tcp dport 40443 accept
        udp dport 40443 accept

        # --- Node Receiver: ONLY from Helsinki ---
        ip saddr ${HELSINKI_IP} tcp dport 8090 accept

        # --- Log & drop everything else ---
        limit rate 5/minute burst 10 packets log prefix "[NFT-INPUT-DROP] " counter drop
        counter drop
    }

    chain forward {
        type filter hook forward priority 10; policy drop;
    }

    chain output {
        type filter hook output priority 10; policy accept;
    }
}
NFTEOF

# --- Apply and enable ---
echo "[3/4] Applying nftables rules..."
nft -f /etc/nftables.conf

systemctl enable nftables 2>/dev/null || true
systemctl restart nftables 2>/dev/null || true

# Restore Layer 1 iptables rules
echo "[+] Restoring Layer 1 iptables rules..."
iptables-restore < /etc/iptables/rules.v4 2>/dev/null || true
ip6tables-restore < /etc/iptables/rules.v6 2>/dev/null || true

# --- Verify ---
echo "[4/4] Verifying firewall..."
echo ""
echo "=== Active nftables ruleset ==="
nft list ruleset | head -40 || true
echo "..."
echo ""

echo "========================================="
echo "  Layer 2 Frankfurt COMPLETE"
echo "========================================="
