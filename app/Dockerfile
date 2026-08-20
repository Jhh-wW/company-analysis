# Linux용 spaCy 휠이 있는 Python 3.13 패치와 이미지 내용을 함께 고정한다.
FROM python:3.13.15-slim-trixie@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TLDEXTRACT_CACHE=/var/data/cache/tldextract

WORKDIR /srv/app

# Render가 새 디스크를 붙이면 시작할 때 소유권을 정리해야 하므로 gosu를 사용한다.
# SSH용 root 암호는 이미지 빌드 때 새 난수로 만들고 평문을 남기지 않는다.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends gosu openssl \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /tmp/requirements.txt
RUN python -m pip install --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# 실행에 필요한 파일만 복사한다. analysis_engine/.env와 로컬 DB는 이미지에 넣지 않는다.
COPY app/src/ /srv/app/src/
COPY app/tools/backup_sqlite.py /srv/app/tools/backup_sqlite.py
COPY analysis_engine/src/ /srv/analysis_engine/src/
COPY analysis_engine/tools/run_pilot.py /srv/analysis_engine/tools/run_pilot.py
COPY analysis_engine/tools/survey_audit_reports.py /srv/analysis_engine/tools/survey_audit_reports.py
COPY analysis_engine/tools/build_goldenset_answer.py /srv/analysis_engine/tools/build_goldenset_answer.py
COPY analysis_engine/data/pilot/ /srv/analysis_engine/data/pilot/

RUN useradd --create-home --uid 1000 --user-group --shell /bin/sh appuser \
    && root_password="$(openssl rand -base64 48)" \
    && root_hash="$(printf '%s' "$root_password" | openssl passwd -6 -stdin)" \
    && usermod --password "$root_hash" --shell /bin/sh root \
    && unset root_password root_hash \
    && install -d -m 0700 -o root -g root /root/.ssh \
    && install -d -m 0700 -o appuser -g appuser /home/appuser/.ssh \
    && install -d -m 0755 -o appuser -g appuser /var/data

ENV HOME=/root
USER root

EXPOSE 10000

# 새로 마운트된 디스크도 appuser가 쓰게 만든 뒤 서버 권한을 즉시 낮춘다.
# SQLite와 메모리 작업 상태를 한 프로세스만 다루도록 worker는 반드시 1개다.
CMD ["sh", "-c", "chown -R appuser:appuser /var/data && exec gosu appuser env HOME=/home/appuser python -m uvicorn src.web.main:app --host 0.0.0.0 --port \"${PORT:-10000}\" --workers 1 --forwarded-allow-ips='*' --limit-concurrency 20 --backlog 32 --no-access-log"]
