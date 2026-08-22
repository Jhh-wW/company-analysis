#!/bin/sh
set -eu

# Render가 빈 영속 디스크를 붙인 첫 실행에서만 root 권한으로 소유권을 맞춘다.
# 웹 기본 명령과 render.yaml이 덮어쓴 cron 명령은 모두 appuser로 실행한다.
if [ "$(id -u)" -eq 0 ]; then
  chown -R appuser:appuser /var/data
  exec gosu appuser env HOME=/home/appuser "$@"
fi

exec "$@"
