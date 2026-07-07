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
#   bash deploy.sh tts | piper | video | all
#
# Security: copies ONLY build-relevant files (server code, Dockerfile,
# requirements.txt) into the build contexts. .env files are never touched.
set -euo pipefail

REPO="${REPO_DIR:-$HOME/pipeline-repo}"
COMPOSE="${COMPOSE_FILE:-$HOME/docker-compose.yml}"
TARGET="${1:-video}"

pull() {
  echo "==> git pull (main)"
  git -C "$REPO" pull
}

deploy_service() {
  local src="$1" dst="$2" svc="$3" main="$4"
  echo "==> $svc: copy build context"
  cp "$REPO/$src/$main"            "$dst/$main"
  cp "$REPO/$src/Dockerfile"       "$dst/Dockerfile"
  cp "$REPO/$src/requirements.txt" "$dst/requirements.txt"
  echo "==> $svc: rebuild"
  docker compose -f "$COMPOSE" up -d --build "$svc"
}

pull
case "$TARGET" in
  video) deploy_service services/video-renderer "$HOME/video-renderer" video-renderer server.py ;;
  tts)   deploy_service services/tts            "$HOME/tts"            tts            edge_tts_server.py ;;
  piper) deploy_service services/tts-fallback   "$HOME/piper"          tts-fallback   piper_server.py ;;
  all)   "$0" video; "$0" tts; "$0" piper ;;
  *) echo "unknown target: '$TARGET' (allowed: video|tts|piper|all)" >&2; exit 1 ;;
esac

echo "==> done: $TARGET"
docker compose -f "$COMPOSE" ps
