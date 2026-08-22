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

# command 문자열 부분 일치로 scope를 고르면 이름을 바꾼 web wrapper가 generic으로
# 우회할 수 있다. Python resolver가 manifest contract·플랫폼 marker를 command보다
# 먼저 적용하고, generic command 자체도 readiness 불가로 닫는다.
python /srv/deploy/validate_environment.py \
  --from-command \
  --runtime-contract "${DEPLOYMENT_RUNTIME_CONTRACT:-}" \
  -- "$@"
echo "배포 환경·실행 범위 검증 통과" >&2
exec "$@"
