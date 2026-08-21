"""Canonical company identities for the approved G3.5 25-case pilot.

The input spelling and the expected DART identity are deliberately separate.
The runner must never infer a corp code from a name at execution time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Final

from src.features.pilot_evaluation.contract import PilotCategory


@dataclass(frozen=True)
class CanonicalPilotCase:
    case_id: str
    category: PilotCategory
    input_name: str
    expected_legal_name: str
    corp_code: str
    stock_code: str = ""
    address_hint: str = ""
    job: str = ""
    posting_text: str = ""


CANONICAL_PILOT_CASES: Final[tuple[CanonicalPilotCase, ...]] = (
    CanonicalPilotCase("P01", PilotCategory.LISTED, "삼성전자", "삼성전자", "00126380", "005930"),
    CanonicalPilotCase("P02", PilotCategory.LISTED, "현대자동차", "현대자동차", "00164742", "005380"),
    CanonicalPilotCase("P03", PilotCategory.LISTED, "LG전자", "LG전자", "00401731", "066570"),
    CanonicalPilotCase("P04", PilotCategory.LISTED, "SK하이닉스", "SK하이닉스", "00164779", "000660"),
    CanonicalPilotCase(
        "P05", PilotCategory.LISTED, "카카오", "카카오", "00258801", "035720", "제주 제주시"
    ),
    CanonicalPilotCase(
        "P06", PilotCategory.LISTED, "카카오페이", "카카오페이", "01244601", "377300", "경기 성남시"
    ),
    CanonicalPilotCase(
        "P07", PilotCategory.LISTED, "파마리서치", "파마리서치", "00970453", "214450", "강원 강릉시"
    ),
    CanonicalPilotCase(
        "P08", PilotCategory.LISTED, "플래티어", "플래티어", "01454341", "367000", "서울 송파구"
    ),
    CanonicalPilotCase(
        "P09", PilotCategory.LISTED, "로보스타", "로보스타", "00536523", "090360", "경기 안산시"
    ),
    CanonicalPilotCase(
        "P10", PilotCategory.LISTED, "하이브", "하이브", "01204056", "352820", "서울 용산구"
    ),
    CanonicalPilotCase(
        "P11",
        PilotCategory.UNLISTED_DISCLOSURE,
        "우리엔",
        "우리엔",
        "01476787",
        address_hint="경기 화성시",
    ),
    CanonicalPilotCase(
        "P12",
        PilotCategory.UNLISTED_DISCLOSURE,
        "토스씨엑스",
        "토스씨엑스",
        "01724143",
        address_hint="서울 강남구",
    ),
    CanonicalPilotCase(
        "P13",
        PilotCategory.UNLISTED_DISCLOSURE,
        "앱솔브랩",
        "앱솔브랩",
        "01921621",
        address_hint="서울 강남구",
    ),
    CanonicalPilotCase(
        "P14",
        PilotCategory.UNLISTED_DISCLOSURE,
        "넥스트증권",
        "넥스트증권",
        "00251349",
        address_hint="서울 영등포구",
    ),
    CanonicalPilotCase(
        "P15",
        PilotCategory.UNLISTED_DISCLOSURE,
        "글로벌머니익스프레스",
        "글로벌머니익스프레스",
        "01538731",
        address_hint="서울 영등포구",
    ),
    CanonicalPilotCase(
        "P16",
        PilotCategory.UNLISTED_DISCLOSURE,
        "콜로세움코퍼레이션",
        "콜로세움코퍼레이션",
        "01928776",
        address_hint="서울 강남구",
    ),
    CanonicalPilotCase(
        "P17",
        PilotCategory.UNLISTED_DISCLOSURE,
        "인텍에프에이",
        "인텍에프에이",
        "00674966",
        address_hint="경기 용인시",
    ),
    CanonicalPilotCase(
        "P18",
        PilotCategory.UNLISTED_DISCLOSURE,
        "카카오모빌리티",
        "카카오모빌리티",
        "01250666",
        address_hint="경기 성남시",
    ),
    CanonicalPilotCase(
        "P19", PilotCategory.SPARSE_OR_AMBIGUOUS, "YG", "와이지엔터테인먼트", "00613318", "122870"
    ),
    CanonicalPilotCase(
        "P20", PilotCategory.SPARSE_OR_AMBIGUOUS, "JYP", "JYP Ent.", "00258689", "035900", "서울 강동구"
    ),
    CanonicalPilotCase(
        "P21", PilotCategory.SPARSE_OR_AMBIGUOUS, "에스엠", "에스엠", "00260930", "041510", "서울 성동구"
    ),
    CanonicalPilotCase(
        "P22", PilotCategory.SPARSE_OR_AMBIGUOUS, "네이버", "NAVER", "00266961", "035420"
    ),
    CanonicalPilotCase(
        "P23", PilotCategory.SPARSE_OR_AMBIGUOUS, "11번가", "십일번가", "01326206"
    ),
    CanonicalPilotCase(
        "P24", PilotCategory.SPARSE_OR_AMBIGUOUS, "배달의민족", "우아한형제들", "01063273"
    ),
    CanonicalPilotCase(
        "P25",
        PilotCategory.SPARSE_OR_AMBIGUOUS,
        "엠투아이코퍼레이션",
        "엠엑스온",
        "00670766",
        "347890",
        "경기 안양시",
    ),
)

# The 25-case manifest remains the comparison set, while the user's current
# paid authorization is deliberately narrower.  Keep this policy next to the
# immutable identities so every execution entry point can fail closed.
APPROVED_PAID_CASE_IDS: Final[frozenset[str]] = frozenset(
    f"P{index:02d}" for index in range(1, 11)
)


def manifest_sha256(
    cases: tuple[CanonicalPilotCase, ...] = CANONICAL_PILOT_CASES,
) -> str:
    payload = [
        {**asdict(case), "category": case.category.value}
        for case in cases
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_manifest(
    cases: tuple[CanonicalPilotCase, ...] = CANONICAL_PILOT_CASES,
) -> None:
    expected_ids = tuple(f"P{index:02d}" for index in range(1, 26))
    if tuple(case.case_id for case in cases) != expected_ids:
        raise ValueError("파일럿 case ID는 P01부터 P25까지 순서대로 한 번씩 필요합니다")
    counts = {
        category: sum(case.category is category for case in cases)
        for category in PilotCategory
    }
    expected_counts = {
        PilotCategory.LISTED: 10,
        PilotCategory.UNLISTED_DISCLOSURE: 8,
        PilotCategory.SPARSE_OR_AMBIGUOUS: 7,
    }
    if counts != expected_counts:
        raise ValueError("파일럿 구성은 상장 10·비상장 공시 8·경계 7이어야 합니다")
    if any(
        len(case.corp_code) != 8
        or not case.corp_code.isdigit()
        or (case.stock_code and (len(case.stock_code) != 6 or not case.stock_code.isdigit()))
        or not case.input_name.strip()
        or not case.expected_legal_name.strip()
        or case.job
        or case.posting_text
        for case in cases
    ):
        raise ValueError("파일럿 법인 식별값 또는 회사분석 전용 빈 입력 계약이 올바르지 않습니다")
    if len({case.corp_code for case in cases}) != len(cases):
        raise ValueError("파일럿 DART 고유번호가 중복되었습니다")
