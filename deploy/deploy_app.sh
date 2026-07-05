#!/usr/bin/env bash
# Деплой приложения (API + дашборд) на сервер.
# Запуск с Мака из корня проекта:  bash deploy/deploy_app.sh
set -euo pipefail

# сервер и ключ задаются через окружение или deploy/env.local (не в репозитории)
[ -f "$(dirname "$0")/env.local" ] && . "$(dirname "$0")/env.local"
SERVER="${SPB_SERVER:?export SPB_SERVER=root@<server-ip>}"
KEY="${SPB_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="/opt/spb-rent"

echo "==> Синхронизирую код, модели и processed-данные..."
rsync -az -e "ssh -i $KEY" \
    --exclude 'data/raw' --exclude 'data/interim' --exclude '*.sqlite' \
    --exclude '.venv/' --exclude '__pycache__/' --exclude '.git/' \
    --exclude '*.log' \
    ./ "$SERVER:$REMOTE_DIR/"

echo "==> Пересобираю образ и перезапускаю сервисы..."
ssh -i "$KEY" "$SERVER" \
    "cd $REMOTE_DIR && docker compose build && docker compose up -d && docker compose ps"

echo ""
echo "Дашборд: http://${SERVER#*@}:8501"
echo "API:     http://${SERVER#*@}:8000/docs"
