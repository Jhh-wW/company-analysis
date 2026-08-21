"""열쇠 링크의 «판단»만 모아 둔다 (문제로그 P-94).

★ 여기에는 시계도 저장소도 없다. 시각·상태를 인자로 받는 순수 함수뿐이다.
  「하루가 바뀌면 예산이 되살아나는가」를 시험에서 실제로 돌려보기 위해서다.

★ 예산을 «링크마다» 따로 세는 것이 이 파일의 핵심이다.
  전역 장부(`budget/logic.py`)와 모양은 같지만, 열쇠별로 하나씩 있다.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from src.core import clock
from src.features.budget.sharing import REPORT_ID_HEX_CHARS
from src.features.sharelink import constants

#: 열쇠로 인정할 글자 모양. 정확히 128비트인 32자리 16진수만 받는다 —
#: ★ 무엇이든 열쇠로 받아주면 주소창에 아무 글자나 넣어 «새 통장»을 무한히 만들 수 있다.
#:   그러면 링크당 상한이 아무 의미가 없어진다.
_KEY_RE = re.compile(rf"^[0-9a-f]{{{constants.KEY_HEX_CHARS}}}$")
_REPORT_ID_RE = re.compile(rf"^[0-9a-f]{{{REPORT_ID_HEX_CHARS}}}$")
# ``share_store.insert_new()``가 기록하는 확장 ISO 8601 시각만 받는다. Python의
# ``fromisoformat``은 ``20260816`` 같은 축약형도 받아주므로, 저장 형식이 망가진
# 값을 정상 발급 시각으로 오인하지 않게 앞부분 모양을 먼저 좁힌다.
_CREATED_AT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)


def is_valid_key(key: str) -> bool:
    """열쇠로 인정할 모양인가.

    Args:
        key: 주소나 쿠키에서 온 글자.

    Returns:
        소문자 정규화 뒤 정확히 32자리 16진수면 True.

    ★ 모양만 본다. 「우리가 발급한 것인가」는 저장소가 판단한다 (`store.py`).
      모양 검사를 먼저 하는 이유는, 이상한 글자가 DB 조회까지 가지 않게 하려는 것이다.
    """
    return bool(_KEY_RE.match((key or "").strip().lower()))


def normalize_scope_value(value: str) -> str:
    """옛 표시 비교 호환용으로 유니코드·대소문자·연속 공백을 정규화한다.

    함수 이름의 ``scope``는 과거 명칭일 뿐 LINK 권한 범위를 뜻하지 않는다.
    """
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def scope_matches(
    *, link_company: str, company: str, link_job: str = "", job: str = ""
) -> bool:
    """지원 맥락 꼬리표와 입력 회사가 같은지 표시 목적으로만 비교한다.

    이 함수 결과는 권한 판정에 쓰지 않는다. ``link_job``과 ``job``은 옛 호출부와
    저장 행을 깨지 않기 위해 받지만 비교에도 사용하지 않는다.
    """
    return bool(
        normalize_scope_value(link_company)
        and normalize_scope_value(link_company) == normalize_scope_value(company)
    )


def report_id_from_reference(reference: str) -> str:
    """결과 ID 또는 ``/result/<ID>`` 주소에서 검증된 보고서 ID만 꺼낸다.

    관리 화면의 공개 입력이므로 경로가 비슷하다는 이유만으로 임의 문자열을 저장하지
    않는다. 직접 ID, 같은 서비스의 상대 경로, 사용자가 복사한 HTTP(S) 전체 주소만
    받고 인증정보·쿼리·fragment·추가 경로가 붙으면 거절한다.
    """
    if not isinstance(reference, str):
        return ""
    raw = reference.strip()
    direct = raw.lower()
    if _REPORT_ID_RE.fullmatch(direct):
        return direct

    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if parsed.scheme and not parsed.netloc:
        return ""
    if parsed.netloc and not parsed.scheme:
        return ""
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] != "result":
        return ""
    candidate = parts[1].lower()
    return candidate if _REPORT_ID_RE.fullmatch(candidate) else ""


def link_max_age_days_from_env() -> int:
    """링크 수명을 읽는다. 잘못된 값은 무기한 허용하지 않고 60일로 돌아간다."""
    raw = os.environ.get(constants.ENV_LINK_MAX_AGE_DAYS, "").strip()
    try:
        days = int(raw) if raw else constants.DEFAULT_LINK_MAX_AGE_DAYS
    except ValueError:
        return constants.DEFAULT_LINK_MAX_AGE_DAYS
    if not 1 <= days <= constants.MAX_LINK_MAX_AGE_DAYS:
        return constants.DEFAULT_LINK_MAX_AGE_DAYS
    return days


def is_share_link_expired(
    created_at: str,
    *,
    today: dt.date | None = None,
    max_age_days: int | None = None,
) -> bool:
    """발급 시각 기준 수명이 지났는지 본다.

    시간대가 적힌 시각은 KST 사업일로 통일한다. 읽을 수 없거나 미래인 시각, 올바르지
    않은 수명은 권한을 주지 않는 쪽으로 닫는다. 만료일을 발급일에 더하지 않고 날짜
    차이를 비교하므로 ``9999-12-31`` 같은 극단값도 overflow로 500을 만들지 않는다.
    """
    if not isinstance(created_at, str):
        return True
    raw = created_at.strip()
    if not _CREATED_AT_RE.match(raw):
        return True
    try:
        issued = clock.business_date_from_iso(raw)
    except (OverflowError, TypeError, ValueError):
        return True

    current = today or clock.today_kst()
    lifetime = (
        link_max_age_days_from_env() if max_age_days is None else max_age_days
    )
    if not isinstance(lifetime, int) or isinstance(lifetime, bool) or lifetime <= 0:
        return True
    if issued > current:
        return True
    return (current - issued).days >= lifetime


@dataclass
class DailySpend:
    """열쇠별 «오늘» 쓴 돈.

    ★ `day`를 같이 들고 있어야 날짜가 바뀔 때 **저절로** 0으로 돌아간다.
      자정에 비우는 장치를 따로 두면 그게 안 돌 때 상한이 영영 안 풀린다.
    """

    day: dt.date
    by_key: dict[str, float] = field(default_factory=dict)


def rolled_over(spend: DailySpend, today: dt.date) -> DailySpend:
    """날짜가 바뀌었으면 «전부 0원»인 새 장부를 돌려준다."""
    if spend.day == today:
        return spend
    return DailySpend(day=today, by_key={})


def spent_for(spend: DailySpend, key: str, today: dt.date) -> float:
    """이 열쇠가 오늘 쓴 돈."""
    return rolled_over(spend, today).by_key.get(key, 0.0)


def add_spend(
    spend: DailySpend, key: str, today: dt.date, amount_krw: float
) -> DailySpend:
    """이 열쇠 앞으로 쓴 돈을 더한다.

    Args:
        spend: 지금 장부.
        key: 열쇠 (열쇠 없는 손님은 `PUBLIC_BUCKET`).
        today: 오늘 날짜.
        amount_krw: 이번에 쓴 돈. **0 이하는 무시한다** — 환불 개념이 없다.

    Returns:
        더해진 장부.
    """
    rolled = rolled_over(spend, today)
    if amount_krw <= 0:
        return rolled
    rolled.by_key[key] = rolled.by_key.get(key, 0.0) + amount_krw
    return rolled


def budget_left(
    spend: DailySpend, key: str, today: dt.date, cap_krw: float
) -> float:
    """이 열쇠에 오늘 남은 예산(원). 0 밑으로 안 내려간다."""
    return max(0.0, cap_krw - spent_for(spend, key, today))


def can_start_new_run(
    spend: DailySpend, key: str, today: dt.date, cap_krw: float
) -> bool:
    """이 열쇠로 «새 조사»를 시작해도 되는가.

    ★ 이것은 **새로 AI를 부르는 일**에만 건다.
      **이미 만들어 둔 보고서를 여는 것은 여기를 안 거친다** — 그건 0원이고,
      예산이 다 됐어도 계속 열려야 한다 (2026-08-16 사용자 결정).
    """
    return budget_left(spend, key, today, cap_krw) > 0


def total_spent(spend: DailySpend, today: dt.date) -> float:
    """오늘 «모든 링크»가 쓴 돈의 합.

    ★ 전체 상한은 두지 않기로 했지만(사용자 결정), **얼마나 나갔는지는 보여야 한다.**
      상한이 없다는 것과 안 보여도 된다는 것은 다른 말이다.
    """
    return sum(rolled_over(spend, today).by_key.values())
