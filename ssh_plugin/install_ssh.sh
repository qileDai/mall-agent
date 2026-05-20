#!/bin/sh
# Install & configure OpenSSH server inside a container image.
#
# Portability:
#   - POSIX sh only (runs in bash / dash / busybox ash).
#   - Package managers: apt-get, apk, dnf, yum, zypper.
#   - Tolerates minimal images (busybox passwd fallback when no chpasswd).
#   - Idempotent: short-circuits when sshd is already installed.
#
# Env tunables (build-time):
#   SSH_PASSWORD   root password to set (default: "password")
#   SSH_PORT       sshd listen port     (default: 22)
#
# NOTE: must run as root.

set -eu

SSH_PASSWORD="${SSH_PASSWORD:-password}"
SSH_PORT="${SSH_PORT:-22}"

log() { printf '[ssh_plugin/install] %s\n' "$*"; }

# --------------------------------------------------------------------------- #
# 1) Install openssh-server (and friends) if not already present.
# --------------------------------------------------------------------------- #
if command -v sshd >/dev/null 2>&1; then
    log "sshd already present, skipping package installation"
else
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y --no-install-recommends openssh-server ca-certificates wget curl
        rm -rf /var/lib/apt/lists/*

    elif command -v apk >/dev/null 2>&1; then
        # alpine: `shadow` provides chpasswd/usermod; openssh-keygen for ssh-keygen -A
        apk add --no-cache openssh openssh-keygen shadow ca-certificates wget curl

    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y openssh-server openssh-clients wget curl
        dnf clean all

    elif command -v yum >/dev/null 2>&1; then
        yum install -y openssh-server openssh-clients wget curl
        yum clean all

    elif command -v zypper >/dev/null 2>&1; then
        zypper --non-interactive install openssh wget curl
        zypper clean -a || true

    else
        log "ERROR: no supported package manager (apt/apk/dnf/yum/zypper); cannot install sshd" >&2
        exit 1
    fi
fi

# --------------------------------------------------------------------------- #
# 2) Ensure runtime directories exist.
# --------------------------------------------------------------------------- #
mkdir -p /var/run/sshd /root/.ssh
chmod 700 /root/.ssh

# --------------------------------------------------------------------------- #
# 3) Set root password (with fallback for busybox-only images).
# --------------------------------------------------------------------------- #
if command -v chpasswd >/dev/null 2>&1; then
    printf 'root:%s\n' "${SSH_PASSWORD}" | chpasswd
elif command -v passwd >/dev/null 2>&1; then
    # busybox passwd reads new password twice from stdin
    printf '%s\n%s\n' "${SSH_PASSWORD}" "${SSH_PASSWORD}" | passwd root >/dev/null
else
    log "WARNING: neither chpasswd nor passwd available; root password NOT set" >&2
fi

# --------------------------------------------------------------------------- #
# 4) Write a drop-in sshd config.
#    More robust than sed-ing /etc/ssh/sshd_config directly:
#    - works even if the option isn't present in the main file
#    - survives distro upgrades
#    - ensures main config loads drop-ins on older distros that don't by default
# --------------------------------------------------------------------------- #
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-ssh-plugin.conf <<EOF
# Managed by ssh_plugin/install_ssh.sh -- do not edit by hand.
Port ${SSH_PORT}
PermitRootLogin yes
PasswordAuthentication yes
PubkeyAuthentication yes
UsePAM no
EOF
chmod 644 /etc/ssh/sshd_config.d/99-ssh-plugin.conf

if [ -f /etc/ssh/sshd_config ]; then
    if ! grep -qE '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config; then
        printf '\n# added by ssh_plugin\nInclude /etc/ssh/sshd_config.d/*.conf\n' >> /etc/ssh/sshd_config
    fi
fi

# --------------------------------------------------------------------------- #
# 5) Generate host keys (noop if they already exist).
# --------------------------------------------------------------------------- #
ssh-keygen -A >/dev/null 2>&1 || true

log "done: openssh-server ready; root password set, listening on port ${SSH_PORT}"
