#!/bin/bash
# =============================================================================
# FITEBOX Network: WiFi Client Mode
# Uses NetworkManager (nmcli) - Compatible with RPi4 and RPi5
# Usage:
#   network-client.sh                                  → restore previous WiFi
#   network-client.sh <ssid>                           → connect DHCP (open/saved)
#   network-client.sh <ssid> <password>                → connect DHCP + WPA
#   network-client.sh <ssid> <password> <ip> <mask> <gw> <dns>  → static IP
# =============================================================================

set -euo pipefail

SSID="${1:-}"
PASSWORD="${2:-}"
STATIC_IP="${3:-}"
NETMASK="${4:-255.255.255.0}"
GATEWAY="${5:-}"
DNS="${6:-8.8.8.8}"
IFACE="wlan0"
HOTSPOT_CON="fitebox-hotspot"

# --- Helper function ---
write_state() {
    local MODE="$1"
    local IP="${2:-}"
    local CONNECTED_SSID="${3:-}"
    
    cat > /tmp/fitebox_network_state.json << STATEEOF
{
    "mode": "${MODE}",
    "ssid": "${CONNECTED_SSID}",
    "ip": "${IP}",
    "interface": "${IFACE}"
}
STATEEOF
    chmod 644 /tmp/fitebox_network_state.json
}

echo "📶 FITEBOX WiFi Client Mode"

# --- 1. Ensure WiFi radio is on ---
rfkill unblock wifi 2>/dev/null || true
sleep 0.5

# --- 2. Tear down hotspot if active ---
ACTIVE_CON=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep "${IFACE}" | cut -d: -f1 || true)
if [ "${ACTIVE_CON}" = "${HOTSPOT_CON}" ]; then
    echo "⏳ Stopping hotspot..."
    nmcli connection down "${HOTSPOT_CON}" 2>/dev/null || true
    sleep 1
fi

# --- 3. If no SSID given, just re-activate previous connection ---
if [ -z "${SSID}" ]; then
    echo "⏳ Restoring previous WiFi connection..."
    
    # Let NetworkManager auto-connect
    nmcli device set "${IFACE}" autoconnect yes
    nmcli device connect "${IFACE}" 2>/dev/null || true
    
    # Wait for connection (max 15s)
    for i in $(seq 1 15); do
        IP=$(nmcli -t -f IP4.ADDRESS device show "${IFACE}" 2>/dev/null | head -1 | cut -d: -f2 | cut -d/ -f1)
        if [ -n "${IP}" ] && [ "${IP}" != "" ]; then
            echo "✅ Connected: ${IP}"
            write_state "client" "${IP}"
            exit 0
        fi
        sleep 1
    done
    
    echo "⚠️  Could not restore previous connection"
    exit 1
fi

# --- 4. Connect to specified SSID ---
echo "⏳ Connecting to: ${SSID}"

# Build nmcli connect command
if [ -n "${STATIC_IP}" ]; then
    echo "   Mode: Static IP (${STATIC_IP})"
    
    # Check if connection profile already exists
    CON_NAME="fitebox-${SSID}"
    nmcli connection delete "${CON_NAME}" 2>/dev/null || true
    
    # Prefix length from netmask
    PREFIX="24"
    case "${NETMASK}" in
        255.255.255.0)   PREFIX="24" ;;
        255.255.0.0)     PREFIX="16" ;;
        255.0.0.0)       PREFIX="8" ;;
        255.255.255.128) PREFIX="25" ;;
        255.255.255.192) PREFIX="26" ;;
    esac
    
    # Create connection with static IP
    if [ -n "${PASSWORD}" ]; then
        nmcli connection add \
            type wifi \
            ifname "${IFACE}" \
            con-name "${CON_NAME}" \
            ssid "${SSID}" \
            -- \
            wifi-sec.key-mgmt wpa-psk \
            wifi-sec.psk "${PASSWORD}" \
            ipv4.method manual \
            ipv4.addresses "${STATIC_IP}/${PREFIX}" \
            ipv4.gateway "${GATEWAY}" \
            ipv4.dns "${DNS}"
    else
        nmcli connection add \
            type wifi \
            ifname "${IFACE}" \
            con-name "${CON_NAME}" \
            ssid "${SSID}" \
            -- \
            ipv4.method manual \
            ipv4.addresses "${STATIC_IP}/${PREFIX}" \
            ipv4.gateway "${GATEWAY}" \
            ipv4.dns "${DNS}"
    fi
    
    nmcli connection up "${CON_NAME}"
    
else
    echo "   Mode: DHCP"
    
    # Simple connect (DHCP)
    if [ -n "${PASSWORD}" ]; then
        nmcli device wifi connect "${SSID}" password "${PASSWORD}" ifname "${IFACE}"
    else
        nmcli device wifi connect "${SSID}" ifname "${IFACE}"
    fi
fi

# --- 5. Wait for IP ---
echo "⏳ Waiting for IP..."
for i in $(seq 1 15); do
    IP=$(nmcli -t -f IP4.ADDRESS device show "${IFACE}" 2>/dev/null | head -1 | cut -d: -f2 | cut -d/ -f1)
    if [ -n "${IP}" ] && [ "${IP}" != "" ]; then
        echo "✅ Connected to ${SSID}"
        echo "   IP: ${IP}"
        write_state "client" "${IP}" "${SSID}"
        exit 0
    fi
    sleep 1
done

echo "❌ Connection failed - no IP obtained"
exit 1
