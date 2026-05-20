#!/bin/sh
# Container entrypoint for vibe-coding-friendly images.
#
# Portability:
#   - POSIX sh only (runs in bash / dash / busybox ash).
#
# Flow:
#   1) start sshd in background so Cursor/Trae can always attach
#   2) optionally run the business CMD
#        - may be CHAINED through the base image's original ENTRYPOINT
#          by setting ORIG_ENTRYPOINT=/path/to/original-entrypoint.sh
#        - may be skipped entirely (SKIP_CMD=1) for a pure SSH workbox
#   3) stay alive as PID 1 with signal forwarding and zombie reaping
#
# Runtime tunables (docker run -e ...):
#   KEEP_ALIVE=1  (default)  keep container alive regardless of CMD exit
#   KEEP_ALIVE=0             classic docker semantics (exec CMD as PID 1)
#   SKIP_CMD=1               do NOT run the business CMD (SSH-only mode)
#   ORIG_ENTRYPOINT=path     chain to the base image's original entrypoint

set -u

KEEP_ALIVE="${KEEP_ALIVE:-1}"
SKIP_CMD="${SKIP_CMD:-0}"
ORIG_ENTRYPOINT="${ORIG_ENTRYPOINT:-}"

log() { printf '[ssh_plugin/entrypoint] %s\n' "$*"; }

# --------------------------------------------------------------------------- #
# 1) Start sshd in the background.
# --------------------------------------------------------------------------- #
if [ ! -f /etc/ssh/ssh_host_rsa_key ] && [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
    ssh-keygen -A >/dev/null 2>&1 || true
fi
mkdir -p /var/run/sshd
/usr/sbin/sshd

# Print the actual port (drop-in conf or main config), not a guessed one.
actual_port=$(
    { cat /etc/ssh/sshd_config.d/*.conf 2>/dev/null; cat /etc/ssh/sshd_config 2>/dev/null; } \
    | awk 'BEGIN{p=22} /^[[:space:]]*Port[[:space:]]+[0-9]+/ {p=$2} END{print p}'
)
log "sshd started on port ${actual_port}"

# --------------------------------------------------------------------------- #
# 2) Decide whether and how to run the business CMD.
# --------------------------------------------------------------------------- #
have_cmd=0
if [ "$#" -gt 0 ] && [ "$SKIP_CMD" != "1" ]; then
    have_cmd=1
fi

# Helper: run the business CMD, optionally chained through ORIG_ENTRYPOINT.
run_business() {
    if [ -n "$ORIG_ENTRYPOINT" ] && [ -x "$ORIG_ENTRYPOINT" ]; then
        "$ORIG_ENTRYPOINT" "$@"
    else
        "$@"
    fi
}

# --------------------------------------------------------------------------- #
# 2a) Classic docker semantics: exec the CMD as PID 1; container dies with CMD.
# --------------------------------------------------------------------------- #
if [ "$KEEP_ALIVE" = "0" ]; then
    if [ "$have_cmd" -eq 0 ]; then
        log "KEEP_ALIVE=0 but no CMD given; falling back to idle wait (SSH stays up)"
        exec tail -f /dev/null
    fi
    log "KEEP_ALIVE=0: exec business CMD: $*"
    if [ -n "$ORIG_ENTRYPOINT" ] && [ -x "$ORIG_ENTRYPOINT" ]; then
        exec "$ORIG_ENTRYPOINT" "$@"
    else
        exec "$@"
    fi
fi

# --------------------------------------------------------------------------- #
# 2b) Default (KEEP_ALIVE=1): business in background, PID 1 stays alive.
# --------------------------------------------------------------------------- #
child_pid=""

cleanup() {
    log "received signal, shutting down ..."
    if [ -f /var/run/sshd.pid ]; then
        kill -TERM "$(cat /var/run/sshd.pid)" 2>/dev/null || true
    fi
    if [ -n "$child_pid" ]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup TERM INT HUP

if [ "$have_cmd" -eq 1 ]; then
    log "running business CMD in background: $*"
    (
        run_business "$@"
        rc=$?
        printf '[ssh_plugin/entrypoint] business CMD exited with code %s; SSH remains available\n' "$rc"
    ) &
    child_pid=$!
else
    log "no business CMD (SSH-only mode)"
fi

# --------------------------------------------------------------------------- #
# 3) PID 1 main loop: reap zombies (orphaned grandchildren land on us),
#    wake periodically so signals (TERM/INT/HUP) can fire the trap.
# --------------------------------------------------------------------------- #
while :; do
    wait 2>/dev/null || true
    # `sleep` as a backgrounded job so `wait` is interruptible by signals.
    sleep 3600 &
    wait "$!" 2>/dev/null || true
done
