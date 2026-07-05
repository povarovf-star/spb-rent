#!/usr/bin/env bash
# Запускает полный сбор на сервере в фоне (nohup) и ставит cron
# на регулярные снимки: понедельник и четверг в 03:00 по времени сервера.
# Запуск с Мака:  bash deploy/start_collect.sh
set -euo pipefail

# сервер и ключ задаются через окружение или deploy/env.local (не в репозитории)
[ -f "$(dirname "$0")/env.local" ] && . "$(dirname "$0")/env.local"
SERVER="${SPB_SERVER:?export SPB_SERVER=root@<server-ip>}"
KEY="${SPB_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="/opt/spb-rent"

SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new $SERVER"

echo "==> Запускаю сбор в фоне..."
$SSH "cd $REMOTE_DIR && \
    nohup .venv/bin/python -m src.scraping.run collect >> collect.out 2>&1 & \
    echo \"PID: \$!\""

echo "==> Ставлю cron (пн и чт, 03:00)..."
$SSH "( crontab -l 2>/dev/null | grep -v spb-rent ; \
    echo '0 3 * * 1,4 cd $REMOTE_DIR && .venv/bin/python -m src.scraping.run collect >> collect.out 2>&1 # spb-rent' \
    ) | crontab -"

echo ""
echo "Следить за прогрессом:"
echo "  ssh -i $KEY $SERVER 'tail -20 $REMOTE_DIR/collect.out'"
echo "Сколько собрано:"
echo "  ssh -i $KEY $SERVER \"cd $REMOTE_DIR && .venv/bin/python -c 'from src.scraping.storage import Storage; print(Storage(\\\"data/cian.sqlite\\\",\\\"data/raw\\\").stats())'\""
