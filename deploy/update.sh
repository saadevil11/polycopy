#!/usr/bin/env bash
#
# polybotshadow — one-command update (run ON THE BOX as your normal user, e.g.
# `ubuntu` — NOT with sudo; the script sudo's only the steps that need it).
#
#   ./deploy/update.sh
#
# It pulls the latest code, rebuilds, and only if the build SUCCEEDS swaps the
# binary and restarts the service. A failed build leaves the running bot
# untouched. Persistent state in $INSTALL_DIR/data survives the restart.
#
# Overridable via env: REPO_DIR, INSTALL_DIR, SERVICE.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/polybotshadow}"
INSTALL_DIR="${INSTALL_DIR:-/opt/polybotshadow}"
SERVICE="${SERVICE:-polybotshadow}"

cd "$REPO_DIR"

# Make sure cargo is on PATH (rustup installs it here).
if [ -f "$HOME/.cargo/env" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi

echo "==> git pull (fast-forward only)"
git pull --ff-only

echo "==> now at $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"

echo "==> cargo build --release   (a failed build stops here; the running bot is untouched)"
cargo build --release

echo "==> swapping binary + restarting $SERVICE"
sudo systemctl stop "$SERVICE"
sudo cp "$REPO_DIR/target/release/polybotshadow" "$INSTALL_DIR/polybotshadow"
sudo systemctl start "$SERVICE"

echo "==> status"
sudo systemctl --no-pager --full status "$SERVICE" | head -n 12 || true

echo
echo "✅ Update complete. Watch logs:  sudo journalctl -u $SERVICE -f"
