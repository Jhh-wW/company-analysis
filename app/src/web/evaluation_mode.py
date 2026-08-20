"""명시적으로 켠 로컬 실시간 성능시험의 안전 설정.

일반 데모와 공개 배포에는 영향을 주지 않는다. 이 모드는 loopback 실행기에서만
사용하며, 유료 provider는 별도 스위치와 화면 동의를 모두 거쳐야 한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Final

from src.core import clock
from src.core.constants import PIPELINE_ENV, PIPELINE_REAL


ENV_MODE: Final[str] = "REALTIME_EVALUATION_MODE"
ENV_PAID_PROVIDERS: Final[str] = "REALTIME_EVALUATION_PAID_PROVIDERS"
ENV_PER_RUN_CAP_KRW: Final[str] = "REALTIME_EVALUATION_PER_RUN_CAP_KRW"
ENV_DAILY_CAP_KRW: Final[str] = "REALTIME_EVALUATION_DAILY_CAP_KRW"
ENV_DISABLE_ENGINE_DOTENV: Final[str] = "ANALYSIS_ENGINE_DISABLE_DOTENV"

DEFAULT_PER_RUN_CAP_KRW: Final[float] = 1200.0
DEFAULT_DAILY_CAP_KRW: Final[float] = 2200.0
MAX_CAP_KRW: Final[float] = 100_000.0
LOCAL_BUCKET: Final[str] = "evaluation:loopback"
CONSENT_VALUE: Final[str] = "yes"
CONSENT_GRANT_TTL_SEC: Final[int] = 15 * 60
CONSENT_TRANSITION_CONTINUE: Final[str] = "continue"
WORKFLOW_ID_HEX_LENGTH: Final[int] = 32
MAX_PENDING_WORKFLOWS: Final[int] = 4096
PREVIEW_BLOCKED_MESSAGE: Final[str] = (
    "현재는 실시간 성능시험 미리보기라 외부 호출이 잠겨 있습니다. "
    "서버를 끈 뒤 -EnablePaidProviders를 명시해서 다시 실행해 주세요."
)
REQUIRED_PROVIDER_ENV_NAMES: Final[tuple[str, ...]] = (
    "DART_API_KEY",
    "ANTHROPIC_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
)
GOOGLE_PLACES_KEY_ENV: Final[str] = "GOOGLE_PLACES_API_KEY"
GOOGLE_PLACES_BILLING_ACK_ENV: Final[str] = "GOOGLE_PLACES_BILLING_ACK"
GOOGLE_PLACES_TERMS_ACK_ENV: Final[str] = "GOOGLE_PLACES_TERMS_ACK"
_CONSENT_GRANT_SECRET: Final[bytes] = secrets.token_bytes(32)
_CSRF_SECRET: Final[str] = secrets.token_urlsafe(32)
_WORKFLOW_LOCK = threading.Lock()
_PENDING_WORKFLOWS: dict[str, float] = {}


class EvaluationConfigurationError(RuntimeError):
    """유료 호출 경계를 안전하게 만들 수 없는 평가 설정."""


@dataclass(frozen=True)
class EvaluationSettings:
    enabled: bool
    paid_providers_enabled: bool
    per_run_cap_krw: float
    daily_cap_krw: float
    business_day_label: str


def _flag(name: str) -> bool:
    """오타나 다른 truthy 문자열은 모두 닫힌 값으로 본다."""
    return os.environ.get(name, "").strip() == "1"


def enabled() -> bool:
    return _flag(ENV_MODE)


def paid_providers_enabled() -> bool:
    return enabled() and _flag(ENV_PAID_PROVIDERS)


def _cap(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EvaluationConfigurationError(
            f"{name}에는 유한한 양수 금액을 넣어야 합니다"
        ) from exc
    if not math.isfinite(value) or value <= 0 or value > MAX_CAP_KRW:
        raise EvaluationConfigurationError(
            f"{name}에는 0원 초과 {MAX_CAP_KRW:.0f}원 이하를 넣어야 합니다"
        )
    return value


def missing_provider_names() -> tuple[str, ...]:
    """값은 돌려주거나 기록하지 않고 비어 있는 key 이름만 돌려준다."""
    return tuple(
        name
        for name in REQUIRED_PROVIDER_ENV_NAMES
        if not os.environ.get(name, "").strip()
    )


def settings() -> EvaluationSettings:
    active = enabled()
    # 일반 배포·데모는 이름이 같은 외부 환경값에 영향을 받지 않는다. 평가 모드를
    # 명시적으로 켠 뒤에만 비용 상한을 파싱한다.
    per_run = (
        _cap(ENV_PER_RUN_CAP_KRW, DEFAULT_PER_RUN_CAP_KRW)
        if active
        else DEFAULT_PER_RUN_CAP_KRW
    )
    daily = (
        _cap(ENV_DAILY_CAP_KRW, DEFAULT_DAILY_CAP_KRW)
        if active
        else DEFAULT_DAILY_CAP_KRW
    )
    if per_run > daily:
        raise EvaluationConfigurationError(
            "건당 예상비용 상한은 일일 예상비용 상한보다 클 수 없습니다"
        )
    return EvaluationSettings(
        enabled=active,
        paid_providers_enabled=paid_providers_enabled(),
        per_run_cap_krw=per_run,
        daily_cap_krw=daily,
        business_day_label=(
            clock.business_day_label(clock.today_kst()) if active else ""
        ),
    )


def validate_startup_configuration() -> EvaluationSettings:
    """직접 실행으로 launcher 경계를 우회해도 유료 모드는 닫히게 한다."""
    current = settings()
    if not current.enabled:
        return current
    if os.environ.get(PIPELINE_ENV, "").strip().lower() != PIPELINE_REAL:
        raise EvaluationConfigurationError(
            "실시간 성능시험은 PIPELINE=real에서만 실행할 수 있습니다"
        )
    if os.environ.get(ENV_DISABLE_ENGINE_DOTENV, "").strip() != "1":
        raise EvaluationConfigurationError(
            "실시간 성능시험에서는 analysis_engine .env 자동 읽기를 차단해야 합니다"
        )
    if current.paid_providers_enabled:
        missing = missing_provider_names()
        if missing:
            raise EvaluationConfigurationError(
                "실시간 성능시험에 필요한 provider 환경변수가 없습니다: "
                + ", ".join(missing)
            )
        candidate_provider = os.environ.get(
            "BUSINESS_CANDIDATE_PROVIDER", ""
        ).strip()
        if candidate_provider not in {"", "disabled", "google_places"}:
            raise EvaluationConfigurationError(
                "유료 성능시험의 회사 후보 공급자는 disabled 또는 google_places여야 합니다"
            )
        if candidate_provider == "google_places":
            if not os.environ.get(GOOGLE_PLACES_KEY_ENV, "").strip():
                raise EvaluationConfigurationError(
                    f"실시간 성능시험의 필수 provider 환경변수가 없습니다: "
                    f"{GOOGLE_PLACES_KEY_ENV}"
                )
            if os.environ.get(GOOGLE_PLACES_BILLING_ACK_ENV, "").strip() != "1":
                raise EvaluationConfigurationError(
                    "유료 후보 검색에는 GOOGLE_PLACES_BILLING_ACK=1 명시 동의가 필요합니다"
                )
            if os.environ.get(GOOGLE_PLACES_TERMS_ACK_ENV, "").strip() != "yes":
                raise EvaluationConfigurationError(
                    "Google Places 후보 검색에는 GOOGLE_PLACES_TERMS_ACK=yes 운영자 확인이 필요합니다"
                )
    elif os.environ.get("BUSINESS_CANDIDATE_PROVIDER", "").strip() not in {
        "",
        "disabled",
    }:
        raise EvaluationConfigurationError(
            "미리보기에서는 회사 후보 공급자를 disabled로 두어야 합니다"
        )
    return current


def consent_granted(value: object) -> bool:
    """유료 평가에서는 화면의 명시적 동의값만 인정한다."""
    if not paid_providers_enabled():
        return False
    return isinstance(value, str) and value.strip().lower() == CONSENT_VALUE


def csrf_secret() -> str:
    """loopback 평가 브라우저의 폼 토큰을 파생할 프로세스 전용 난수."""
    return _CSRF_SECRET


def issue_workflow_id(*, now: float | None = None) -> str:
    """입력 화면 한 장에만 쓸 수 있는 평가 전환 nonce를 발급한다."""
    if not paid_providers_enabled():
        return ""
    issued_at = time.monotonic() if now is None else float(now)
    with _WORKFLOW_LOCK:
        expired_before = issued_at - CONSENT_GRANT_TTL_SEC
        for token, created_at in tuple(_PENDING_WORKFLOWS.items()):
            if created_at < expired_before:
                _PENDING_WORKFLOWS.pop(token, None)
        while len(_PENDING_WORKFLOWS) >= MAX_PENDING_WORKFLOWS:
            oldest = min(_PENDING_WORKFLOWS, key=_PENDING_WORKFLOWS.__getitem__)
            _PENDING_WORKFLOWS.pop(oldest, None)
        for _ in range(8):
            workflow_id = secrets.token_hex(WORKFLOW_ID_HEX_LENGTH // 2)
            if workflow_id not in _PENDING_WORKFLOWS:
                _PENDING_WORKFLOWS[workflow_id] = issued_at
                return workflow_id
    raise EvaluationConfigurationError("평가 입력 전환 번호를 만들지 못했습니다")


def consume_workflow_id(workflow_id: object, *, now: float | None = None) -> bool:
    """최초 `/confirm` 전환을 lock 안에서 정확히 한 번만 소비한다."""
    if (
        not paid_providers_enabled()
        or not isinstance(workflow_id, str)
        or len(workflow_id) != WORKFLOW_ID_HEX_LENGTH
        or any(char not in "0123456789abcdef" for char in workflow_id)
    ):
        return False
    current = time.monotonic() if now is None else float(now)
    with _WORKFLOW_LOCK:
        issued_at = _PENDING_WORKFLOWS.pop(workflow_id, None)
    return bool(
        issued_at is not None
        and issued_at <= current + 10
        and current - issued_at <= CONSENT_GRANT_TTL_SEC
    )


def _consent_payload(
    *,
    issued_at: int,
    company: str,
    job: str,
    region: str,
    posting_text: str,
    bucket_id: str,
    workflow_id: str,
    transition: str,
) -> bytes:
    # 폼 원문은 토큰에 넣지 않는다. 길이 구분 JSON을 먼저 해시해 회사·주소·공고가
    # 브라우저 기록이나 서버 로그에 서명 payload로 중복 노출되지 않게 한다.
    fingerprint = hashlib.sha256(
        json.dumps(
            [company, job, region, posting_text],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        f"{issued_at}:{workflow_id}:{transition}:{bucket_id}:{fingerprint}"
    ).encode("ascii")


def issue_consent_grant(
    *,
    company: str,
    job: str,
    region: str,
    posting_text: str,
    bucket_id: str,
    workflow_id: str,
    transition: str = CONSENT_TRANSITION_CONTINUE,
    now: float | None = None,
) -> str:
    """화면 동의를 입력·비용 통장에 묶은 짧은 서버 서명으로 바꾼다."""
    issued_at = int(time.time() if now is None else now)
    payload = _consent_payload(
        issued_at=issued_at,
        company=company,
        job=job,
        region=region,
        posting_text=posting_text,
        bucket_id=bucket_id,
        workflow_id=workflow_id,
        transition=transition,
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(_CONSENT_GRANT_SECRET, payload, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return f"{issued_at}.{workflow_id}.{transition}.{signature}"


def consent_grant_valid(
    grant: object,
    *,
    company: str,
    job: str,
    region: str,
    posting_text: str,
    bucket_id: str,
    expected_transition: str = CONSENT_TRANSITION_CONTINUE,
    now: float | None = None,
) -> bool:
    """15분 안의 같은 입력·통장에 발급한 서명만 후속 폼에서 인정한다."""
    if not paid_providers_enabled() or not isinstance(grant, str):
        return False
    try:
        raw_issued, workflow_id, transition, received = grant.strip().split(".", 3)
        issued_at = int(raw_issued)
    except (TypeError, ValueError):
        return False
    if (
        len(workflow_id) != WORKFLOW_ID_HEX_LENGTH
        or any(char not in "0123456789abcdef" for char in workflow_id)
        or transition != expected_transition
    ):
        return False
    current = int(time.time() if now is None else now)
    if issued_at > current + 10 or current - issued_at > CONSENT_GRANT_TTL_SEC:
        return False
    payload = _consent_payload(
        issued_at=issued_at,
        company=company,
        job=job,
        region=region,
        posting_text=posting_text,
        bucket_id=bucket_id,
        workflow_id=workflow_id,
        transition=transition,
    )
    expected = base64.urlsafe_b64encode(
        hmac.new(_CONSENT_GRANT_SECRET, payload, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(received, expected)
