#!/bin/bash
# ===========================
# FITEBOX MASTER SETUP SCRIPT
# ===========================

set -e

# Detect the real user (the one who invoked sudo)
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
else
    echo "❌ ERROR: This script must be run with sudo"
    echo "Usage: sudo $0"
    exit 1
fi

echo "---------------------------------------------------"
echo "  🚀 Starting FITEBOX Setup"
echo "  👤 Installing for user: $REAL_USER"
echo "---------------------------------------------------"

# === 1. Update System & Core Dependencies ===
echo "[1/10] Updating system and installing base tools..."
apt update && apt upgrade -y
apt install -y curl wget git build-essential v4l-utils alsa-utils bc jq

# === 2. Docker Installation ===
if ! [ -x "$(command -v docker)" ]; then
    echo "[2/10] Installing Docker..."
    curl -sSL https://get.docker.com | sh
    usermod -aG docker "$REAL_USER"
else
    echo "[2/10] Docker is already installed."
fi
apt install -y docker-compose-plugin

# === 3. Audio & Service Optimization (Disable Pulse/Pipewire) ===
echo "[3/10] Disabling Desktop Audio Services (PulseAudio/PipeWire)..."
# Masking is the most aggressive way to prevent them from starting
systemctl --global mask pulseaudio.service pulseaudio.socket 2>/dev/null || true
systemctl --global mask pipewire pipewire.socket pipewire-pulse pipewire-pulse.socket wireplumber 2>/dev/null || true

# Prevent PulseAudio auto-spawn
mkdir -p /etc/pulse
cat > /etc/pulse/client.conf <<EOF
autospawn = no
daemon-binary = /bin/true
EOF

# === 4. Hardware & Kernel Optimization ===
echo "[4/10] Optimizing USB, Kernel and Cgroups..."

# USB Buffer (Critical for 4K/High Bandwidth Video)
cat > /etc/modprobe.d/fitebox-usb.conf <<EOF
options usbcore usbfs_memory_mb=1000
EOF

# Kernel parameters (Swap, Latency, File limits)
cat > /etc/sysctl.d/99-fitebox.conf <<EOF
vm.swappiness=10
vm.dirty_ratio=10
vm.dirty_background_ratio=5
kernel.sched_rt_runtime_us=-1
EOF
sysctl --system > /dev/null

# File limits (to allow more open files for video processing and recording)
cat > /etc/security/limits.d/fitebox.conf <<EOF
$REAL_USER soft nofile 65536
$REAL_USER hard nofile 65536
$REAL_USER soft nproc 32768
$REAL_USER hard nproc 32768
EOF

# Cgroups (Docker memory management)
CMDLINE_PATH="/boot/firmware/cmdline.txt"
[ ! -f "$CMDLINE_PATH" ] && CMDLINE_PATH="/boot/cmdline.txt"
if ! grep -q "cgroup_enable=memory" "$CMDLINE_PATH"; then
    sed -i '1s/$/ cgroup_enable=cpuset cgroup_enable=memory cgroup_memory=1/' "$CMDLINE_PATH"
    echo "Cgroups parameters added to $CMDLINE_PATH"
else
    echo "Cgroups already enabled. Skipping..."
fi

# === 5. RPi 5 Specifics (PCIe Gen 3 & Boot Mode) ===
MODEL=$(cat /proc/device-tree/model)
echo "[5/10] Configuring $MODEL hardware..."

# Boot to Console (login required - no autologin for security)
raspi-config nonint do_boot_behaviour B1

if [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then
    if ! grep -q "dtparam=pciex1_gen=3" /boot/firmware/config.txt; then
        echo "dtparam=pciex1_gen=3" >> /boot/firmware/config.txt
    fi
fi

# === 6. Plymouth UI Installation ===
echo "[6/10] Installing Fitebox Plymouth theme..."
THEME_NAME="fitebox"
THEME_DEST="/usr/share/plymouth/themes/$THEME_NAME"

# Make directory for the theme (if it doesn't exist)
mkdir -p "$THEME_DEST"

# Copy files from the local plymouth/ directory to the system's Plymouth themes directory
# Assuming the script is run from the project root: ./bin/setup.sh
if [ -d "./plymouth" ]; then
    cp ./plymouth/* "$THEME_DEST/"

    # Register the theme with Plymouth
    plymouth-set-default-theme -R "$THEME_NAME"
    echo "Plymouth theme '$THEME_NAME' installed and set as default."
else
    echo "⚠️  ./plymouth directory not found, skipping theme."
fi

# === 7. Sudoers & Permissions ===
echo "[7/10] Configuring permissions for user '$REAL_USER'..."
cat > /etc/sudoers.d/010_fitebox-permissions <<EOF
$REAL_USER ALL=(ALL) NOPASSWD: /usr/sbin/reboot
$REAL_USER ALL=(ALL) NOPASSWD: /usr/sbin/shutdown
$REAL_USER ALL=(ALL) NOPASSWD: /usr/bin/plymouth display-message *
EOF
chmod 440 /etc/sudoers.d/010_fitebox-permissions

# Add to i2c if needed for future OLED
getent group i2c >/dev/null && usermod -aG i2c "$REAL_USER" || true

# === 8. File System Structure ===
echo "[8/10] Creating recording directories..."
mkdir -p ./recordings ./log ./run
chown -R "$REAL_USER:$REAL_USER" ./recordings ./log ./run
sudo chmod -R 2775 ./recordings ./log ./run

# === 9. File System Structure ===
echo "[9/11] 🔐 Generate self-signed certificates for Fitebox..."

# Make certs directory if it doesn't exist
mkdir -p certs

# Generate a self-signed certificate (valid for 10 years to avoid maintenance)
openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout certs/fitebox.key \
  -out certs/fitebox.crt \
  -subj "/C=ES/ST=Fitebox/O=Fitebox/CN=fitebox.local"

# Ensure permissions (readable by nginx container)
chmod 644 certs/fitebox.crt
chmod 600 certs/fitebox.key

# === 10. Docker User Permissions Configuration ===
echo "[10/10] Configuring Docker user permissions..."

# Detect UID/GID of the real user and ssh dir
REAL_UID=$(id -u "$REAL_USER")
REAL_GID=$(id -g "$REAL_USER")
SSH_DIR=$(eval echo ~"$REAL_USER")/.ssh

echo "   Detected: $REAL_USER (UID=$REAL_UID, GID=$REAL_GID)"

# Make .env for docker-compose
cat > .env <<EOF
# FITEBOX Docker Environment
# Auto-generated by setup.sh on $(date)
# User: $REAL_USER

# User permissions (so recordings are owned by '$REAL_USER', not root)
USER_UID=$REAL_UID
USER_GID=$REAL_GID
SSH_DIR=$SSH_DIR
EOF

chown "$REAL_USER:$REAL_USER" .env

echo "   ✅ Created .env with USER_UID=$REAL_UID, USER_GID=$REAL_GID"
echo "   Files created by Docker will be owned by: $REAL_USER"

# === Finalizing ===
echo "----------------------------------------------------"
echo "  ✅ Setup Complete!"
echo ""
echo "  📋 Summary:"
echo "  - User: $REAL_USER (UID=$REAL_UID, GID=$REAL_GID)"
echo "  - Docker configured to run as this user"
echo "  - Recordings will be owned by '$REAL_USER'"
echo "  - Audio services (PulseAudio/PipeWire) disabled"
echo "  - System optimizations applied"
echo ""
echo "  ⚠️  A reboot is REQUIRED to apply Kernel and PCIe changes."
echo "  🔥🔥🔥 PLEASE REBOOT!!! 🔥🔥🔥"
echo "----------------------------------------------------"
