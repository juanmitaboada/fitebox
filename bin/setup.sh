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
# i2c-tools is needed for i2cdetect (OLED detection / diagnostics)
apt install -y curl wget git build-essential v4l-utils alsa-utils bc jq i2c-tools

# === 2. Docker Installation ===
if ! [ -x "$(command -v docker)" ]; then
    echo "[2/10] Installing Docker..."
    curl -sSL https://get.docker.com | sh
else
    echo "[2/10] Docker is already installed."
fi
usermod -aG docker "$REAL_USER"
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

# --- Kernel command line (cmdline.txt) -------------------------------------
# cmdline.txt MUST stay a SINGLE line, and we must NEVER touch root=/PARTUUID
# (it is unique to each SD card / SSD). We only append keys that are missing,
# so re-running this script is safe and never duplicates parameters.
CMDLINE_PATH="/boot/firmware/cmdline.txt"
[ ! -f "$CMDLINE_PATH" ] && CMDLINE_PATH="/boot/cmdline.txt"

# add_cmdline_param <grep-guard> <string-to-append-if-guard-not-found>
add_cmdline_param() {
    if ! grep -q -- "$1" "$CMDLINE_PATH"; then
        sed -i "1s|\$| $2|" "$CMDLINE_PATH"
        echo "   cmdline.txt += $2"
    else
        echo "   cmdline.txt already has '$1', skipping."
    fi
}

# Cgroups: Docker needs memory cgroups to enforce per-container limits
add_cmdline_param "cgroup_enable=memory" "cgroup_enable=cpuset cgroup_enable=memory cgroup_memory=1"
# Pin HDMI output to 1080p60: the display daemon owns the HDMI port for status
# screens; without a fixed mode the resolution is auto-negotiated (may be wrong/blank)
add_cmdline_param "video=" "video=HDMI-A-1:1920x1080M@60"
# Hide the kernel boot logos (clean appliance splash)
add_cmdline_param "logo.nologo" "logo.nologo"
# Disable the blinking text cursor on the console
add_cmdline_param "vt.global_cursor_default" "vt.global_cursor_default=0"

# === 5. Hardware Interfaces & RPi 5 Specifics ===
MODEL=$(cat /proc/device-tree/model)
echo "[5/10] Configuring hardware interfaces for $MODEL..."

# Enable I2C for the SSD1306 OLED (address 0x3c). do_i2c 0 = ENABLE.
# Previously this had to be done by hand via raspi-config (README 5.2).
raspi-config nonint do_i2c 0 || true

# Boot to Console (login required - no autologin for security)
raspi-config nonint do_boot_behaviour B1 || true

CONFIG_TXT="/boot/firmware/config.txt"
[ ! -f "$CONFIG_TXT" ] && CONFIG_TXT="/boot/config.txt"

if [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then
    # PCIe Gen 3 for faster NVMe SSD throughput
    if ! grep -q "^dtparam=pciex1_gen=3" "$CONFIG_TXT"; then
        echo "dtparam=pciex1_gen=3" >> "$CONFIG_TXT"
        echo "   config.txt += dtparam=pciex1_gen=3"
    fi

    # Force the 4K page-size kernel for software compatibility.
    # The Pi 5 boots kernel_2712.img (16K pages) by default; some components in
    # the FFmpeg / V4L2 capture / Python stack misbehave under 16K pages.
    # kernel8.img is the generic ARM64 kernel and uses 4K pages.
    # The trailing [all] header guarantees the directive applies to every model
    # regardless of the preceding section, and as it is the last thing in the
    # file it does not alter the context of anything else.
    # Verify after reboot with: getconf PAGESIZE  (4096 = OK, 16384 = wrong)
    if ! grep -q "^kernel=kernel8.img" "$CONFIG_TXT"; then
        printf '\n# FITEBOX: force 4K page-size kernel for software compatibility\n[all]\nkernel=kernel8.img\n' >> "$CONFIG_TXT"
        echo "   config.txt += kernel=kernel8.img (4K page size)"
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

# Add to i2c group so the user can talk to the OLED bus without root
getent group i2c >/dev/null && usermod -aG i2c "$REAL_USER" || true

# === 8. File System Structure ===
echo "[8/10] Creating recording directories..."
mkdir -p ./recordings ./log ./run
chown -R "$REAL_USER:$REAL_USER" ./recordings ./log ./run
chmod -R 2775 ./recordings ./log ./run

# === 9. TLS Certificates ===
echo "[9/10] 🔐 Generating self-signed certificates for Fitebox..."

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
echo "  - I2C enabled (OLED), 4K page-size kernel forced (RPi 5)"
echo "  - HDMI pinned to 1080p60, clean console (no logo/cursor)"
echo "  - System optimizations applied"
echo ""
echo "  ⚠️  A reboot is REQUIRED to apply Kernel, PCIe and cmdline changes."
echo "  🔥🔥🔥 PLEASE REBOOT!!! 🔥🔥🔥"
echo "----------------------------------------------------"
