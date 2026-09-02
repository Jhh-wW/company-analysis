"""당면과제의 문제와 회사 대응을 관계로 판정한다.

``문제``·``대응`` 같은 낱말 하나씩을 독립 채점하면 고객용 문제 해결 광고나
리스크 관리 제품 소개가 회사 자신의 당면과제로 바뀐다. 이 모듈은 구체적
부정 상태/영향을 issue로 보고, response는 그 issue와 가까우며 명시적 연결어와
구체적 회사 행동이 함께 있을 때만 인정한다.
"""

from __future__ import annotations

from dataclasses import dataclass


_MARKETING_EXCLUSIONS: tuple[str, ...] = (
    "고객의 문제를 해결",
    "고객 문제를 해결",
    "맞춤형 솔루션을 제공",
    "리스크 관리 솔루션",
    "위험 관리 솔루션",
    "고객 경험을 개선",
    "고객경험을 개선",
    "고객 불편을 개선",
)

_CONCRETE_NEGATIVE_SIGNALS: tuple[str, ...] = (
    "부담",
    "차질",
    "부족",
    "압박",
    "손실",
    "어려움",
    "제약",
    "불확실",
    "취약",
    "악화",
    "하락",
    "감소",
    "지연",
    "중단",
    "문제가 발생",
    "위험이 발생",
    "리스크가 증가",
    "과제로 남",
)

_COMPANY_ACTION_SIGNALS: tuple[str, ...] = (
    "다변화",
    "전환",
    "확대",
    "축소",
    "도입",
    "구축",
    "투자",
    "재협상",
    "확보",
    "조정",
    "강화",
    "감축",
    "대체",
    "절감",
    "자동화",
    "변경",
)

_RELATION_CONNECTORS: tuple[str, ...] = (
    "이에 대응",
    "이에 따라",
    "이를 해결",
    "해결하기 위해",
    "대응하기 위해",
    "부담을 줄이기 위해",
    "위험을 낮추기 위해",
    "리스크를 낮추기 위해",
    "문제를 줄이기 위해",
)
_NEARBY_RANGE_DISTANCE = 1


@dataclass(frozen=True)
class ChallengeEvidence:
    """문서 범위 index 기준의 문제·연결된 대응 판정."""

    issue_range_indexes: tuple[int, ...]
    response_range_indexes: tuple[int, ...]

    def has_issue(self, index: int) -> bool:
        return index in self.issue_range_indexes

    def has_response(self, index: int) -> bool:
        return index in self.response_range_indexes


def classify_challenge_evidence(ranges: tuple[str, ...]) -> ChallengeEvidence:
    """같은 범위 또는 바로 앞 범위에 연결된 당면과제만 판정한다."""

    lowered_ranges = tuple(text.casefold() for text in ranges)
    issue_indexes = tuple(
        index
        for index, text in enumerate(lowered_ranges)
        if _has_concrete_issue(text)
    )
    issue_set = set(issue_indexes)
    response_indexes: list[int] = []

    for index, text in enumerate(lowered_ranges):
        if not _has_company_action(text):
            continue
        # 한 범위 안에서 문제와 행동이 함께 나오면 그 자체가 직접 관계다.
        # 범위를 넘길 때만 「이에 대응해」 같은 명시적 연결어를 요구한다.
        if index in issue_set or (
            index - _NEARBY_RANGE_DISTANCE in issue_set
            and _has_relation_connector(text)
        ):
            response_indexes.append(index)

    return ChallengeEvidence(
        issue_range_indexes=issue_indexes,
        response_range_indexes=tuple(response_indexes),
    )


def _has_concrete_issue(text: str) -> bool:
    if any(phrase in text for phrase in _MARKETING_EXCLUSIONS):
        return False
    return any(signal in text for signal in _CONCRETE_NEGATIVE_SIGNALS)


def _has_company_action(text: str) -> bool:
    if any(phrase in text for phrase in _MARKETING_EXCLUSIONS):
        return False
    return any(signal in text for signal in _COMPANY_ACTION_SIGNALS)


def _has_relation_connector(text: str) -> bool:
    return any(connector in text for connector in _RELATION_CONNECTORS)
