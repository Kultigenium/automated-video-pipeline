#!/usr/bin/env bash
#
# deploy.sh — deploys the current main branch onto the host.
#
# Runs ON the host. Flow:
#   1) develop locally  ->  git push
#   2) on the host:     ->  bash deploy.sh <service>
#
# Usage:
#   bash deploy.sh              # default: video-renderer
#   bash deploy.sh tts | fallback | video | all
#
# Security: copies ONLY build-relevant files (code, Dockerfile,
# requirements.txt) into the build contexts. .env files are never touched.
# Rebuilds exactly one service (--no-deps): a blanket `compose up -d` could
# recreate unrelated containers with a different environment.
set -euo pipefail

REPO="${REPO_DIR:-$HOME/pipeline-repo}"
COMPOSE="${COMPOSE_FILE:-$HOME/docker-compose.yml}"
TARGET="${1:-video}"

pull() {
  echo "==> git pull (main)"
  git -C "$REPO" pull
}

deploy_service() {
  local src="$1" dst="$2" svc="$3"; shift 3
  echo "==> $svc: copy build context"
  for f in "$@" Dockerfile requirements.txt; do
    cp "$REPO/$src/$f" "$dst/$f"
  done
  echo "==> $svc: rebuild"
  docker compose -f "$COMPOSE" up -d --build --no-deps "$svc"
}

pull
case "$TARGET" in
  video)    deploy_service services/video-renderer "$HOME/video-renderer" video-renderer \
              server.py beweis.py broll.py motion.py motion3.py ;;
  tts)      deploy_service services/tts          "$HOME/tts"          tts          elevenlabs_server.py ;;
  fallback) deploy_service services/tts-fallback "$HOME/tts-fallback" tts-fallback kokoro_server.py ;;
  all)      "$0" video; "$0" tts; "$0" fallback ;;
  *) echo "unknown target: '$TARGET' (allowed: video|tts|fallback|all)" >&2; exit 1 ;;
esac

echo "==> done: $TARGET"
docker compose -f "$COMPOSE" ps
