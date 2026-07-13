#!/usr/bin/env bash
# Starts the full collection on the server in the background (nohup) and installs a cron
# for regular snapshots: Monday and Thursday at 03:00 server time.
# Run from the Mac:  bash deploy/start_collect.sh
set -euo pipefail

# server and key are set via the environment or deploy/env.local (not in the repo)
[ -f "$(dirname "$0")/env.local" ] && . "$(dirname "$0")/env.local"
SERVER="${SPB_SERVER:?export SPB_SERVER=root@<server-ip>}"
KEY="${SPB_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="/opt/spb-rent"

SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new $SERVER"

echo "==> Starting the collection in the background..."
$SSH "cd $REMOTE_DIR && \
    nohup .venv/bin/python -m src.scraping.run collect >> collect.out 2>&1 & \
    echo \"PID: \$!\""

echo "==> Installing cron (Mon and Thu, 03:00)..."
$SSH "( crontab -l 2>/dev/null | grep -v spb-rent ; \
    echo '0 3 * * 1,4 cd $REMOTE_DIR && .venv/bin/python -m src.scraping.run collect >> collect.out 2>&1 # spb-rent' \
    ) | crontab -"

echo ""
echo "Watch progress:"
echo "  ssh -i $KEY $SERVER 'tail -20 $REMOTE_DIR/collect.out'"
echo "How much collected:"
echo "  ssh -i $KEY $SERVER \"cd $REMOTE_DIR && .venv/bin/python -c 'from src.scraping.storage import Storage; print(Storage(\\\"data/cian.sqlite\\\",\\\"data/raw\\\").stats())'\""
