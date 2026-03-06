#!/bin/bash
# =============================================================================
# FITEBOX Network: Activate Hotspot (AP mode)
# Uses NetworkManager (nmcli) - Compatible with RPi4 and RPi5
# Usage: network-adhoc.sh <ssid> <password>
# =============================================================================

set -euo pipefail

SSID="${1:-fitebox_ap}"
PASSWORD="${2:-fitebox00}"
IFACE="wlan0"
CON_NAME="fitebox-hotspot"
BAND="bg"          # 2.4GHz (max compat RPi4+RPi5)
CHANNEL="6"
IP_BASE="192.168.4"
IP_ADDR="${IP_BASE}.1/24"

echo "📡 FITEBOX Hotspot Setup"
echo "   SSID:     ${SSID}"
echo "   IFACE:    ${IFACE}"

# --- 1. Ensure WiFi radio is on ---
rfkill unblock wifi 2>/dev/null || true
sleep 0.5

# --- 2. Tear down any existing hotspot/connection on wlan0 ---
echo "⏳ Cleaning existing connections on ${IFACE}..."

# Disconnect current connection on wlan0
nmcli device disconnect "${IFACE}" 2>/dev/null || true

# Delete previous fitebox hotspot profile if exists
nmcli connection delete "${CON_NAME}" 2>/dev/null || true

sleep 1

# --- 3. Create hotspot connection profile ---
echo "⏳ Creating hotspot profile..."

nmcli connection add \
    type wifi \
    ifname "${IFACE}" \
    con-name "${CON_NAME}" \
    autoconnect no \
    ssid "${SSID}" \
    -- \
    wifi.mode ap \
    wifi.band "${BAND}" \
    wifi.channel "${CHANNEL}" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "${PASSWORD}" \
    ipv4.method shared \
    ipv4.addresses "${IP_ADDR}"

# --- 4. Bring up the hotspot ---
echo "⏳ Activating hotspot..."
nmcli connection up "${CON_NAME}"

sleep 2

# --- 5. Verify ---
STATE=$(nmcli -t -f GENERAL.STATE device show "${IFACE}" 2>/dev/null | cut -d: -f2)
IP=$(nmcli -t -f IP4.ADDRESS device show "${IFACE}" 2>/dev/null | head -1 | cut -d: -f2 | cut -d/ -f1)

echo ""
echo "✅ Hotspot active"
echo "   SSID:     ${SSID}"
echo "   Password: ${PASSWORD}"
echo "   IP:       ${IP:-${IP_BASE}.1}"
echo "   State:    ${STATE:-unknown}"

# Write state file for other components to read
cat > /tmp/fitebox_network_state.json << EOF
{
    "mode": "adhoc",
    "ssid": "${SSID}",
    "password": "${PASSWORD}",
    "ip": "${IP:-${IP_BASE}.1}",
    "interface": "${IFACE}",
    "connection": "${CON_NAME}"
}
EOF

chmod 644 /tmp/fitebox_network_state.json

exit 0
