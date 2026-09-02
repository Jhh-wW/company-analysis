"""당면과제의 문제와 회사 대응을 관계로 판정한다.

``문제``·``대응`` 같은 낱말 하나씩을 독립 채점하면 고객용 문제 해결 광고나
리스크 관리 제품 소개가 회사 자신의 당면과제로 바뀐다. 이 모듈은 구체적
부정 상태/영향을 issue로 보고, response는 그 issue와 가까우며 명시적 연결어와
구체적 회사 행동이 함께 있을 때만 인정한다.
"""

from __future__ import annotations

import re
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
    "발굴",
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

# ``하락``·``감소``는 금리·부담처럼 낮아지는 편이 유리한 대상에도 쓰인다.
# 방향어 하나가 아니라 회사 성과/운영 지표와 나쁜 방향이 함께 있어야 한다.
_NEGATIVE_DIRECTIONAL_IMPACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:매출|영업이익|순이익|수익성|이익률|마진|점유율|판매량|수주|"
        r"생산량|가동률).{0,12}?(?:하락|감소|낮아|악화|축소)"
    ),
    re.compile(
        r"(?:원가율|제조원가|원가|비용|부담|압박|손실|적자|리스크|위험)"
        r".{0,12}?(?:상승|증가|높아|커졌|가중|악화|확대)"
    ),
)

# 부정 명사가 실제로 줄었다는 문장은 당면 문제가 아니라 개선 결과다.
_NEGATIVE_STATE_IMPROVEMENT_PATTERN = re.compile(
    r"(?:부담|압박|손실|적자|리스크|위험|불확실성?|원가율?|비용)"
    r".{0,10}?(?:감소|하락|완화|낮아|줄어|개선)"
)

# 신호 낱말 자체는 부정적이어도, 그 신호가 「않았다」·「없었다」처럼
# 명시적으로 부정되거나 「해소」·「미발생」·「아니다」처럼 이미 끝난 일로
# 서술되면 지금 회사가 겪는 당면과제가 아니다(P1-C). 부분 문자열
# 매칭만으로는 이 부정·해소 서술을 볼 수 없어 별도로 확인한다.
_NEGATION_OR_RESOLUTION_MARKERS: tuple[str, ...] = (
    "않았",
    "않는다",
    "않습니다",
    "않아",
    "없었",
    "없습니다",
    "해소",
    "미발생",
    "아니었",
    "아니다",
    "아닌",
    "아닙니다",
)

_THIRD_PARTY_OWNER_PATTERN = re.compile(
    r"(?:고객사|고객|협력사|공급업체|파트너사|경쟁사)(?:의|가|는|은|이|에서)?"
)
_THIRD_PARTY_ACTION_SUBJECT_PATTERN = re.compile(
    r"(?:고객사|고객|협력사|공급업체|파트너사|경쟁사)(?:가|는|은|이)"
)
_COMPANY_SUBJECT_PATTERN = re.compile(
    r"(?:당사|자사|우리\s*회사|회사가|회사는|회사의|회사를)"
)
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"[.!?。！？\n]")


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
    """회사 부정 영향과 인과 연결된 회사 행동만 당면과제로 판정한다."""

    lowered_ranges = tuple(text.casefold() for text in ranges)
    issue_indexes = tuple(
        index
        for index, text in enumerate(lowered_ranges)
        if _has_concrete_issue(text)
    )
    issue_set = set(issue_indexes)
    response_indexes: list[int] = []

    for index, text in enumerate(lowered_ranges):
        previous_has_issue = index - _NEARBY_RANGE_DISTANCE in issue_set
        if _has_causally_linked_company_action(
            text,
            previous_has_issue=previous_has_issue,
        ):
            response_indexes.append(index)

    return ChallengeEvidence(
        issue_range_indexes=issue_indexes,
        response_range_indexes=tuple(response_indexes),
    )


def _has_concrete_issue(text: str) -> bool:
    if any(phrase in text for phrase in _MARKETING_EXCLUSIONS):
        return False

    improvement_spans = tuple(
        match.span() for match in _NEGATIVE_STATE_IMPROVEMENT_PATTERN.finditer(text)
    )
    candidates: list[tuple[int, int]] = []
    for signal in _CONCRETE_NEGATIVE_SIGNALS:
        start = 0
        while True:
            found = text.find(signal, start)
            if found < 0:
                break
            candidates.append((found, found + len(signal)))
            start = found + len(signal)
    for pattern in _NEGATIVE_DIRECTIONAL_IMPACT_PATTERNS:
        candidates.extend(match.span() for match in pattern.finditer(text))

    for start, end in sorted(candidates):
        if any(
            span_start <= start and end <= span_end
            for span_start, span_end in improvement_spans
        ):
            continue
        candidate_text = text[start:end]
        if ("하락" in candidate_text or "감소" in candidate_text) and not any(
            pattern.search(candidate_text)
            for pattern in _NEGATIVE_DIRECTIONAL_IMPACT_PATTERNS
        ):
            continue
        if _belongs_to_third_party(text, start):
            continue
        if _is_negated_or_resolved(text, end):
            continue
        return True
    return False


def _company_action_positions(text: str) -> tuple[int, ...]:
    """구체 행동 낱말의 시작 위치를 중복 없이 돌려준다."""

    if any(phrase in text for phrase in _MARKETING_EXCLUSIONS):
        return ()
    positions: set[int] = set()
    for signal in _COMPANY_ACTION_SIGNALS:
        start = 0
        while True:
            found = text.find(signal, start)
            if found < 0:
                break
            positions.add(found)
            start = found + len(signal)
    return tuple(sorted(positions))


def _has_causally_linked_company_action(
    text: str,
    *,
    previous_has_issue: bool,
) -> bool:
    """연결어 앞의 문제와 연결어 뒤 회사 행동이 같은 인과 단위인지 본다."""

    for connector in _RELATION_CONNECTORS:
        search_from = 0
        while True:
            connector_start = text.find(connector, search_from)
            if connector_start < 0:
                break
            connector_end = connector_start + len(connector)
            same_range_issue = _has_concrete_issue(text[:connector_start])
            if (
                (same_range_issue or previous_has_issue)
                and _has_company_action_after(text, connector_end)
            ):
                return True
            search_from = connector_end
    return False


def _has_company_action_after(text: str, connector_end: int) -> bool:
    """연결어 뒤 행동의 주체가 제3자가 아닌 회사 자신인지 확인한다."""

    sentence_end_match = _SENTENCE_BOUNDARY_PATTERN.search(text, connector_end)
    sentence_end = sentence_end_match.start() if sentence_end_match else len(text)
    action_text = text[connector_end:sentence_end]
    sentence_start = 0
    for boundary in _SENTENCE_BOUNDARY_PATTERN.finditer(text, 0, connector_end):
        sentence_start = boundary.end()
    for relative_action_start in _company_action_positions(action_text):
        action_start = connector_end + relative_action_start
        subject_context = text[sentence_start:action_start]
        third_party_subjects = tuple(
            _THIRD_PARTY_ACTION_SUBJECT_PATTERN.finditer(subject_context)
        )
        company_subjects = tuple(_COMPANY_SUBJECT_PATTERN.finditer(subject_context))
        if not third_party_subjects:
            return True
        if company_subjects and company_subjects[-1].start() > third_party_subjects[-1].start():
            return True
    return False


def _belongs_to_third_party(text: str, signal_start: int) -> bool:
    """부정 영향 신호 바로 앞 문장 주체가 고객사·협력사인지 판정한다."""

    sentence_start = 0
    for boundary in _SENTENCE_BOUNDARY_PATTERN.finditer(text, 0, signal_start):
        sentence_start = boundary.end()
    prefix = text[sentence_start:signal_start]
    owners = tuple(_THIRD_PARTY_OWNER_PATTERN.finditer(prefix))
    if not owners:
        return False
    last_owner_end = owners[-1].end()
    return _COMPANY_SUBJECT_PATTERN.search(prefix, last_owner_end) is None


def _is_negated_or_resolved(text: str, signal_end: int) -> bool:
    """부정 신호 뒤 같은 문장 끝까지 부정·해소 서술이 있는지 본다(P1-C).

    ``차질``·``부족`` 같은 신호 낱말은 그 자체로는 방향을 모른다.
    「차질이 발생하지 않았다」・「부족 문제는 없었다」・「압박이 해소됐다」・
    「지연은 미발생했다」・「어려움은 사실이 아니다」처럼 신호 뒤에 명시적
    부정·해소 서술이 뒤따르면 지금 겪는 당면과제가 아니라 «문제가 없다»는
    선언이다. 신호 낱말 앞부분(예: 「해소」)까지 부정 마커로 오인하지
    않도록 신호가 끝난 지점부터만 같은 문장 끝까지 검사한다.
    """

    sentence_end_match = _SENTENCE_BOUNDARY_PATTERN.search(text, signal_end)
    sentence_end = sentence_end_match.start() if sentence_end_match else len(text)
    remainder = text[signal_end:sentence_end]
    return any(marker in remainder for marker in _NEGATION_OR_RESOLUTION_MARKERS)
