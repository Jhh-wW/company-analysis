# Linux용 spaCy 휠이 있는 Python 3.13 패치와 이미지 내용을 함께 고정한다.
FROM python:3.13.15-slim-trixie@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/appuser \
    XDG_CACHE_HOME=/var/data/cache \
    TMPDIR=/tmp \
    APP_DATA_ROOT=/var/data \
    STORAGE_DB_PATH=/var/data/storage.db \
    OBSERVABILITY_RECORDS_PATH=/var/data/observability/runs.jsonl \
    TLDEXTRACT_CACHE=/var/data/cache/tldextract \
    PORT=10000 \
    PIPELINE=demo \
    BETA_ADMIN_ONLY=1 \
    LOG_LEVEL=info \
    GRACEFUL_SHUTDOWN_SECONDS=300 \
    HEALTHCHECK_PATH=/readyz

WORKDIR /srv/app

# 런타임 계정과 영속 경로를 먼저 만든다. 이미지는 root 암호나 SSH 자격증명을
# 만들지 않으며, 컨테이너 기본 사용자도 이 계정으로 고정한다.
RUN useradd --create-home --uid 1000 --user-group --shell /bin/sh appuser \
    && install -d -m 0770 -o appuser -g appuser \
        /var/data /var/data/cache /var/data/observability

COPY app/requirements.txt /tmp/requirements.txt
RUN python -m pip install --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# 실행에 필요한 파일만 복사한다. analysis_engine/.env와 로컬 DB는 이미지에 넣지 않는다.
COPY app/src/ /srv/app/src/
COPY app/tools/__init__.py /srv/app/tools/__init__.py
COPY app/tools/internal_trigger.py /srv/app/tools/internal_trigger.py
COPY app/tools/backup_sqlite.py /srv/app/tools/backup_sqlite.py
COPY app/tools/trigger_backup.py /srv/app/tools/trigger_backup.py
COPY app/tools/trigger_maintenance.py /srv/app/tools/trigger_maintenance.py
COPY app/tools/container_entrypoint.sh /srv/app/tools/container_entrypoint.sh
COPY deploy/container_entrypoint.sh /srv/deploy/container_entrypoint.sh
COPY deploy/container_healthcheck.py /srv/deploy/container_healthcheck.py
COPY deploy/validate_environment.py /srv/deploy/validate_environment.py
COPY deploy/verify_image.py /srv/deploy/verify_image.py
COPY analysis_engine/src/ /srv/analysis_engine/src/
COPY analysis_engine/tools/run_pilot.py /srv/analysis_engine/tools/run_pilot.py
COPY analysis_engine/tools/survey_audit_reports.py /srv/analysis_engine/tools/survey_audit_reports.py
COPY analysis_engine/tools/build_goldenset_answer.py /srv/analysis_engine/tools/build_goldenset_answer.py
COPY analysis_engine/data/pilot/ /srv/analysis_engine/data/pilot/

RUN install -m 0755 /srv/deploy/container_entrypoint.sh /usr/local/bin/company-analysis-entrypoint

VOLUME ["/var/data"]
USER appuser

EXPOSE 10000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=15s --timeout=4s --start-period=45s --retries=4 \
    CMD ["python", "/srv/deploy/container_healthcheck.py"]

# 플랫폼이 CMD를 웹·cron 명령으로 바꿔도 ENTRYPOINT 환경 검증은 항상 거친다.
ENTRYPOINT ["company-analysis-entrypoint"]

# SQLite와 메모리 작업 상태를 한 프로세스만 다루도록 worker는 반드시 1개다.
CMD ["sh", "-c", "exec python -m uvicorn src.web.main:app --host 0.0.0.0 --port \"${PORT:-10000}\" --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --limit-concurrency 20 --backlog 32 --timeout-keep-alive 5 --timeout-graceful-shutdown \"${GRACEFUL_SHUTDOWN_SECONDS:-300}\" --log-level \"${LOG_LEVEL:-info}\""]
