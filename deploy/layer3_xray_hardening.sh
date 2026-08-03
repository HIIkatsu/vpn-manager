#!/usr/bin/env bash
# =============================================================================
# Layer 3: Xray Core Hardening
# Adds SMTP/BitTorrent blocks at routing level, tightens timeouts, adds policy
# Works on ALL server roles (detects config automatically)
# =============================================================================
set -euo pipefail

XRAY_CONFIG="/usr/local/etc/xray/config.json"
XRAY_BACKUP="/usr/local/etc/xray/config.json.backup.$(date +%s)"

echo "========================================="
echo "  Layer 3: Xray Core Hardening"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================="

if [ ! -f "$XRAY_CONFIG" ]; then
    echo "ERROR: Xray config not found at $XRAY_CONFIG"
    exit 1
fi

# --- Backup current config ---
echo "[1/4] Backing up current config..."
cp "$XRAY_CONFIG" "$XRAY_BACKUP"
echo "  Backup: $XRAY_BACKUP"

# --- Apply hardening via Python (JSON manipulation) ---
echo "[2/4] Applying Xray config hardening..."

python3 << 'PYEOF'
import json
import sys
import copy

CONFIG_PATH = "/usr/local/etc/xray/config.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

changed = False

# =============================================
# 1. Add/update POLICY section (timeouts, stats)
# =============================================
policy = config.get("policy", {})
levels = policy.get("levels", {})

# Level 0 = default for all users
level_0 = levels.get("0", {})
hardened_level_0 = {
    "handshake": 4,           # 4s handshake timeout (kills slow scanners)
    "connIdle": 300,          # 5min idle timeout
    "uplinkOnly": 2,          # Close 2s after downlink ends
    "downlinkOnly": 5,        # Close 5s after uplink ends
    "statsUserUplink": True,  # Per-user traffic stats
    "statsUserDownlink": True,
    "bufferSize": 4           # 4KB buffer (saves RAM under load)
}

for k, v in hardened_level_0.items():
    if level_0.get(k) != v:
        level_0[k] = v
        changed = True

levels["0"] = level_0
policy["levels"] = levels

# System-level stats
system_policy = policy.get("system", {})
for k in ["statsInboundUplink", "statsInboundDownlink", "statsOutboundUplink", "statsOutboundDownlink"]:
    if not system_policy.get(k):
        system_policy[k] = True
        changed = True
policy["system"] = system_policy
config["policy"] = policy

# =============================================
# 2. Ensure "stats" section exists (required for stats to work)
# =============================================
if "stats" not in config:
    config["stats"] = {}
    changed = True

# =============================================
# 3. Add ROUTING rules to block SMTP and BitTorrent
# =============================================
routing = config.get("routing", {})
if "domainStrategy" not in routing:
    routing["domainStrategy"] = "AsIs"
rules = routing.get("rules", [])

# Check if SMTP block rule already exists
has_smtp_block = any(
    r.get("outboundTag") == "block" and "25" in str(r.get("port", ""))
    for r in rules
)

if not has_smtp_block:
    # Insert SMTP block at the BEGINNING of rules (highest priority)
    smtp_rule = {
        "type": "field",
        "outboundTag": "block",
        "port": "25,465,587,2525",
        "network": "tcp"
    }
    rules.insert(0, smtp_rule)
    changed = True
    print("  + Added SMTP block routing rule (ports 25,465,587,2525)")

# Check if BitTorrent block rule already exists
has_bt_block = any(
    r.get("outboundTag") == "block" and "bittorrent" in str(r.get("protocol", []))
    for r in rules
)

if not has_bt_block:
    bt_rule = {
        "type": "field",
        "outboundTag": "block",
        "protocol": ["bittorrent"]
    }
    rules.insert(0, bt_rule)
    changed = True
    print("  + Added BitTorrent block routing rule")

# Block common spam/abuse ports
has_abuse_block = any(
    r.get("outboundTag") == "block" and "6667" in str(r.get("port", ""))
    for r in rules
)

if not has_abuse_block:
    abuse_rule = {
        "type": "field",
        "outboundTag": "block",
        "port": "6667,6668,6669,6697,7000,7001,194",
        "network": "tcp"
    }
    rules.insert(0, abuse_rule)
    changed = True
    print("  + Added IRC/abuse ports block routing rule")

routing["rules"] = rules
config["routing"] = routing

# =============================================
# 4. Ensure "block" outbound exists
# =============================================
outbounds = config.get("outbounds", [])
has_block_outbound = any(o.get("tag") == "block" for o in outbounds)
if not has_block_outbound:
    outbounds.append({
        "tag": "block",
        "protocol": "blackhole",
        "settings": {
            "response": {"type": "none"}
        }
    })
    config["outbounds"] = outbounds
    changed = True
    print("  + Added 'block' blackhole outbound")

# =============================================
# 5. Enable sniffing on all VLESS inbounds
# =============================================
inbounds = config.get("inbounds", [])
for ib in inbounds:
    if ib.get("protocol") == "vless":
        sniffing = ib.get("sniffing", {})
        if not sniffing.get("enabled"):
            sniffing["enabled"] = True
            sniffing["destOverride"] = ["http", "tls", "quic"]
            ib["sniffing"] = sniffing
            changed = True
            print(f"  + Enabled sniffing on inbound '{ib.get('tag', 'unknown')}'")

config["inbounds"] = inbounds

# =============================================
# 6. Write config
# =============================================
if changed:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print("\n  Config updated successfully.")
else:
    print("\n  Config already hardened, no changes needed.")

sys.exit(0)
PYEOF

# --- Validate config ---
echo "[3/4] Validating Xray config..."
if /usr/local/bin/xray run -test -config "$XRAY_CONFIG" 2>&1; then
    echo "  ✅ Config validation passed"
else
    echo "  ❌ Config validation FAILED! Restoring backup..."
    cp "$XRAY_BACKUP" "$XRAY_CONFIG"
    echo "  Backup restored. Original config is active."
    exit 1
fi

# --- Restart Xray ---
echo "[4/4] Restarting Xray service..."
systemctl restart xray

sleep 2

if systemctl is-active --quiet xray; then
    echo "  ✅ Xray restarted successfully"
else
    echo "  ❌ Xray failed to start! Restoring backup..."
    cp "$XRAY_BACKUP" "$XRAY_CONFIG"
    systemctl restart xray
    echo "  Backup restored. Check logs: journalctl -u xray -n 50"
    exit 1
fi

echo ""
echo "========================================="
echo "  Layer 3 COMPLETE"
echo "  - SMTP blocked at Xray routing level"
echo "  - BitTorrent blocked"
echo "  - IRC/abuse ports blocked"
echo "  - Handshake timeout: 4s"
echo "  - Idle timeout: 300s"
echo "  - Sniffing enabled on all VLESS inbounds"
echo "  - Per-user traffic stats enabled"
echo "========================================="
