"""OpenDART 클라이언트 — 사용량 계수기 내장 (착수 순서 3번의 「계수기 v1 필수」).

키는 환경변수 DART_API_KEY로만 받는다(하드코딩 금지). .env는 프로그램만 읽는다.
일일 한도: 키당 20,000건 (2026-08-14 공식 확인 — 오류 020 정의). 계수기가 로컬에서 세고,
경보 문턱을 넘으면 경고, 한도면 예외를 던져 「재시도할수록 더 막히는」 상황을 차단한다.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from core.runtime_paths import ENV_DATA_ROOT, LOCAL_LOG_DIR, runtime_log_dir
from core import usage_store

DAILY_LIMIT = 20_000          # 공식 한도 (오류 020)
WARN_RATIO = 0.8              # 경보 문턱 — 80% 소진 시 경고
BASE_URL = "https://opendart.fss.or.kr/api"
TIMEOUT_LIGHT_SEC = 10        # 회사·목록 조회 (03_수집/01_흐름 §5)
COUNTER_FILENAME = "dart_usage.json"
# 예전 코드가 가져다 쓸 수 있도록 남겨 둔 로컬 기본값이다.
COUNTER_PATH = LOCAL_LOG_DIR / COUNTER_FILENAME

ERR_NO_DATA = "013"           # 원래 없다 — 즉시 포기
ERR_LIMIT = "020"             # 한도 소진 — 즉시 중단


class DartLimitReached(RuntimeError):
    """일일 한도 도달 — 오늘은 더 부르지 않는다."""


class UsageCounter:
    """날짜별 호출 수를 파일로 세는 계수기 (프로세스 재시작에도 유지)."""

    def __init__(self, path: Path | None = None, limit: int = DAILY_LIMIT) -> None:
        # 기본 인자에서 경로를 미리 계산하지 않는다. 그래야 배포 환경변수를 읽은 뒤
        # 객체를 만들었을 때 Render 영속 디스크 경로가 정확히 적용된다.
        self.path = path if path is not None else default_counter_path()
        self.limit = limit

    def today_count(self, today: str | None = None) -> int:
        key = today or dt.date.today().isoformat()
        return usage_store.today_count(self.path, key)

    def tick(self, today: str | None = None) -> int:
        """호출 1건 기록. 한도면 DartLimitReached, 경보 문턱이면 경고 출력."""
        key = today or dt.date.today().isoformat()
        try:
            count = usage_store.tick(self.path, key, self.limit)
        except usage_store.UsageLimitReached as exc:
            raise DartLimitReached(
                f"DART 일일 한도({self.limit}건) 도달 — 내일까지 중단"
            ) from exc
        if count >= int(self.limit * WARN_RATIO):
            print(f"⚠️ DART 사용량 {count}/{self.limit} — 경보 문턱({WARN_RATIO:.0%}) 초과")
        return count


def default_counter_path() -> Path:
    """DART 사용량 기록 경로를 돌려준다.

    로컬에서는 기존 ``prototype_v1/logs``를 그대로 쓴다. 배포에서는
    ``APP_DATA_ROOT=/var/data``로 두면 ``/var/data/logs``에 기록되어 서버를
    다시 시작해도 계수기가 유지된다.
    """
    return runtime_log_dir() / COUNTER_FILENAME


def api_key() -> str:
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "DART_API_KEY가 없습니다 — prototype_v1/.env 에 'DART_API_KEY=발급키' 한 줄을 넣고, "
            "실행 전 환경변수로 로드하세요 (opendart.fss.or.kr 개인 즉시 발급·무료)"
        )
    return key


def get_json(endpoint: str, params: dict[str, Any],
             counter: UsageCounter | None = None) -> dict[str, Any]:
    """가벼운 JSON API 호출 (company.json · list.json). 호출 전 계수기 tick."""
    (counter or UsageCounter()).tick()
    query = urllib.parse.urlencode({"crtfc_key": api_key(), **params})
    with urllib.request.urlopen(f"{BASE_URL}/{endpoint}?{query}", timeout=TIMEOUT_LIGHT_SEC) as res:
        payload = json.loads(res.read().decode("utf-8"))
    if payload.get("status") == ERR_LIMIT:
        raise DartLimitReached("DART 응답 020 — 서버 기준 한도 소진")
    return payload


TIMEOUT_CORPCODE_SEC = 60     # corpCode.zip 약 1~2MB
TIMEOUT_DOCUMENT_SEC = 60     # 공시서류 원본 zip — 감사보고서는 보통 수백 KB


def download_document(rcept_no: str, dest_dir: Path,
                      counter: UsageCounter | None = None) -> Path:
    """공시서류 원본(document.xml)을 내려받아 첫 문서 파일을 저장한다. 있으면 재사용.

    zip 안에는 접수번호 이름의 XML(공시 원문)이 들어 있다. 오류 응답은 zip이 아닌
    XML로 오므로 corpCode와 같은 방식으로 상태만 노출한다(키 노출 금지).
    """
    out_path = dest_dir / f"{rcept_no}.xml"
    if out_path.exists():
        return out_path
    (counter or UsageCounter()).tick()
    query = urllib.parse.urlencode({"crtfc_key": api_key(), "rcept_no": rcept_no})
    with urllib.request.urlopen(f"{BASE_URL}/document.xml?{query}",
                                timeout=TIMEOUT_DOCUMENT_SEC) as res:
        data = res.read()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        head = data[:300].decode("utf-8", errors="replace")
        status = "020" if ERR_LIMIT in head else ("응답 머리: " + head.split("message")[0][:80])
        raise RuntimeError(f"공시서류 {rcept_no} 다운로드 실패 — zip이 아닌 응답 ({status})") from exc
    infos = archive.infolist()
    if not infos:
        raise RuntimeError(f"공시서류 {rcept_no} — zip이 비어 있음")
    # 사업보고서 zip은 본문+첨부 여러 파일 — 가장 큰 파일이 본문이다 (첨부만 잡히면 절 표제가 빈다)
    main = max(infos, key=lambda i: i.file_size)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with archive.open(main) as f:
        out_path.write_bytes(f.read())
    return out_path


def download_corpcode(dest_dir: Path, counter: UsageCounter | None = None) -> Path:
    """corpCode.xml(전체 회사 고유번호 목록)을 내려받아 푼다. 이미 있으면 재사용 — 호출 절약.

    오류 응답(잘못된 키 등)은 zip이 아니라 XML로 오므로, 그 경우 상태 코드만 노출한다(키 노출 금지).
    """
    xml_path = dest_dir / "CORPCODE.xml"
    if xml_path.exists():
        return xml_path
    (counter or UsageCounter()).tick()
    query = urllib.parse.urlencode({"crtfc_key": api_key()})
    with urllib.request.urlopen(f"{BASE_URL}/corpCode.xml?{query}",
                                timeout=TIMEOUT_CORPCODE_SEC) as res:
        data = res.read()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        head = data[:300].decode("utf-8", errors="replace")
        status = "020" if ERR_LIMIT in head else ("응답 머리: " + head.split("message")[0][:80])
        raise RuntimeError(f"corpCode 다운로드 실패 — zip이 아닌 응답 ({status})") from exc
    dest_dir.mkdir(parents=True, exist_ok=True)
    with archive.open("CORPCODE.xml") as f:
        xml_path.write_bytes(f.read())
    return xml_path
