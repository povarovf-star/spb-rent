#!/usr/bin/env bash
# Заливает проект на сервер и запускает probe (1 запрос к API ЦИАН).
# Запуск с Мака из корня проекта:  bash deploy/deploy.sh
set -euo pipefail

# сервер и ключ задаются через окружение или deploy/env.local (не в репозитории)
[ -f "$(dirname "$0")/env.local" ] && . "$(dirname "$0")/env.local"
SERVER="${SPB_SERVER:?export SPB_SERVER=root@<server-ip>}"
KEY="${SPB_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="/opt/spb-rent"

SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new $SERVER"

echo "==> Копирую код на сервер ($REMOTE_DIR)..."
$SSH "mkdir -p $REMOTE_DIR"
rsync -az -e "ssh -i $KEY" \
    --exclude 'data/' --exclude '.venv/' --exclude '__pycache__/' \
    --exclude '.git/' --exclude '*.sqlite' --exclude 'scraping.log' \
    ./ "$SERVER:$REMOTE_DIR/"

echo "==> Настраиваю окружение (venv, зависимости — не трогаю системный python)..."
$SSH "cd $REMOTE_DIR && \
    (command -v python3 >/dev/null || apt-get install -y python3) && \
    ([ -d .venv ] || python3 -m venv .venv) && \
    .venv/bin/pip install -q --upgrade pip && \
    .venv/bin/pip install -q -r requirements.txt"

echo "==> Probe: проверяю, отвечает ли API ЦИАН с IP сервера..."
$SSH "cd $REMOTE_DIR && .venv/bin/python -m src.scraping.run probe"

echo ""
echo "Если выше напечатано 'OK: API отвечает' — запускай полный сбор:"
echo "  bash deploy/start_collect.sh"
