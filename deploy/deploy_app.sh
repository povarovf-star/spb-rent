#!/usr/bin/env bash
# Deploy of the application (API + dashboard) to the server.
# Run from the Mac from the project root:  bash deploy/deploy_app.sh
set -euo pipefail

# server and key are set via the environment or deploy/env.local (not in the repo)
[ -f "$(dirname "$0")/env.local" ] && . "$(dirname "$0")/env.local"
SERVER="${SPB_SERVER:?export SPB_SERVER=root@<server-ip>}"
KEY="${SPB_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="/opt/spb-rent"

echo "==> Syncing code, models and processed data..."
rsync -az -e "ssh -i $KEY" \
    --exclude 'data/raw' --exclude 'data/interim' --exclude '*.sqlite' \
    --exclude '.venv/' --exclude '__pycache__/' --exclude '.git/' \
    --exclude '*.log' \
    ./ "$SERVER:$REMOTE_DIR/"

echo "==> Rebuilding the image and restarting services..."
ssh -i "$KEY" "$SERVER" \
    "cd $REMOTE_DIR && docker compose build && docker compose up -d && docker compose ps"

echo ""
echo "Dashboard: http://${SERVER#*@}:8501"
echo "API:     http://${SERVER#*@}:8000/docs"
