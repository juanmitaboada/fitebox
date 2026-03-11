# ========================================================
# UNIVERSAL FITEBOX RECORDER DOCKERFILE
# ========================================================
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# Basic dependencies + diagnostic tools
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    procps \
    bc \
    jq \
    usbutils \
    pciutils \
    v4l-utils \
    alsa-utils \
    i2c-tools \
    net-tools \
    iproute2 \
    vim-nox \
    network-manager \
    && rm -rf /var/lib/apt/lists/*

# Raspberry Pi repository
RUN curl -fsSL http://archive.raspberrypi.org/debian/raspberrypi.gpg.key | gpg --dearmor -o /etc/apt/trusted.gpg.d/raspberrypi.gpg \
    && echo "deb http://archive.raspberrypi.org/debian/ bookworm main" > /etc/apt/sources.list.d/raspi.list

# Docker CLI + Compose (for self-update) + Git
RUN curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y \
        docker-ce-cli \
        docker-compose-plugin \
        git \
    && rm -rf /var/lib/apt/lists/*

# Main dependencies
RUN apt-get update && apt-get install -y \
    supervisor \
    ffmpeg \
    mkvtoolnix \
    plymouth \
    libplymouth5 \
    fonts-dejavu-core \
    iputils-ping \
    iproute2 \
    curl \
    iw \
    fontconfig \
    python3 \
    python3-pip \
    python3-dev \
    python3-numpy \
    gcc \
    gpiod \
    libgpiod2 \
    libgpiod-dev \
    libraspberrypi-bin \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libopenjp2-7-dev \
    libtiff5-dev \
    libusb-1.0-0-dev \
    libportaudio2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python requirements
RUN pip3 install --break-system-packages pip-tools
COPY src/ /app/
COPY src/requirements.txt ./
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Create runtime directories
RUN mkdir -p /recordings /tmp /fitebox/run /fitebox/log /app /tests

# Copy project files
COPY docker/recorder/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/recorder/entrypoint.sh /entrypoint.sh

# Set Build Date
# RUN date +%d%m%y > /app/BUILD_DATE
RUN date +%y%m%d%H%M%S > /app/BUILD_DATE

# Set permissions
RUN chmod +x /entrypoint.sh && \
    chmod +x /app/*.sh 2>/dev/null || true

# Set build mode (default to local for development)
ARG FITEBOX_BUILD_MODE=local
LABEL fitebox.build.mode="${FITEBOX_BUILD_MODE}"
RUN echo "${FITEBOX_BUILD_MODE}" > /app/BUILD_MODE

# Set safe directory for Git operations
RUN git config --global --add safe.directory /fitebox/project

ENTRYPOINT ["/entrypoint.sh"]
