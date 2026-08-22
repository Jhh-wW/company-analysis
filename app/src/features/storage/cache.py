"""캐시 1층·2층 — 키 만들기, 조회·저장, 별칭 캐시.

정본: 확정/03_수집/2_규칙/03_캐시와저장.md (이 파일의 유일한 정본)

# 캐시 1층 — 회사(고유번호) × 직무 × 공고 지문 → 완성 보고서

★ 「회사(고유번호)」를 반드시 쓴다. 화면에 보이는 회사명 문자열이 아니다.
  이름은 같은데 다른 법인(계열사)이 실재하므로, 이름으로 키를 만들면
  「계열사 오인으로 오염된 캐시가 다음 사람에게 반환」되는 사고가 난다
  (팀장 지시 — `01_식별/2_규칙/01_이름대조.md` 확인 카드 규칙 2 참고).
  이 파일의 모든 함수는 `corp_id`를 **필수 인자**로 받는다 — 회사명만으로는
  절대 캐시를 찾거나 채우지 못하게 만들어, 실수로 이름 문자열을 넘겨도
  타입은 맞아 버그를 코드 리뷰로만 잡아야 하는 상황을 최대한 줄였다.

# 1층 신선도 (O9) — 어디까지 이 파일이 하는가

정본은 "검사 내용: O9 그대로 — DART 사업연도 비교 · 뉴스/방향 3년, 코드는
04 게이트와 같은 코드를 부른다"고 적었다. 그런데 이 저장소 기능을 만드는
시점에는 04 게이트의 O9 코드가 아직 파이프라인 쪽에 없다(03_수집 담당
몫). 그래서 이 파일이 O9 규칙 자체를 구현해 갖고 있는다 — **04 게이트가
나중에 만들어지면 그쪽이 이 파일의 신선도 판정 부분을 갖다 쓰는 방향으로
합쳐야 한다** (지금은 반대로 못 한다. 04 게이트 코드가 없다).

- DART 부분(사업연도 비교)은 **호출하는 쪽이 "지금 최신 사업연도"를
  넘겨줘야** 판정할 수 있다(그 값 자체는 DART를 불러야 아는 값이라 storage가
  직접 조회하지 않는다 — storage는 네트워크를 만지지 않는다). 안 넘기면
  (`current_fiscal_year=None`) **신선하다고 보지 않는다** — 모르는 걸
  신선하다고 우겨서 남의 옛 보고서를 내보내는 사고보다, 캐시를 한 번 더
  미스 처리해 재수집하는 쪽이 싸다(★ 팀장 확인 필요 — 정본에 이 경우가
  명시돼 있지 않아 보수적으로 정했다. 04 게이트가 자리 잡으면 이 값은
  거의 항상 채워져 들어올 것이다).
- 뉴스·방향(3년) 부분은 `provenance/freshness.is_stale()`을 그대로
  불러 쓴다 — "코드는 한 벌"이라는 지시를 이 부분에서는 이미 지킬 수
  있었다(이미 있는 코드이므로).
- "종료연도가 명시된 계획(최장 5년)" 규칙은 **여기서 구현하지 않는다** —
  문장 안에서 "그 연도"를 읽어내는 건 텍스트 분석이 필요해 이 파일의
  범위(순수 캐시 조회·저장)를 넘는다. 알려진 한계로 남긴다.

# 캐시 2층 — 회사(고유번호) → 수집 자료 재사용

`layer2_cache`에 회사 단위로 조각(fragments)·공시 메타(filing)·칸별
판정(cell_judgments)을 저장한다. 이 히트의 신선도(04 게이트 몫)는 이
파일이 판정하지 않는다 — 정본이 "04 게이트의 신선도 검사는 2층 히트분만
본다"고 명시했으므로, 이 파일은 자료를 있는 그대로 보관·반환만 한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from src.features.pipeline.port import Report
from src.features.provenance.freshness import is_stale
from src.features.provenance.sources import (
    Source,
    SourceKind,
    official_web_currentness_is_usable,
)
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.report_standard.publish import PublishBlockedError, validate_publishable
from src.features.storage import reports as reports_store
from src.features.storage.constants import (
    LAYER1_MAX_ENTRIES_PER_JOB,
    TABLE_ALIAS_CACHE,
    TABLE_LAYER1_CACHE,
    TABLE_LAYER2_CACHE,
    TABLE_REPORTS,
)

_WHITESPACE = re.compile(r"\s+")
#: 정규화한 요구역량 문장을 이어붙일 때 쓰는 구분자. 문장 안에는 절대
#: 나오지 않는 제어문자라(ASCII Unit Separator) 문장이 우연히 겹쳐 지문이
#: 같아지는 사고를 피한다.
_FINGERPRINT_JOIN = "\x1f"

# 회사분석 제품은 옛 ``회사×직무×공고지문`` 캐시와 같은 테이블을 읽지만,
# 제품 namespace와 schema version을 모두 키에 넣어 빈 직무/빈 지문인 옛
# 항목과 섞이지 않게 한다. 이 값들은 보고서 본문이나 사용자 화면에는 노출되지
# 않는 저장소 내부 식별자다.
_COMPANY_ANALYSIS_PRODUCT_KEY = "product:company-analysis"
_COMPANY_ANALYSIS_SCHEMA_REQUIREMENTS = (f"schema:{CANONICAL_SCHEMA_VERSION}",)


# ══════════════════════════════════════════════════════════
# 정규화 · 지문
# ══════════════════════════════════════════════════════════


def _normalize_text(text: str) -> str:
    """공백을 하나로 뭉치고 앞뒤를 자르고 대소문자를 통일한다.

    ★ 1층 키(직무)와 별칭 캐시 키(회사 통칭)가 같은 정규화 규칙을 쓴다
      (정본 §1-b 별칭 캐시 — "층1 정규화 규칙과 동일").
    """
    return _WHITESPACE.sub(" ", text.strip()).casefold()


def normalize_job(job: str) -> str:
    """직무명을 캐시 키로 쓸 수 있게 다듬는다."""
    return _normalize_text(job)


def normalize_alias(typed_name: str) -> str:
    """사용자가 입력한 회사 통칭을 별칭 캐시 키로 다듬는다."""
    return _normalize_text(typed_name)


def posting_fingerprint(requirements: Iterable[str]) -> str:
    """요구역량 목록으로 공고 지문을 만든다.

    정본 §1 캐시 키 — "요구역량 목록을 정규화(정렬·공백 제거)한 뒤 해시".

    ★ 정렬하는 이유 — AI가 같은 공고를 다시 읽어도 문장을 뽑는 순서가
      매번 같다는 보장이 없다. 정렬해야 "내용이 같으면 지문도 같다"가 참이
      된다.
    """
    normalized = sorted(_normalize_text(r) for r in requirements if r.strip())
    joined = _FINGERPRINT_JOIN.join(normalized)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════
# 1층 신선도 (O9) — §모듈 docstring 참고
# ══════════════════════════════════════════════════════════


def _is_layer1_fresh(
    report: Report,
    *,
    cached_fiscal_year: Optional[int],
    current_fiscal_year: Optional[int],
    today: Optional[dt.date],
) -> bool:
    """1층 캐시 후보가 O9를 통과하는가. 한 소스라도 만료면 미달(정본 원칙)."""
    if cached_fiscal_year is None or current_fiscal_year is None:
        return False
    if cached_fiscal_year != current_fiscal_year:
        return False

    reference_date = today or dt.date.today()
    for citation in report.citations:
        if not isinstance(citation, Source) or citation.kind is SourceKind.FILING:
            # 공시(DART)는 위에서 이미 사업연도로 비교했다 — 날짜 뺄셈을 또
            # 적용하면 "12월 결산 회사를 2월에 보면 만료"처럼 정본이 피하려던
            # 오탐이 그대로 재현된다 (§왜 DART는 개월 수로 안 재나).
            continue
        if not official_web_currentness_is_usable(
            source_type=citation.source_type,
            url=citation.url,
            published_at=citation.published_at,
            disclosed_at=citation.disclosed_at,
            collected_at=citation.collected_at,
            reference_date=reference_date.isoformat(),
        ):
            return False
        date_str = citation.published_at or citation.collected_at
        if not date_str:
            continue
        if is_stale(date_str, today=today) is True:
            return False
    return True


# ══════════════════════════════════════════════════════════
# 1층 — 회사 × 직무 × 공고 지문 → 보고서
# ══════════════════════════════════════════════════════════


def has_layer1_candidates(conn: sqlite3.Connection, *, corp_id: str, job: str) -> bool:
    """이 회사·직무로 저장된 1층 항목이 하나라도 있는가 (0원 — 파이프라인 4번).

    ★ 지문을 아직 모를 때(5.5 전) 쓴다. "예"면 5(판정)를 건너뛸 후보가
      있다는 뜻일 뿐, 확정은 `get_layer1_hit()`이 한다.
    """
    row = conn.execute(
        f"SELECT 1 FROM {TABLE_LAYER1_CACHE} WHERE corp_id = ? AND job_key = ? LIMIT 1",
        (corp_id, normalize_job(job)),
    ).fetchone()
    return row is not None


def get_layer1_hit(
    conn: sqlite3.Connection,
    *,
    corp_id: str,
    job: str,
    requirements: list[str],
    current_fiscal_year: Optional[int] = None,
    today: Optional[dt.date] = None,
) -> Optional[Report]:
    """지문까지 대조해 1층 캐시를 확정한다 (파이프라인 "1층 확정", 5.5 뒤).

    Args:
        conn: `db.connect()`가 연 연결.
        corp_id: 회사 고유번호. **회사명 문자열을 넣지 않는다.**
        job: 직무명(정규화는 이 함수가 한다).
        requirements: 5.5가 방금 뽑은 요구역량 목록 — 여기서 지문을 계산해
            저장된 지문과 대조한다. 원문 자체는 이 함수에 들어오지 않는다.
        current_fiscal_year: 지금 시점의 최신 사업연도(호출부가 DART로
            이미 알고 있는 값). 없으면 신선도를 확인할 수 없어 미스 처리한다.
        today: 신선도 판정 기준일. 생략하면 오늘(시험에서만 고정값을 쓴다).

    Returns:
        지문이 일치하고 O9를 통과한 보고서. 아니면 `None`(2층 경로로).
    """
    row = conn.execute(
        f"""
        SELECT report_id, fiscal_year FROM {TABLE_LAYER1_CACHE}
        WHERE corp_id = ? AND job_key = ? AND posting_fingerprint = ?
        """,
        (corp_id, normalize_job(job), posting_fingerprint(requirements)),
    ).fetchone()
    if row is None:
        return None

    report = reports_store.load(conn, row["report_id"])
    if report is None:
        return None  # 참조가 끊어졌다(있어선 안 되지만 방어적으로) — 미스로 취급

    if not _is_layer1_fresh(
        report,
        cached_fiscal_year=row["fiscal_year"],
        current_fiscal_year=current_fiscal_year,
        today=today,
    ):
        return None
    return report


def save_layer1(
    conn: sqlite3.Connection,
    *,
    corp_id: str,
    job: str,
    requirements: list[str],
    report: Report,
    fiscal_year: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> str:
    """1층 캐시에 완성된 보고서를 저장한다.

    같은 (corp_id, job, requirements)로 다시 부르면 **덮어쓴다**(새 report_id로
    바뀌고, 더 이상 아무도 안 가리키게 된 옛 보고서 본문은 지운다 — DB가
    무한히 자라지 않게).

    Returns:
        이번에 저장된 `report_id`.
    """
    job_key = normalize_job(job)
    fingerprint = posting_fingerprint(requirements)
    stamp = (now or dt.datetime.now()).isoformat(timespec="seconds")

    existing = conn.execute(
        f"""
        SELECT report_id FROM {TABLE_LAYER1_CACHE}
        WHERE corp_id = ? AND job_key = ? AND posting_fingerprint = ?
        """,
        (corp_id, job_key, fingerprint),
    ).fetchone()

    report_id = uuid.uuid4().hex
    reports_store.save(conn, report_id, corp_id, job, report, created_at=stamp)
    conn.execute(
        f"""
        INSERT INTO {TABLE_LAYER1_CACHE}
            (corp_id, job_key, posting_fingerprint, report_id, fiscal_year, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(corp_id, job_key, posting_fingerprint) DO UPDATE SET
            report_id=excluded.report_id,
            fiscal_year=excluded.fiscal_year,
            created_at=excluded.created_at
        """,
        (corp_id, job_key, fingerprint, report_id, fiscal_year, stamp),
    )
    if existing is not None and existing["report_id"] != report_id:
        conn.execute(
            f"DELETE FROM {TABLE_REPORTS} WHERE report_id = ?", (existing["report_id"],)
        )

    _evict_layer1_overflow(conn, corp_id, job_key, current_fiscal_year=fiscal_year)
    return report_id


def get_company_report_hit(
    conn: sqlite3.Connection,
    *,
    corp_id: str,
    current_fiscal_year: Optional[int] = None,
    today: Optional[dt.date] = None,
) -> Optional[Report]:
    """회사분석 전용 1층 캐시를 조회한다.

    옛 범용 API에 빈 ``job``/빈 공고 지문을 넘기지 않고 명시적인 제품·스키마
    namespace를 사용하므로 과거 직무 보고서와 충돌하지 않는다.
    """
    report = get_layer1_hit(
        conn,
        corp_id=corp_id,
        job=_COMPANY_ANALYSIS_PRODUCT_KEY,
        requirements=list(_COMPANY_ANALYSIS_SCHEMA_REQUIREMENTS),
        current_fiscal_year=current_fiscal_year,
        today=today,
    )
    # 캐시 namespace가 잘못 붙었거나 과거 코드가 v2 payload를 v3 키 아래에
    # 저장했더라도 canonical 보고서처럼 반환하지 않는다.
    if (
        report is None
        or report.schema_version != CANONICAL_SCHEMA_VERSION
        or not validate_publishable(report)
    ):
        return None
    return report


def save_company_report(
    conn: sqlite3.Connection,
    *,
    corp_id: str,
    report: Report,
    fiscal_year: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> str:
    """회사분석 보고서를 제품·스키마 namespace로 격리해 저장한다."""
    validation = validate_publishable(report)
    if not validation:
        raise PublishBlockedError(validation)
    return save_layer1(
        conn,
        corp_id=corp_id,
        job=_COMPANY_ANALYSIS_PRODUCT_KEY,
        requirements=list(_COMPANY_ANALYSIS_SCHEMA_REQUIREMENTS),
        report=report,
        fiscal_year=fiscal_year,
        now=now,
    )


def _evict_layer1_overflow(
    conn: sqlite3.Connection,
    corp_id: str,
    job_key: str,
    *,
    current_fiscal_year: Optional[int],
) -> None:
    """회사×직무당 상한(`LAYER1_MAX_ENTRIES_PER_JOB`)을 넘으면 오래된 것부터 지운다.

    정본 §보관 상한 — "축출 우선 대상: 사업연도가 바뀐(신선도 만료) 보고서".
    사업연도가 이번에 저장한 값과 다른 항목을 먼저, 그다음 오래된 것부터 지운다.
    """
    rows = conn.execute(
        f"""
        SELECT id, report_id, fiscal_year, created_at FROM {TABLE_LAYER1_CACHE}
        WHERE corp_id = ? AND job_key = ?
        """,
        (corp_id, job_key),
    ).fetchall()
    overflow = len(rows) - LAYER1_MAX_ENTRIES_PER_JOB
    if overflow <= 0:
        return

    def sort_key(row: sqlite3.Row) -> tuple[int, str]:
        year_differs = (
            current_fiscal_year is not None and row["fiscal_year"] != current_fiscal_year
        )
        stale_first = 0 if year_differs else 1
        return (stale_first, row["created_at"])

    victims = sorted(rows, key=sort_key)[:overflow]
    for victim in victims:
        conn.execute(f"DELETE FROM {TABLE_LAYER1_CACHE} WHERE id = ?", (victim["id"],))
        conn.execute(
            f"DELETE FROM {TABLE_REPORTS} WHERE report_id = ?", (victim["report_id"],)
        )


# ══════════════════════════════════════════════════════════
# 2층 — 회사(고유번호) → 수집 자료 재사용
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Layer2Cache:
    """회사 단위로 재사용하는 수집 자료 한 벌."""

    corp_id: str
    #: 조각 번호 → {"종류":..., "원문":..., "출처":...} (real.py의 `frags`와 같은 모양)
    fragments: dict[int, dict[str, str]]
    #: 최근 공시 메타(보고서명·접수번호 등). 없으면 `None`.
    filing: Optional[dict[str, Any]]
    #: 칸별 판정 결과. 없으면 `None`.
    cell_judgments: Optional[dict[str, bool]]
    #: 수집 당시 최신 사업연도. 04 게이트가 신선도를 볼 때 쓴다.
    fiscal_year: Optional[int]
    collected_at: str
    updated_at: str


def _fragments_to_json(fragments: dict[int, dict[str, str]]) -> str:
    """`dict[int, ...]`를 JSON으로 왕복시킨다.

    ★ JSON 객체 키는 문자열만 허용된다. `{fragment_id: ...}`를 그대로
      `json.dumps`하면 다시 읽을 때 키가 문자열로 바뀐다(정수 조각 번호가
      깨진다). `[id, value]` 쌍의 배열로 감싸 정수 키를 그대로 지킨다.
    """
    return json.dumps([[fid, val] for fid, val in fragments.items()], ensure_ascii=False)


def _fragments_from_json(text: str) -> dict[int, dict[str, str]]:
    pairs = json.loads(text)
    return {int(fid): val for fid, val in pairs}


def save_layer2(
    conn: sqlite3.Connection,
    *,
    corp_id: str,
    fragments: dict[int, dict[str, str]],
    filing: Optional[dict[str, Any]] = None,
    cell_judgments: Optional[dict[str, bool]] = None,
    fiscal_year: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> None:
    """2층 캐시(회사 단위 수집 자료)를 저장한다. 같은 `corp_id`면 통째로 덮어쓴다.

    ★ 정본 §4 — 2층 키는 회사 단위다. 소스별 부분 갱신(예: DART만 새로
      받기)은 1차 범위 밖이라 이 함수도 "전체를 한 번에" 저장·교체한다.
    """
    stamp = (now or dt.datetime.now()).isoformat(timespec="seconds")
    conn.execute(
        f"""
        INSERT INTO {TABLE_LAYER2_CACHE}
            (corp_id, fragments_json, filing_json, cell_judgments_json,
             fiscal_year, collected_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(corp_id) DO UPDATE SET
            fragments_json=excluded.fragments_json,
            filing_json=excluded.filing_json,
            cell_judgments_json=excluded.cell_judgments_json,
            fiscal_year=excluded.fiscal_year,
            updated_at=excluded.updated_at
        """,
        (
            corp_id,
            _fragments_to_json(fragments),
            json.dumps(filing, ensure_ascii=False) if filing is not None else None,
            json.dumps(cell_judgments, ensure_ascii=False)
            if cell_judgments is not None
            else None,
            fiscal_year,
            stamp,
            stamp,
        ),
    )


def get_layer2(conn: sqlite3.Connection, corp_id: str) -> Optional[Layer2Cache]:
    """회사 고유번호로 2층 캐시를 불러온다. 없으면 `None`."""
    row = conn.execute(
        f"SELECT * FROM {TABLE_LAYER2_CACHE} WHERE corp_id = ?", (corp_id,)
    ).fetchone()
    if row is None:
        return None
    return Layer2Cache(
        corp_id=row["corp_id"],
        fragments=_fragments_from_json(row["fragments_json"]),
        filing=json.loads(row["filing_json"]) if row["filing_json"] else None,
        cell_judgments=json.loads(row["cell_judgments_json"])
        if row["cell_judgments_json"]
        else None,
        fiscal_year=row["fiscal_year"],
        collected_at=row["collected_at"],
        updated_at=row["updated_at"],
    )


# ══════════════════════════════════════════════════════════
# 별칭 캐시 (1-b) — 정규화된 통칭 → 회사 고유번호
# ══════════════════════════════════════════════════════════


def get_alias(conn: sqlite3.Connection, typed_name: str) -> Optional[str]:
    """통칭으로 회사 고유번호를 찾는다. 히트하면 층2(AI 이름 대조) 호출을 건너뛴다."""
    row = conn.execute(
        f"SELECT corp_id FROM {TABLE_ALIAS_CACHE} WHERE alias_key = ?",
        (normalize_alias(typed_name),),
    ).fetchone()
    return row["corp_id"] if row is not None else None


def save_alias(
    conn: sqlite3.Connection,
    typed_name: str,
    corp_id: str,
    *,
    now: Optional[dt.datetime] = None,
) -> None:
    """별칭을 저장한다. ★ 정본 — "층2를 거쳐 확인 카드 [맞습니다]를 받은 뒤에만" 불러야 한다.

    이 함수 자체는 그 규칙을 강제하지 않는다(storage는 언제 부를지 모른다) —
    호출부(회사 확정 화면 흐름)가 지켜야 한다.
    """
    stamp = (now or dt.datetime.now()).isoformat(timespec="seconds")
    key = normalize_alias(typed_name)
    conn.execute(
        f"""
        INSERT INTO {TABLE_ALIAS_CACHE} (alias_key, corp_id, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(alias_key) DO UPDATE SET
            corp_id=excluded.corp_id, created_at=excluded.created_at
        """,
        (key, corp_id, stamp),
    )


def invalidate_alias(conn: sqlite3.Connection, typed_name: str) -> None:
    """별칭 하나를 지운다.

    ★ 정본 §1-b — "무효화·수명 = 미결(오클릭으로 오염된 별칭을 되돌릴 경로)".
      1차는 이 수동 삭제 함수만 둔다. 자동 만료(수명)는 아직 값이 없다 —
      나중에 값이 정해지면 `load_alias`에 만료 검사를 추가해야 한다.
    """
    conn.execute(
        f"DELETE FROM {TABLE_ALIAS_CACHE} WHERE alias_key = ?", (normalize_alias(typed_name),)
    )
