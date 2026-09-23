"""근거 후보 생산부의 예산·추정 비율·회사유형별 기대 경로 상수.

매직 넘버를 코드 곳곳에 흩어놓지 않기 위해 여기 한 곳에 모은다. 값을 바꿀
일이 생기면 이 파일만 고치면 된다 (매직 넘버 금지 정합).
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from src.shared.report_evidence.constants import (
    CollectionState,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


CHAPTER_EVIDENCE_PRODUCER_VERSION: Final[str] = "chapter-evidence-producer-v1"
SELECTION_CHANGE_CONTEXT: Final[str] = "selection_change_context"
SELECTION_RECENT_CONTEXT: Final[str] = "selection_recent_context"

# ── 감사인 표준 문구 조각 — 근거 선별에서 사업 칸을 주지 않는다 ─────────────
# ★ 왜 필요한가(2026-09-23 5차 유료 실행, 독립 검수 상-3) — 감사보고서 「감사인의
#   책임」 단락(주어는 감사인 「우리」)이 5장 과제·대응 칸을 받아 작가가 감사인의
#   감사절차·감사증거 입수를 회사의 대응으로 옮겨 적었다. 수집 엔진이 근원에서
#   막지만(analysis_engine …/auditor_boilerplate.py), AI 재판정·저장된 수집 결과·
#   다른 수집기로 들어온 조각도 이 선별 한 곳을 지나므로 여기서 한 번 더 거른다.
# ★ 값은 엔진 사본과 «같은 글자»다 — 엔진은 app을 import할 수 없고, composer
#   사본(audit_boilerplate_constants.py)과도 서로 import하지 않는다. 세 벌은
#   tests/test_auditor_boilerplate_parity.py가 ast로 대조한다. 값의 근거는 엔진
#   constants.py 주석에 있다.
AUDITOR_BOILERPLATE_MARKERS: Final[tuple[str, ...]] = (
    # 감사보고서 머리말·수신인·서명 문형
    "독립된감사인",
    "이사회귀중",
    "감사보고서의근거가된감사",
    # 「감사보고서일」 단독은 회사 주석의 기준일(「감사보고서일 현재 소송 결과를 예측할 수
    # 없습니다」)에도 쓰인다 — 감사인 문형 세 가지만 남긴다(2026-09-23 B 수정 재검토 R2).
    "감사보고서일까지입수",
    "감사보고서일현재로유효",
    "감사보고서일후",
    "감사보고서를발행",
    # 감사의견·감사의견근거 단락
    "감사인의책임",
    "우리의의견으로는",
    "재무제표를감사하였",
    "내부회계관리제도를감사하였",
    "유의적인회계정책의요약을포함한",
    "재무제표감사",
    "감사기준",
    "독립성관련윤리적요구사항",
    "감사받지아니한",
    # 감사인 책임 단락의 감사 행위
    "감사증거",
    "감사절차를설계",
    "감사와관련된내부통제",
    "효과성에대한의견을표명",
    "의견을변형",
    "의견형성",
    # 사업보고서에 옮겨 실린 핵심감사사항 머리 문단의 감사인 문형 — 구조 표지로도 쓴다(R6).
    "우리의의견형성",
    "수행한주요감사절차",
    "전문가적회의주의",
    # 「전문가적 회의주의」의 실제 번역 변형 — 5차 감사보고서 원문이 이 꼴이었다(R7).
    "전문가적의구심",
    "감사범위와감사시기",
    "발견하지못할위험",
    "왜곡표시는중요하다고간주",
    "계속기업전제의적절성",
    "전반적인표시와구조",
    "회계추정치와관련공시의합리성",
    # 경영진·지배기구 책임 단락(감사보고서 안의 표준 문형)
    "경영진과지배기구의책임",
    "공정하게표시할책임",
    "계속기업전제의사용",
    "계속기업관련사항을공시할책임",
    "재무보고절차의감시",
)
AUDITOR_BOILERPLATE_EXCLUDED_COMPOUNDS: Final[dict[str, tuple[str, ...]]] = {
    "감사기준": ("내부감사기준",),
    # 회사 내부·자체·품질 감사 조직의 절차 설계는 감사인 행위가 아니다 — composer
    # 후보 쪽 행위 표지와 같은 면제(2026-09-23 독립 검토 F4).
    "감사절차를설계": ("내부감사절차를설계", "자체감사절차를설계", "품질감사절차를설계"),
}
#: 감사보고서에만 나오는 «구조 표지» — 문서 종류를 모르는 자리에서는 글에 이 표지가
#: 있을 때만 감사인 문구 판정을 건다(AUDITOR_BOILERPLATE_MARKERS 의 부분집합).
#: ★ 왜 필요한가(2026-09-23 독립 검토 F1) — 「감사증거」「감사기준」「재무제표감사」
#:   「감사보고서를 발행」 같은 짧은 표지는 감사 소프트웨어·감사 서비스 회사의 사업
#:   문장에도 나온다. 그 낱말만으로 판정하면 그 업종의 사업 문단이 통째로 버려진다.
#:   여기에는 감사인 보고서 문형에만 나오는 긴 표현만 둔다.
#: ★ 머리말 표지(「독립된감사인」 등)만으로는 부족하다 — 5차 실측 조각 12(감사인 책임
#:   단락)에는 머리말이 없었다. 구조 표지는 긴 감사인 문형 5개였다(감사보고서일까지 입수·
#:   발견하지 못할 위험·계속기업전제의 적절성·전반적인 표시와 구조·회계추정치와 관련 공시의 합리성).
#: ★ 「감사보고서일」 단독은 넣지 않는다(2026-09-23 B 수정 재검토 R2) — 실제 사업보고서의 회사
#:   주석(「감사보고서일 현재 … 영향을 예측할 수 없습니다」)이 이 낱말만으로 관문을 열고 문단째
#:   버려졌다. 감사인만 쓰는 세 문형(까지 입수·현재로 유효·후)으로 좁혔다.
#: ★ 「우리의 의견형성」(사업보고서의 핵심감사사항 머리 문단, R6)과 「전문가적 의구심」(R7)을 더했다.
#:   실제 공시 22건에서 두 문형은 감사인 문단에만 나왔다.
AUDITOR_STRUCTURAL_MARKERS: Final[tuple[str, ...]] = (
    "독립된감사인",
    "이사회귀중",
    "감사보고서의근거가된감사",
    "감사보고서일까지입수",
    "감사보고서일현재로유효",
    "감사보고서일후",
    "감사인의책임",
    "우리의의견으로는",
    "재무제표를감사하였",
    "내부회계관리제도를감사하였",
    "유의적인회계정책의요약을포함한",
    "독립성관련윤리적요구사항",
    "우리의의견형성",
    "수행한주요감사절차",
    "전문가적회의주의",
    "전문가적의구심",
    "감사범위와감사시기",
    "발견하지못할위험",
    "왜곡표시는중요하다고간주",
    "계속기업전제의적절성",
    "전반적인표시와구조",
    "회계추정치와관련공시의합리성",
    "경영진과지배기구의책임",
    "계속기업전제의사용",
    "계속기업관련사항을공시할책임",
    "재무보고절차의감시",
)
AUDITOR_BOILERPLATE_EXEMPTION_PATTERN: Final[str] = (
    r"\d[\d,.]*(?:조|억|만|천|백만|십억)?원|위반|과징금|제재|재작성|오류수정"
)
AUDITOR_CLAUSE_SPLIT_PATTERN: Final[str] = r"(?<=다)\.|[.。!?](?=\s|$)|[\r\n]+"
AUDITOR_CONTENT_CHAR_PATTERN: Final[str] = r"[0-9A-Za-z가-힣]"
AUDITOR_TRIVIAL_CLAUSE_MAX_CONTENT_CHARS: Final[int] = 9
#: 감사인 표준 문구뿐인 조각을 선별에서 뺀 건수 사유(「…:<건수>」 꼴로 남긴다).
#: 자료 부족이 아니라 «알아본 비사업 문구»이므로 내부 결속 오류 접두와 섞지 않는다.
AUDITOR_BOILERPLATE_FRAGMENT_IGNORED: Final[str] = "auditor_boilerplate_fragment_ignored"
#: 문서 종류에 따른 감사인 문구 판정 범위(2026-09-23 독립 검토 F1).
#: · 감사보고서: 구조 표지가 없어도 판정한다 — 감사인 책임 단락 조각은 머리말 없이
#:   잘려 오기도 한다.
#: · DART 정기보고서(사업·반기·분기): 감사인 의견 절이 섞일 수 있어, 조각에 구조 표지가
#:   있을 때만 판정한다.
#: · 그 밖(홈페이지·IR·뉴스 등): 회사나 언론이 쓴 글이라 판정하지 않는다 — 감사 서비스·
#:   감사 소프트웨어 회사의 사업 문장을 지킨다.
AUDITOR_JUDGED_SOURCE_KINDS: Final[frozenset[str]] = frozenset({
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
})
AUDITOR_STRUCTURE_GATED_SOURCE_KINDS: Final[frozenset[str]] = frozenset({
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
})

# 근거 원문 문자 수 → 예상 토큰 추정 비율.
# 한국어 위주 원문은 토크나이저별로 평균 1토큰≈2~2.6자 범위를 보인다. 예산을
# 실제보다 «적게» 추정해 나중에 호출이 넘치는 사고를 피하려고 범위의 낮은 쪽인
# 2.2자/토큰(=더 많은 토큰으로 추정)을 안전 마진으로 쓴다.
CHARS_PER_ESTIMATED_TOKEN: Final[float] = 2.2

# 장 하나가 받는 근거 원문 문자 예산. 수집 슬롯이 가장 많은 장(business_model,
# 3칸)이 슬롯당 여유 있게(약 3천자) 채워도 넘치지 않도록 잡았다.
DEFAULT_MAX_CHARS_PER_SECTION: Final[int] = 12000

# 위 문자 예산을 CHARS_PER_ESTIMATED_TOKEN 로 전부 환산해도 남는 여유를 두어,
# 문자 예산이 실질적인 상한이 되고 토큰 예산이 우발적으로 먼저 걸리지 않게 한다.
DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION: Final[int] = 6000

# build_section_bundle(logic.py)이 UNKNOWN으로 보는 두 조회 상태와 같은 집합을
# 생산부 진단에서도 그대로 쓴다 — 같은 방향의 판정이어야 하기 때문이다.
FAILURE_COLLECTION_STATES: Final[frozenset[CollectionState]] = frozenset(
    {CollectionState.FAILED, CollectionState.TRUNCATED}
)
# 정상적으로 확인했지만 자료가 없었던 상태 — INSUFFICIENT 방향의 근거가 된다.
OBSERVED_ABSENCE_COLLECTION_STATES: Final[frozenset[CollectionState]] = frozenset(
    {CollectionState.OK, CollectionState.MISSING}
)


class CompanyType(str, Enum):
    """생산부가 아는 회사 자료 형태 — 어느 조회 경로가 필수인지만 바꾼다.

    슬롯 요구 자체(REQUIRED_EVIDENCE_SLOTS_BY_SECTION)는 유형과 무관하게 항상
    같다. 유형이 바꾸는 것은 «그 슬롯을 확인하는 정상 경로가 공시인지 공식
    웹인지» 뿐이다.

    UNDECIDED: DART 수집기가 판정 근거 문서를 하나도 확보하지 못해 회사
    유형 자체를 아직 못 정한 경우. 기대 경로를 «모름»으로 다룬다 — 특정
    접두어를 기대하지 않으므로 REQUIRED_PATH_PREFIX_BY_COMPANY_TYPE에
    등록하지 않고, diagnose.py도 이 값에는 기대 경로 가산 확인을 적용하지
    않는다(계약과 완전히 같은 판정만 쓴다).
    """

    LISTED = "listed"
    AUDIT_ONLY = "audit_only"
    FINANCIAL = "financial"
    UNDECIDED = "undecided"


# CollectionAttempt.source_kind 접두어 — 실제 접두어는 수집기(공시 수집기
# ``evidence_collection``, 공식 웹 수집기 ``homepage.wide_collect``) 쪽
# 상수가 정본이다. 여기서는 그 접두어«패턴»만 참조한다.
DART_SOURCE_KIND_PREFIX: Final[str] = "dart_"
OFFICIAL_SOURCE_KIND_PREFIX: Final[str] = "official_"

_LISTED_DEFAULT_PREFIXES: Final[dict[str, str]] = {
    section_id: DART_SOURCE_KIND_PREFIX for section_id in REQUIRED_EVIDENCE_SECTION_IDS
}

# listed: 공시(dart_*)가 대부분 슬롯의 REQUIRED, 웹은 OPTIONAL 보강.
_REQUIRED_PATH_PREFIX_LISTED: Final[dict[str, str]] = dict(_LISTED_DEFAULT_PREFIXES)

# audit_only(감사보고서만 내는 회사): 사업 설명·조직문화·전략처럼 감사보고서에
# 잘 안 담기는 서술형 내용은 공식 웹이 REQUIRED. 실적·이행 같은 재무성 내용은
# 여전히 공시(감사보고서)가 REQUIRED — past_changes:completed_execution 등.
# 명시되지 않은 나머지 장(business_model·current_challenges·
# operations_partners)은 감사보고서 주석에 담기는 재무·운영 서술이라 dart_ 를
# 기본값으로 둔다. ⚠️ 확인 못 함 — 실데이터로 이 기본값을 검증하지 않았다.
_REQUIRED_PATH_PREFIX_AUDIT_ONLY: Final[dict[str, str]] = {
    **_LISTED_DEFAULT_PREFIXES,
    "identity": OFFICIAL_SOURCE_KIND_PREFIX,
    "portfolio": OFFICIAL_SOURCE_KIND_PREFIX,
    "future_strategy": OFFICIAL_SOURCE_KIND_PREFIX,
    "culture": OFFICIAL_SOURCE_KIND_PREFIX,
    "competitive_position": OFFICIAL_SOURCE_KIND_PREFIX,
}

# financial(금융형): listed와 기대 경로가 같다. 「매출액」 대신 이자수익 등
# 대체 지표 원문을 허용하는 것은 경로 정책이 아니라 select.py가 애초에 특정
# 지표 키워드로 조각을 거르지 않는 것으로 보장된다 — 여기서는 경로만 다룬다.
_REQUIRED_PATH_PREFIX_FINANCIAL: Final[dict[str, str]] = dict(_LISTED_DEFAULT_PREFIXES)

REQUIRED_PATH_PREFIX_BY_COMPANY_TYPE: Final[dict[CompanyType, dict[str, str]]] = {
    CompanyType.LISTED: _REQUIRED_PATH_PREFIX_LISTED,
    CompanyType.AUDIT_ONLY: _REQUIRED_PATH_PREFIX_AUDIT_ONLY,
    CompanyType.FINANCIAL: _REQUIRED_PATH_PREFIX_FINANCIAL,
}

# 접두어는 화면·옛 진단 호환 설명에만 남긴다. 실제 판정은 아래 닫힌 종류
# 집합으로 한다. 그렇지 않으면 ``dart_typo``·``official_fake``도 정상 필수
# 경로로 오인된다.
_DART_REQUIRED_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        SOURCE_KIND_DART_BUSINESS_REPORT,
        SOURCE_KIND_DART_AUDIT_REPORT,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT,
        SOURCE_KIND_DART_QUARTERLY_REPORT,
    }
)
_OFFICIAL_REQUIRED_SOURCE_KINDS: Final[frozenset[str]] = OFFICIAL_WEB_SOURCE_KINDS

REQUIRED_SOURCE_KINDS_BY_COMPANY_TYPE: Final[
    dict[CompanyType, dict[str, frozenset[str]]]
] = {
    company_type: {
        section_id: (
            _DART_REQUIRED_SOURCE_KINDS
            if prefix == DART_SOURCE_KIND_PREFIX
            else _OFFICIAL_REQUIRED_SOURCE_KINDS
        )
        for section_id, prefix in by_section.items()
    }
    for company_type, by_section in REQUIRED_PATH_PREFIX_BY_COMPANY_TYPE.items()
}


def expected_required_path_prefix(company_type: CompanyType, section_id: str) -> str:
    """이 회사유형에서 이 장의 필수 슬롯을 정상 확인하는 source_kind 접두어."""

    try:
        by_section = REQUIRED_PATH_PREFIX_BY_COMPANY_TYPE[company_type]
    except KeyError as error:
        raise ValueError(f"알 수 없는 회사 유형입니다: {company_type!r}") from error
    try:
        return by_section[section_id]
    except KeyError as error:
        raise ValueError(f"알 수 없는 근거 장 식별자입니다: {section_id}") from error


def expected_required_source_kinds(
    company_type: CompanyType,
    section_id: str,
) -> frozenset[str]:
    """회사유형·장에 허용된 필수 조회 종류를 정확 일치 집합으로 돌려준다."""

    try:
        by_section = REQUIRED_SOURCE_KINDS_BY_COMPANY_TYPE[company_type]
    except KeyError as error:
        raise ValueError(f"알 수 없는 회사 유형입니다: {company_type!r}") from error
    try:
        return by_section[section_id]
    except KeyError as error:
        raise ValueError(f"알 수 없는 근거 장 식별자입니다: {section_id}") from error
