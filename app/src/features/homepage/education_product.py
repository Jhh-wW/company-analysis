"""공식 상품 문서에서 현재 제공하는 실습 교육상품 설명만 고른다."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from src.features.homepage.wide_types import WideDocumentIdentity


EDUCATION_PRODUCT_SLOT = "portfolio:product_role"
_EDITORIAL_PATH_RE = re.compile(r"(?:^|/)(?:blog|news|press|tutorial|review)(?:/|$)", re.I)
_ENROLLMENT_RE = re.compile(r"수강\s*신청")
_DESCRIPTION_RE = re.compile(
    r"(?:실습형\s*(?:무료\s*|유료\s*)?(?:특강|강좌|교육\s*과정)|"
    r"(?:무료\s*|유료\s*)?실습\s*(?:특강|강좌|교육\s*과정))\s*(?:입니다|이다)[.!。]?\s*$"
)
_LEARNING_RELATION_RE = re.compile(
    r"(?:활용해|활용하여|이용해|이용하여)[^.!。]{4,}(?:만드는|제작하는|분석하는|구현하는)"
)
_NON_PRODUCT_RE = re.compile(
    r"사내|내부\s*교육|임직원|직원\s*(?:대상|교육|전용)|타사|다른\s*회사|"
    r"제삼자|제3자|외부\s*(?:회사|업체)|소개하는|소개합니다|추천하는|추천합니다|"
    r"예정|계획|향후|종료|폐강|중단|폐지|개최했|진행했|제공했|열렸"
)
_CLOSED_COURSE_RE = re.compile(
    r"(?:특강|강좌|과정|수강|모집|신청|교육)[^.!。]{0,25}(?:종료|폐강|중단|예정)|"
    r"(?:종료|폐강|중단|예정)[^.!。]{0,25}(?:특강|강좌|과정|수강|모집|신청|교육)"
)
_COURSE_SUBJECT_RE = re.compile(r"^(?:본|이|해당|이번)\s*(?:특강|강좌|과정|교육)(?:는|은|가|이)\s*")
_CONDITIONAL_RE = re.compile(r"경우|(?:되는|될|한|할)\s*(?:때|시)|(?:하|되)면|(?:한|된)다면")
_CUSTOMER_STAFF_RE = re.compile(r"고객사\s*(?:임직원|직원)")
_UNIT_RE = re.compile(r"(?<=[.!?。])\s+|[\n\r]")
_REGISTRATION_SUBJECT_RE = re.compile(r"^(?:(?:본|이)\s*)?(?:(?:특강|강좌|과정)(?:의)?\s*)?(?:수강\s*)?(?:신청|모집)")
_CLOSED_STATUS_RE = re.compile(r"종료|폐강|중단")


def _non_product_relation(text: str) -> bool:
    """고객사 직원은 외부 수강 대상이며 회사 자신의 내부교육과 구분한다."""
    return bool(_NON_PRODUCT_RE.search(_CUSTOMER_STAFF_RE.sub("외부 수강자", text)))


def _document_disqualifies_course(ranges: tuple[str, ...]) -> bool:
    for text in ranges:
        for unit in _UNIT_RE.split(text):
            unit = unit.strip()
            # 다른 강좌 소개나 강사의 경력은 현재 상품의 제한으로 옮기지 않는다.
            if _COURSE_SUBJECT_RE.match(unit) and _non_product_relation(unit):
                conditional_closure = _CLOSED_COURSE_RE.search(unit) and _CONDITIONAL_RE.search(unit)
                if not conditional_closure or _non_product_relation(_CLOSED_STATUS_RE.sub("", unit)):
                    return True
            if _CLOSED_COURSE_RE.search(unit) and not _CONDITIONAL_RE.search(unit):
                if _COURSE_SUBJECT_RE.match(unit) or _REGISTRATION_SUBJECT_RE.match(unit):
                    return True
    return False


def education_product_range_indices(
    document: WideDocumentIdentity, candidate_slots: tuple[str, ...]
) -> frozenset[int]:
    """제목·수강 신청·구체 실습의 현재 상품 설명 관계를 같은 문서에 결속한다.

    이 함수는 기존 작성 자격 검사 이후에만 호출한다. 학습에 쓰는 도구를
    제공자의 개발 제품으로 추출하거나 가격·매출 슬롯을 추가하지 않는다.
    """
    if EDUCATION_PRODUCT_SLOT not in candidate_slots or _EDITORIAL_PATH_RE.search(
        urlsplit(document.canonical_url).path
    ):
        return frozenset()
    title = " ".join(document.title.split())
    if not title or _non_product_relation(title):
        return frozenset()
    ranges = document.usable_ranges
    if not any(" ".join(text.split()) == title for text in ranges):
        return frozenset()
    if not any(_ENROLLMENT_RE.search(text) for text in ranges):
        return frozenset()
    if _document_disqualifies_course(ranges):
        return frozenset()
    return frozenset(
        index for index, text in enumerate(ranges)
        if _DESCRIPTION_RE.search(text)
        and _LEARNING_RELATION_RE.search(text)
        and not _non_product_relation(text)
    )
