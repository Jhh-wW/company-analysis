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

# 캐시 2층 — build/source namespace 도입 전 차단

옛 `layer2_cache`는 회사 고유번호 하나만 키로 써 배포·출처가 다른 수집 자료를
가를 수 없다. 현재 생산 호출은 없으며, 테이블 키와 API가 검증된 build/source
신원을 함께 받도록 migration되기 전까지 읽기·쓰기를 모두 명시적으로 막는다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import sqlite3
import uuid
from typing import Any, Iterable, Optional

from src.core import clock
from src.features.pipeline.port import Report
from src.features.provenance.freshness import is_stale
from src.features.provenance.sources import (
    Source,
    SourceKind,
    official_web_currentness_is_usable,
)
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.report_standard.publish import PublishBlockedError, validate_publishable
from src.features.storage import reports as reports_store
from src.features.storage.constants import (
    LAYER1_MAX_ENTRIES_PER_JOB,
    TABLE_ALIAS_CACHE,
    TABLE_LAYER1_CACHE,
    TABLE_REPORTS,
)
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_source_identity import (
    ReportSourceIdentityError,
    require_financial_payload_digest,
)

_WHITESPACE = re.compile(r"\s+")
#: 정규화한 요구역량 문장을 이어붙일 때 쓰는 구분자. 문장 안에는 절대
#: 나오지 않는 제어문자라(ASCII Unit Separator) 문장이 우연히 겹쳐 지문이
#: 같아지는 사고를 피한다.
_FINGERPRINT_JOIN = "\x1f"

# 회사분석 제품은 옛 ``회사×직무×공고지문`` 캐시와 같은 테이블을 읽지만,
# 제품 namespace·schema version·실제 출처 지문을 모두 키에 넣어 빈 직무/
# 빈 지문인 옛 항목이나 정정 전 DART 자료와 섞이지 않게 한다. 이 값들은
# 보고서 본문이나 사용자 화면에는 노출되지 않는 저장소 내부 식별자다.
_COMPANY_ANALYSIS_PRODUCT_KEY = "product:company-analysis"
_COMPANY_ANALYSIS_SCHEMA_REQUIREMENTS = (f"schema:{CANONICAL_SCHEMA_VERSION}",)


def _commit_connection(conn: sqlite3.Connection) -> None:
    """신원 최종 검사 직후 commit하며 시험은 실패를 이 seam에 주입한다."""

    conn.commit()


def _source_identity_requirement(source_identity_digest: str) -> str | None:
    """완전한 출처 신원 SHA-256만 캐시 열쇠로 허용한다."""

    try:
        digest = require_financial_payload_digest(
            source_identity_digest,
            allow_empty=False,
        )
    except ReportSourceIdentityError:
        return None
    return f"source:{digest}"


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

    # ★ 「오늘」은 반드시 KST 다 (2026-08-27 실측으로 찾은 결함).
    #   수집일(`collected_at`)은 저장할 때 KST 로 적는다(`real.py` 의 `today_kst()`).
    #   그런데 여기서 «서버 로컬 날짜»로 재면 서버가 UTC 일 때 하루가 어긋나,
    #   방금 저장한 자료가 «미래에 수집된 것»(age_days = -1)이 되어 캐시가 통째로 거절된다.
    #   실측: 리눅스(UTC) 에서 UTC 15:00~24:00 (= KST 00:00~09:00) 동안
    #   같은 회사를 다시 조사하면 캐시가 «절대» 안 먹어 본조사 비용이 또 나갔다.
    #   재현법: `TZ=UTC0` 로 test_real_cache.py 를 돌리면 4건이 빨간불이 된다.
    reference_date = today or clock.today_kst()
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
        # ★ 위에서 정한 `reference_date`(KST)를 그대로 넘긴다.
        #   인자 `today`(호출부에서 보통 None)를 넘기면 `freshness.is_stale` 안에서
        #   다시 «서버 로컬 날짜»로 떨어져, 같은 함수 안에서 기준일이 두 개가 된다.
        #   여기는 3년 창이라 하루 차이로 판정이 뒤집히진 않지만, 기준을 하나로 둔다.
        if is_stale(date_str, today=reference_date) is True:
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
    engine_build_identity: build_identity_contract.EngineBuildIdentity,
    fiscal_year: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> str:
    """1층 캐시에 완성된 보고서를 저장한다.

    같은 (corp_id, job, requirements)로 다시 부르면 **덮어쓴다**(새 report_id로
    바뀌고, 더 이상 아무도 안 가리키게 된 옛 보고서 본문은 지운다 — DB가
    무한히 자라지 않게). 이 함수가 해당 layer1 transaction의 commit까지
    소유하므로 더 큰 원자 transaction 안의 부분 쓰기로 사용하지 않는다.

    Returns:
        이번에 저장된 `report_id`.
    """
    if not isinstance(
        engine_build_identity,
        build_identity_contract.EngineBuildIdentity,
    ) or not engine_build_identity.cache_usable:
        raise ValueError("검증된 정상 배포의 엔진 빌드 신원이 필요합니다")
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
    build_identity_contract.assert_engine_build_identity_current(
        engine_build_identity
    )
    reports_store.save(conn, report_id, corp_id, job, report, created_at=stamp)
    # 본문 INSERT 뒤 환경이 바뀌었으면 아래 cache INSERT를 하지 않고 호출
    # transaction 전체를 rollback하게 한다.
    build_identity_contract.assert_engine_build_identity_current(
        engine_build_identity
    )
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
    build_identity_contract.assert_engine_build_identity_current(
        engine_build_identity
    )
    # 이 함수가 layer1 본문·열쇠 transaction을 소유한다. context manager의
    # __exit__에 commit을 미루면 마지막 fence와 실제 영속화 사이가 다시 열린다.
    _commit_connection(conn)
    return report_id


def get_company_report_hit(
    conn: sqlite3.Connection,
    *,
    corp_id: str,
    build_id: str,
    source_identity_digest: str,
    current_fiscal_year: Optional[int] = None,
    today: Optional[dt.date] = None,
) -> Optional[Report]:
    """회사분석 전용 1층 캐시를 조회한다.

    옛 범용 API에 빈 ``job``/빈 공고 지문을 넘기지 않고 명시적인 제품·스키마·
    출처 namespace를 사용하므로 과거 직무 보고서나 정정 전 자료와 충돌하지 않는다.
    """
    requirements = _company_requirements(build_id, source_identity_digest)
    if not requirements:
        return None
    report = get_layer1_hit(
        conn,
        corp_id=corp_id,
        job=_COMPANY_ANALYSIS_PRODUCT_KEY,
        requirements=requirements,
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
    build_identity: build_identity_contract.EngineBuildIdentity,
    source_identity_digest: str,
    fiscal_year: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> Optional[str]:
    """회사분석 보고서를 제품·스키마 namespace로 격리해 저장한다."""
    if not isinstance(build_identity, build_identity_contract.EngineBuildIdentity):
        raise TypeError("검증된 엔진 빌드 신원이 필요합니다")
    requirements = _company_requirements(build_identity.build_id, source_identity_digest)
    if not requirements:
        return None
    validation = validate_publishable(report)
    if not validation:
        raise PublishBlockedError(validation)
    return save_layer1(
        conn,
        corp_id=corp_id,
        job=_COMPANY_ANALYSIS_PRODUCT_KEY,
        requirements=requirements,
        report=report,
        engine_build_identity=build_identity,
        fiscal_year=fiscal_year,
        now=now,
    )


def _company_requirements(build_id: str, source_identity_digest: str) -> list[str]:
    """v1도 배포 빌드와 실제 출처가 모두 확정됐을 때만 쓰는 열쇠."""

    source_requirement = _source_identity_requirement(source_identity_digest)
    if not build_identity_contract.build_id_is_usable(build_id) or source_requirement is None:
        return []
    return [
        *_COMPANY_ANALYSIS_SCHEMA_REQUIREMENTS,
        f"build:{build_id}",
        source_requirement,
    ]


def _v2_requirements(build_id: str, source_identity_digest: str) -> list[str]:
    """v2 캐시 namespace — 스키마 + 코드 지문 + 실제 출처 지문.

    ★ 코드 지문을 열쇠에 넣는 이유 (오늘 실측으로 당한 사고)
      캐시가 옛 보고서를 물고 오면 「엔진을 고쳐도 화면이 그대로」가 된다.
      v2-26에서 «v2는 캐시를 아예 안 읽는다»로 막았지만, 그 대가로 같은
      회사를 두 번 조사하면 두 번 다 본조사 비용이 나갔다.
      지문을 열쇠에 넣으면 둘 다 해결된다 — 코드가 그대로면 적중해 돈을
      아끼고, 한 글자라도 바뀌면 저절로 불일치라 옛 결과가 절대 안 나온다.
      사람이 「캐시를 비워야지」를 기억할 필요가 없다.
      실제 DART 접수번호나 정규화한 재무 응답이 달라져도 같은 원리로
      열쇠가 달라지므로, 같은 사업연도 안의 정정도 놓치지 않는다.
    """
    source_requirement = _source_identity_requirement(source_identity_digest)
    if source_requirement is None:
        return []
    return [
        f"schema:{ENGINE_V2_SCHEMA_VERSION}",
        f"build:{build_id}",
        source_requirement,
    ]


def get_v2_report_hit(
    conn: sqlite3.Connection,
    *,
    corp_id: str,
    build_id: str,
    source_identity_digest: str,
    current_fiscal_year: Optional[int] = None,
    today: Optional[dt.date] = None,
) -> Optional[Report]:
    """엔진 v2 보고서 전용 1층 캐시를 조회한다.

    ★ v1 캐시와 «열쇠가 다르다» — 서로의 보고서를 절대 못 꺼낸다.
    ★ 지문을 못 만들었으면(«모르는 상태») 조회하지 않는다.
      「모르겠다」를 「같다」로 바꾸면 옛 결과가 새 결과인 척 나간다.
    """
    requirements = _v2_requirements(build_id, source_identity_digest)
    if not build_identity_contract.build_id_is_usable(build_id) or not requirements:
        return None
    report = get_layer1_hit(
        conn,
        corp_id=corp_id,
        job=_COMPANY_ANALYSIS_PRODUCT_KEY,
        requirements=requirements,
        current_fiscal_year=current_fiscal_year,
        today=today,
    )
    # 열쇠가 맞아도 «내용»이 v2가 아니면 안 준다. 저장 경로가 잘못된 과거
    # 코드가 남긴 것을 v2인 척 돌려주지 않기 위한 이중 확인이다.
    if report is None or report.schema_version != ENGINE_V2_SCHEMA_VERSION:
        return None
    return report


def save_v2_report(
    conn: sqlite3.Connection,
    *,
    corp_id: str,
    report: Report,
    build_identity: build_identity_contract.EngineBuildIdentity,
    source_identity_digest: str,
    fiscal_year: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> Optional[str]:
    """엔진 v2 보고서를 «그 코드 지문»과 함께 저장한다.

    Returns:
        저장한 보고서 id. 저장하지 않았으면 ``None``.

    ★ v1의 canonical 출고 게이트(validate_publishable)를 태우지 않는다 —
      v2는 그 게이트를 지나지 않는 다른 계약이고, 이미 출고 직전에
      validate_v2를 통과한 보고서만 여기 온다(real.py).
    ★ 스키마가 v2가 아니면 «조용히 안 저장한다». v1 보고서가 v2 열쇠 아래
      들어가면 다음 조사에서 v1이 v2인 척 나온다.
    """
    if not isinstance(build_identity, build_identity_contract.EngineBuildIdentity):
        raise TypeError("검증된 엔진 빌드 신원이 필요합니다")
    requirements = _v2_requirements(build_identity.build_id, source_identity_digest)
    if not build_identity.cache_usable or not requirements:
        return None
    if report.schema_version != ENGINE_V2_SCHEMA_VERSION:
        return None
    return save_layer1(
        conn,
        corp_id=corp_id,
        job=_COMPANY_ANALYSIS_PRODUCT_KEY,
        requirements=requirements,
        report=report,
        engine_build_identity=build_identity,
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


class Layer2CacheIdentityRequiredError(RuntimeError):
    """corp-only 2층 캐시는 검증된 build/source namespace 없이는 사용할 수 없다."""


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
    """옛 corp-only 2층 쓰기를 명시적으로 차단한다.

    생산 호출은 없으며, build/source namespace가 스키마와 API에 함께 들어오기
    전에는 옛 행을 새 배포 결과로 덮어쓸 수 없다.
    """
    raise Layer2CacheIdentityRequiredError(
        "corp-only 2층 캐시는 build/source 신원 없이 저장할 수 없습니다"
    )


def get_layer2(conn: sqlite3.Connection, corp_id: str) -> None:
    """옛 corp-only 2층 읽기를 명시적으로 차단한다."""
    raise Layer2CacheIdentityRequiredError(
        "corp-only 2층 캐시는 build/source 신원 없이 재사용할 수 없습니다"
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
