#!/bin/bash
# ===========================
# FITEBOX INSTALL SCRIPT
# ===========================
# Downloads all required files and sets up FITEBOX on a fresh Raspberry Pi 5.
#
# Usage:
#   sudo bash install.sh              # installs latest
#   sudo bash install.sh 1.2          # installs specific version
#   sudo bash install.sh 1.2-rc1      # installs release candidate

set -e

REPO_RAW="https://raw.githubusercontent.com/juanmitaboada/fitebox/main"
INSTALL_DIR="/home/${SUDO_USER:-$USER}/fitebox"
IMAGE_TAG="${1:-latest}"

# --- Root checks ---

if [ -z "$SUDO_USER" ]; then
    echo "❌ ERROR: This script must be run with sudo"
    echo "   Usage: curl -fsSL $REPO_RAW/bin/install.sh -o install.sh && sudo bash install.sh"
    exit 1
fi

echo "---------------------------------------------------"
echo "  🚀 FITEBOX Installer"
echo "  👤 Installing for user: $SUDO_USER"
echo "  📁 Install directory:   $INSTALL_DIR"
echo "  🐳 Image tag:           $IMAGE_TAG"
echo "---------------------------------------------------"

# --- Create install directory structure ---

echo "[1/5] Downloading FITEBOX files..."

mkdir -p "$INSTALL_DIR/bin"
mkdir -p "$INSTALL_DIR/docker/recorder"
chown -R "$SUDO_USER:$SUDO_USER" "$INSTALL_DIR"
cd "$INSTALL_DIR"

curl -fsSL "$REPO_RAW/bin/setup.sh"               -o bin/setup.sh
curl -fsSL "$REPO_RAW/bin/docker-compose.yml"     -o docker-compose.yml
curl -fsSL "$REPO_RAW/docker/recorder/nginx.conf" -o docker/recorder/nginx.conf

chmod +x bin/setup.sh

echo "      ✅ Files downloaded."

# --- Inject image tag into docker-compose.yml ---

sed -i "s|image: docker.io/br0th3r/fitebox:latest|image: docker.io/br0th3r/fitebox:${IMAGE_TAG}|" docker-compose.yml

# --- Run setup ---

echo "[2/5] Running system setup (this may take a few minutes)..."
bash bin/setup.sh
echo "      ✅ System setup complete."

# --- Pull Docker image ---

echo "[3/5] Pulling FITEBOX Docker image (${IMAGE_TAG})..."
sudo docker compose pull
echo "      ✅ Image pulled."

# --- First start (registers restart: unless-stopped policy) ---

echo "[4/5] Starting FITEBOX for the first time..."
sudo docker compose up -d
echo "      ✅ FITEBOX started."

# --- Fix ownership (everything was created as root via sudo) ---
chown -R "$SUDO_USER:$SUDO_USER" "$INSTALL_DIR"

# --- Reboot via FITEBOX socket (shows message on OLED/display) ---

echo "[5/5] Rebooting to apply kernel and PCIe changes..."
echo ""
echo "---------------------------------------------------"
echo "  ✅ Installation complete!"
echo ""
echo "  The system will now reboot."
echo "  After reboot, FITEBOX will start automatically."
echo "  Access the web UI at: https://<your-rpi-ip>"
echo "---------------------------------------------------"
echo ""

sleep 3

if docker exec fitebox-recorder python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('/fitebox/run/fitebox_control.sock')
s.sendall((json.dumps({'type': 'command', 'action': 'system.reboot'}) + '\n').encode())
s.close()
" 2>/dev/null; then
    echo "  🔄 Reboot requested via FITEBOX."
else
    echo "  ⚠️  Could not reach FITEBOX socket, falling back to system reboot..."
    reboot
fi
