"""열쇠 링크의 «판단»만 모아 둔다.

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
#: 만료일 열에 적히는 KST 사업일. ``date.fromisoformat``은 ``20261225`` 같은
#: 축약형도 받으므로, 저장 형식이 깨진 값을 정상 만료일로 오인하지 않게 좁힌다.
_EXPIRES_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    """새로 발급할 링크의 수명(일)을 읽는다.

    잘못된 값은 무기한 허용하지 않고 기본값(90일)으로 돌아간다.

    ★ 이 값은 «새 발급»과 «만료일이 아직 안 적힌 옛 행»에만 쓰인다. 이미
      ``share_links.expires_at``이 적힌 링크는 그 날짜가 우선이다 —
      그래야 관리자가 미룬 만료일이 전역 설정에 덮이지 않는다.
    """
    raw = os.environ.get(constants.ENV_LINK_MAX_AGE_DAYS, "").strip()
    try:
        days = int(raw) if raw else constants.DEFAULT_LINK_MAX_AGE_DAYS
    except ValueError:
        return constants.DEFAULT_LINK_MAX_AGE_DAYS
    if not 1 <= days <= constants.MAX_LINK_MAX_AGE_DAYS:
        return constants.DEFAULT_LINK_MAX_AGE_DAYS
    return days


def expiry_date_from_value(value: str) -> dt.date | None:
    """저장된 만료일 글자를 날짜로 바꾼다. 모양이 아니면 ``None``.

    Args:
        value: ``share_links.expires_at``에 적힌 ``YYYY-MM-DD``.

    Returns:
        날짜. 비었거나 읽을 수 없으면 ``None`` — 그때는 발급일 + 수명 규칙으로
        되돌아간다. **읽기 실패를 「만료 안 됨」으로 뭉개지 않는다.**
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not _EXPIRES_AT_RE.match(raw):
        return None
    try:
        return dt.date.fromisoformat(raw)
    except (OverflowError, TypeError, ValueError):
        return None


def has_stored_expiry(expires_at: str) -> bool:
    """만료일 열에 «무언가 적혀» 있는가. 모양이 맞는지는 따지지 않는다."""

    return bool(str(expires_at or "").strip())


def expiry_date_of(
    created_at: str,
    *,
    expires_at: str = "",
    max_age_days: int | None = None,
) -> dt.date | None:
    """이 링크가 닫히는 날(그날 00:00 KST부터 닫힘). 알 수 없으면 ``None``.

    ★ 저장된 ``expires_at``이 있으면 **그 날짜가 우선**이다. 관리자가 미룬
      만료일이 전역 수명 설정에 덮이면 「연장」 단추가 거짓말이 된다.
    ★ 적혀 있는데 «읽을 수 없으면» 기본 수명으로 되돌아가지 않는다. 되돌아가면
      옛 규칙(60일)으로 굳은 링크가 표가 깨진 것만으로 30일 더 열린다.
    """
    stored = expiry_date_from_value(expires_at)
    if stored is not None:
        return stored
    if has_stored_expiry(expires_at):
        return None
    if not isinstance(created_at, str) or not _CREATED_AT_RE.match(
        created_at.strip()
    ):
        return None
    try:
        issued = clock.business_date_from_iso(created_at.strip())
    except (OverflowError, TypeError, ValueError):
        return None
    lifetime = (
        link_max_age_days_from_env() if max_age_days is None else max_age_days
    )
    if not isinstance(lifetime, int) or isinstance(lifetime, bool) or lifetime <= 0:
        return None
    try:
        return issued + dt.timedelta(days=lifetime)
    except (OverflowError, ValueError):
        return None


def link_expired(link: object, *, today: dt.date | None = None) -> bool:
    """저장된 링크 한 줄이 지금 닫혀 있는가.

    Args:
        link: ``created_at``·``expires_at``을 가진 저장 행(`store.ShareLink`).
        today: 기준 날짜(KST). 생략하면 지금.

    Returns:
        닫혔으면 True. 값을 읽을 수 없으면 **닫는 쪽**으로 떨어진다.

    ★ `is_share_link_expired`와 달리 **행 하나를 통째로** 본다. 호출부가
      ``expires_at``을 빠뜨려 저장된 만료일을 무시하는 일이 없게 하려는 것이다.
    """
    return is_share_link_expired(
        str(getattr(link, "created_at", "") or ""),
        today=today,
        expires_at=str(getattr(link, "expires_at", "") or ""),
    )


def is_share_link_expired(
    created_at: str,
    *,
    today: dt.date | None = None,
    max_age_days: int | None = None,
    expires_at: str = "",
) -> bool:
    """이 링크의 수명이 지났는지 본다.

    Args:
        created_at: 발급 시각(ISO 8601).
        today: 기준 날짜(KST). 생략하면 지금.
        max_age_days: 수명(일). 생략하면 환경값·기본값.
        expires_at: 저장된 만료일(``YYYY-MM-DD``). **있으면 이 날짜가 우선**이다.

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
    stored_expiry = expiry_date_from_value(expires_at)
    if stored_expiry is not None:
        # 저장된 만료일이 발급일보다 앞이면 표가 깨진 것이다. 그때도 닫는다.
        return issued > current or current >= stored_expiry
    if has_stored_expiry(expires_at):
        # 적혀 있는데 못 읽는다. 기본 수명으로 되돌아가면 「그 링크가 원래
        # 닫히던 날」을 잃어버린 채 더 오래 열린다. 그래서 닫는 쪽으로 간다.
        return True
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


def total_budget_left(total_spent_krw: float, total_cap_krw: float) -> float:
    """이 링크에 «수명 전체»로 남은 예산(원). 0 밑으로 안 내려간다.

    Args:
        total_spent_krw: 지금까지 이 링크가 쓴 돈(종결 실행 실측 + 진행 중 예약).
        total_cap_krw: 이 링크의 수명 전체 상한.

    Returns:
        남은 돈. 이미 넘었으면 0.0.

    ★ 하루 예산(`budget_left`)과 달리 **날이 바뀌어도 되살아나지 않는다.**
      그게 「하루 상한」과 「누적 상한」을 둘 다 두는 이유다.
    """
    return max(0.0, total_cap_krw - total_spent_krw)


def can_start_within_total_budget(
    total_spent_krw: float, total_cap_krw: float
) -> bool:
    """«수명 전체» 예산만 놓고 볼 때 새 조사를 시작해도 되는가.

    ★ 숫자가 깨져 NaN이 들어오면 `max`가 0.0을 돌려주므로 **막는 쪽**으로 떨어진다.
      깨진 장부로 돈을 쓰는 것보다 링크 하나가 멈추는 편이 낫다.
    """
    return total_budget_left(total_spent_krw, total_cap_krw) > 0


def can_start_new_run(
    spend: DailySpend,
    key: str,
    today: dt.date,
    cap_krw: float,
    *,
    total_spent_krw: float | None = None,
    total_cap_krw: float | None = None,
) -> bool:
    """이 열쇠로 «새 조사»를 시작해도 되는가.

    Args:
        spend: 오늘 장부.
        key: 열쇠 (열쇠 없는 손님은 `PUBLIC_BUCKET`).
        today: 오늘 날짜.
        cap_krw: 이 갈래의 **하루** 상한.
        total_spent_krw: 이 링크가 수명 전체에 쓴 돈. LINK 갈래에만 준다.
        total_cap_krw: 이 링크의 수명 전체 상한. LINK 갈래에만 준다.

    Returns:
        하루 상한과 누적 상한을 **둘 다** 통과하면 True.

    ★ 이것은 **새로 AI를 부르는 일**에만 건다.
      **이미 만들어 둔 보고서를 여는 것은 여기를 안 거친다** — 그건 0원이고,
      예산이 다 됐어도 계속 열려야 한다 (사용자 결정).
    ★ 누적 두 인자는 LINK 갈래에만 의미가 있다. MEMBER·ADMIN·PUBLIC은 링크가
      아니라 사람·전체 통장이라 「수명」이라는 개념이 없어 그대로 생략한다.
    """
    if budget_left(spend, key, today, cap_krw) <= 0:
        return False
    if total_spent_krw is None or total_cap_krw is None:
        return True
    return can_start_within_total_budget(total_spent_krw, total_cap_krw)


def total_spent(spend: DailySpend, today: dt.date) -> float:
    """오늘 «모든 링크»가 쓴 돈의 합.

    ★ 전체 상한은 두지 않기로 했지만(사용자 결정), **얼마나 나갔는지는 보여야 한다.**
      상한이 없다는 것과 안 보여도 된다는 것은 다른 말이다.
    """
    return sum(rolled_over(spend, today).by_key.values())
