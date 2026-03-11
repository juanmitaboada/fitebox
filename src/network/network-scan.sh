#!/bin/bash
# =============================================================================
# FITEBOX Network: Scan WiFi Networks
# Uses NetworkManager (nmcli) - Compatible with RPi4 and RPi5
# Usage: network-scan.sh
# Outputs: JSON array of networks to stdout
# =============================================================================

set -euo pipefail

IFACE="wlan0"

# Trigger fresh scan
nmcli device wifi rescan ifname "${IFACE}" 2>/dev/null || true
sleep 2

# Parse nmcli output into JSON
# Fields: SSID, SIGNAL, SECURITY, FREQ
echo "["

FIRST=true
nmcli -t -f SSID,SIGNAL,SECURITY,FREQ device wifi list ifname "${IFACE}" 2>/dev/null | \
while IFS=: read -r SSID SIGNAL SECURITY FREQ; do
    # Skip empty SSIDs (hidden networks)
    [ -z "${SSID}" ] && continue

    # Skip duplicates (nmcli can show same SSID on different channels)
    # We just output all and let the consumer deduplicate

    if [ "${FIRST}" = true ]; then
        FIRST=false
    else
        echo ","
    fi

    # Determine band from frequency
    BAND="2.4GHz"
    if [ "${FREQ%%.*}" -gt 5000 ] 2>/dev/null; then
        BAND="5GHz"
    fi

    # Clean security string
    SEC="Open"
    case "${SECURITY}" in
        *WPA3*) SEC="WPA3" ;;
        *WPA2*) SEC="WPA2" ;;
        *WPA*)  SEC="WPA" ;;
        *WEP*)  SEC="WEP" ;;
    esac

    printf '  {"ssid": "%s", "signal": "%s", "security": "%s", "band": "%s"}' \
        "${SSID}" "${SIGNAL}" "${SEC}" "${BAND}"
done

echo ""
echo "]"
