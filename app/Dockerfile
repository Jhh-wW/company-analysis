# syntax=docker/dockerfile:1

# Linux용 spaCy 휠이 있는 Python 3.13 패치와 이미지 내용을 함께 고정한다.
FROM python:3.13.14-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64

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

# 실행에 필요한 파일만 복사한다. prototype_v1/.env와 로컬 DB는 이미지에 넣지 않는다.
COPY app/src/ /srv/app/src/
COPY app/tools/backup_sqlite.py /srv/app/tools/backup_sqlite.py
COPY prototype_v1/src/ /srv/prototype_v1/src/
COPY prototype_v1/tools/run_pilot.py /srv/prototype_v1/tools/run_pilot.py
COPY prototype_v1/tools/survey_audit_reports.py /srv/prototype_v1/tools/survey_audit_reports.py
COPY prototype_v1/tools/build_goldenset_answer.py /srv/prototype_v1/tools/build_goldenset_answer.py
COPY prototype_v1/data/pilot/ /srv/prototype_v1/data/pilot/

RUN useradd --create-home --uid 1000 --user-group --shell /bin/sh appuser \
    && root_password="$(openssl rand -base64 48)" \
    && root_hash="$(printf '%s' "$root_password" | openssl passwd -6 -stdin)" \
    && usermod --password "$root_hash" --shell /bin/sh root \
    && unset root_password root_hash \
    && install -d -m 0700 -o root -g root /root/.ssh \
    && install -d -m 0700 -o appuser -g appuser /home/appuser/.ssh \
    && install -d -m 0755 -o appuser -g appuser /var/data \
    && chown -R appuser:appuser /srv/app /srv/prototype_v1

ENV HOME=/root
USER root

EXPOSE 10000

# 새로 마운트된 디스크도 appuser가 쓰게 만든 뒤 서버 권한을 즉시 낮춘다.
# SQLite와 메모리 작업 상태를 한 프로세스만 다루도록 worker는 반드시 1개다.
CMD ["sh", "-c", "chown -R appuser:appuser /var/data && exec gosu appuser env HOME=/home/appuser python -m uvicorn src.web.main:app --host 0.0.0.0 --port \"${PORT:-10000}\" --workers 1"]
