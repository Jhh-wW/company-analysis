#!/bin/sh
set -eu

# 플랫폼 설정이 잘못돼 root로 덮어써졌다면 조용히 권한을 낮추는 대신 시작을 막는다.
# 영속 볼륨은 UID/GID 1000이 쓸 수 있게 플랫폼의 fsGroup 또는 사전 프로비저닝으로 맞춘다.
if [ "$(id -u)" -eq 0 ]; then
  echo "배포 오류: 컨테이너를 root로 실행할 수 없습니다." >&2
  exit 77
fi

if [ "$#" -eq 0 ]; then
  echo "배포 오류: 실행 명령이 없습니다." >&2
  exit 64
fi

scope="generic"
case " $* " in
  *"src.web.main:app"*) scope="web" ;;
  *"tools.trigger_backup"*) scope="backup-trigger" ;;
  *"tools.trigger_maintenance"*) scope="maintenance-trigger" ;;
esac

python /srv/deploy/validate_environment.py --scope "$scope"
echo "배포 환경 검증 통과: ${scope}" >&2
exec "$@"
