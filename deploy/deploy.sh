#!/usr/bin/env bash
# Uploads the project to the server and runs probe (1 request to the CIAN API).
# Run from the Mac from the project root:  bash deploy/deploy.sh
set -euo pipefail

# server and key are set via the environment or deploy/env.local (not in the repo)
[ -f "$(dirname "$0")/env.local" ] && . "$(dirname "$0")/env.local"
SERVER="${SPB_SERVER:?export SPB_SERVER=root@<server-ip>}"
KEY="${SPB_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="/opt/spb-rent"

SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new $SERVER"

echo "==> Copying code to the server ($REMOTE_DIR)..."
$SSH "mkdir -p $REMOTE_DIR"
rsync -az -e "ssh -i $KEY" \
    --exclude 'data/' --exclude '.venv/' --exclude '__pycache__/' \
    --exclude '.git/' --exclude '*.sqlite' --exclude 'scraping.log' \
    ./ "$SERVER:$REMOTE_DIR/"

echo "==> Setting up the environment (venv, dependencies, leaving the system python alone)..."
$SSH "cd $REMOTE_DIR && \
    (command -v python3 >/dev/null || apt-get install -y python3) && \
    ([ -d .venv ] || python3 -m venv .venv) && \
    .venv/bin/pip install -q --upgrade pip && \
    .venv/bin/pip install -q -r requirements.txt"

echo "==> Probe: checking whether the CIAN API responds from the server IP..."
$SSH "cd $REMOTE_DIR && .venv/bin/python -m src.scraping.run probe"

echo ""
echo "If 'OK: the API responds' printed above, start the full collection:"
echo "  bash deploy/start_collect.sh"
