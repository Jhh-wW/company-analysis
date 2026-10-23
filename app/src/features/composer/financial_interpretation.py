"""정확한 손익 수치와 그 수치로 지어낸 인과 해석을 구별한다."""

from collections.abc import Mapping
import unicodedata

from src.features.composer.direct_support import (
    _relation_entries, _relation_problem, _role_match, _sentences_covering,
)
from src.features.composer.financial_interpretation_constants import (
    CAUSE_FIRST_RESULT_RE, CHANGE_BASES, FINANCIAL_CAUSE_DENIAL_RE,
    FINANCIAL_CAUSE_RE, FINANCIAL_COMPARISON_BASIS_MISMATCH,
    FINANCIAL_COMPARISON_RE, FINANCIAL_LOSS_RE, FINANCIAL_SENTENCE_RE,
    FINANCIAL_SUBJECT_COMPARISON_RE,
    FINANCIAL_SELF_PREFIX_RE, FINANCIAL_SOURCE_MODAL_RE, FINANCIAL_CAUSAL_BRIDGE_RE,
    NAMED_CAUSE_RE, NET_LOSS_CAUSE_UNBOUND, NET_LOSS_RE,
    NONOPERATING_METRIC_RE, OPERATING_METRIC_RE, RATE_BASES, RESULT_FIRST_CAUSE_RE,
    RESULT_FIRST_BACKGROUND_CAUSE_RE,
)


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _basis(value: str) -> str:
    if value in CHANGE_BASES:
        return "change"
    if value in RATE_BASES:
        return "rate"
    return "level"


def _relation_context_preserved(sentence: str, sources: Mapping[str, str],
                                entry: Mapping, span: tuple[int, int]) -> bool:
    """관계 앞의 다른 주어·조건을 잘라 같은 회사의 확정 인과로 옮기지 않는다."""
    # 기존 원문 문장 분리기는 종결 마침표를 제거한다. 포함 검증을 마친 구절의
    # 마지막 마침표만 같은 형태로 맞추고 주어·조건 텍스트는 보존한다.
    quote = _surface(entry["원문"]).removesuffix(".")
    matched = _role_match(quote, _surface(entry["원인"]), _surface(entry["결과"]))
    if matched is None:
        return False
    source_bridge = _inter_role_context(matched.group(), entry)
    claim_bridge = _inter_role_context(sentence[span[0]:span[1]], entry)
    if source_bridge is None or source_bridge != claim_bridge:
        return False
    claim_prefix = sentence[:span[0]]
    if FINANCIAL_SELF_PREFIX_RE.fullmatch(claim_prefix):
        claim_prefix = ""
    for part in _sentences_covering(sources[entry["근거"].strip()], quote):
        source = _surface(part)
        quote_start = source.find(quote)
        if quote_start < 0 or FINANCIAL_SOURCE_MODAL_RE.search(source):
            continue
        source_prefix = source[:quote_start + matched.start()]
        if FINANCIAL_SELF_PREFIX_RE.fullmatch(source_prefix):
            source_prefix = ""
        if source_prefix == claim_prefix:
            return True
    return False


def _inter_role_context(text: str, entry: Mapping) -> str | None:
    roles = (_surface(entry["원인"]), _surface(entry["결과"]))
    positions = sorted((text.find(role), role) for role in roles)
    if positions[0][0] < 0:
        return None
    start, first = positions[0]
    bridge = text[start + len(first):positions[1][0]]
    context = FINANCIAL_CAUSAL_BRIDGE_RE.sub("", bridge, count=1)
    return "" if FINANCIAL_SELF_PREFIX_RE.fullmatch(context) else context


def _direct_net_loss_support(sentence: str, sources: Mapping[str, str], entries: object) -> bool:
    # 같은 원문의 완결 문장을 그대로 옮긴 인과는 이 추가 검사로 막지 않는다.
    # 포함 문자열만 보면 조건부·타사 인용의 뒷부분도 빌릴 수 있어 전체 문장만 대조한다.
    if any(_surface(part) == sentence
           for source in sources.values() for part in FINANCIAL_SENTENCE_RE.split(source)):
        return True
    # 재서술된 인과는 기존 검수의 직접 인과 증명 계약을 그대로 재사용한다.
    # 빈 결과는 승인이 아니다. 같은 후보에 걸린 원인/순손실 쌍의 자리까지 필요하다.
    for entry in _relation_entries(entries):
        if (entry.get("유형") != "인과"
                or not isinstance(entry.get("원인"), str)
                or not isinstance(entry.get("결과"), str)
                or not OPERATING_METRIC_RE.search(_surface(entry["원인"]))
                or not NET_LOSS_RE.search(_surface(entry["결과"]))):
            continue
        problem, span = _relation_problem(entry, sentence, sources, None)
        if (not problem and span is not None
                and _relation_context_preserved(sentence, sources, entry, span)):
            return True
    return False


def financial_interpretation_problem(
    text: str, sources: Mapping[str, str], entries: object = None,
) -> str:
    """손실 원인의 닫힌 표현만 검사한다. 빈 반환은 기존 의미·수치 검수를 대체하지 않는다.

    증가액 비교는 이익의 변화 설명이 될 수 있지만 총액과 섞어 손실의 원인으로
    쓸 수 없다. 영업손실의 산술과 달리 순손실에는 영업외손익·세금도 관여하므로
    영업항목만의 순손실 인과는 자기 원문의 직접 설명이 있어야 한다.
    """
    for part in FINANCIAL_SENTENCE_RE.split(text):
        sentence = _surface(part)
        denials = tuple(FINANCIAL_CAUSE_DENIAL_RE.finditer(sentence))
        markers = tuple(FINANCIAL_CAUSE_RE.finditer(sentence))
        positive = tuple(marker for marker in markers if not any(
            denial.start() <= marker.start() < denial.end() for denial in denials
        ))
        if not FINANCIAL_LOSS_RE.search(sentence) or not positive:
            continue
        for pattern in (FINANCIAL_COMPARISON_RE, FINANCIAL_SUBJECT_COMPARISON_RE):
            for comparison in pattern.finditer(sentence):
                # 비교에 바로 이어지는 원인 주장만 본다. 뒤의 별도 인과 부정은
                # 앞의 긍정 원인을 면제하지 않고, 부인한 비교 자체는 제외하지 않는다.
                marker = next((item for item in markers if item.start() >= comparison.end()), None)
                if (marker in positive
                        and _basis(comparison["left"]) != _basis(comparison["right"])):
                    return FINANCIAL_COMPARISON_BASIS_MISMATCH
        if not NET_LOSS_RE.search(sentence):
            continue
        cause = next((match["cause"] for pattern in (
            RESULT_FIRST_CAUSE_RE, CAUSE_FIRST_RESULT_RE, NAMED_CAUSE_RE,
            RESULT_FIRST_BACKGROUND_CAUSE_RE,
        ) if (match := pattern.search(sentence)) and any(
            match.start() <= marker.start() < match.end() for marker in positive
        )), "")
        if (OPERATING_METRIC_RE.search(cause)
                and not NONOPERATING_METRIC_RE.search(cause)
                and not _direct_net_loss_support(sentence, sources, entries)):
            return NET_LOSS_CAUSE_UNBOUND
    return ""
