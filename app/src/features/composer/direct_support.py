"""후보가 자기 인용에서 «직접» 가져왔다고 내세운 것만 원문과 대조하는 순수 검사.

기존 근거 검증은 수치·추세·시점·비교 배열만 원문과 맞춰 본다. 그래서
① 회사의 말이라며 붙인 서열·최상급 표현과 ② 인과 단언이 어느 단계에서도
원문과 대조되지 않았다. 이 파일은 그 두 자리만 좁게 메운다.

지키는 선:
  · 유사도 문턱·회사명·날짜·조각번호를 쓰지 않는다. 문법 표지와 장 계약의
    닫힌 어휘, 그리고 문자열 포함만 본다.
  · 표지가 없으면 아무 판정도 하지 않는다 — 올바른 요약·바꿔쓰기·해석 문장을
    이 검사로 막지 않는다. 「주요 원인이 아니다」처럼 부정한 문장도 단언이
    아니므로 손대지 않는다.
  · 빈 문자열은 «문제 없음»이지 승인이 아니다. 나머지 판정은 기존 검수가 한다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from src.features.composer.direct_support_constants import (
    ATTRIBUTION_RE,
    CAUSE_CLAIM_ROLES_MISMATCH,
    CAUSE_CLAIM_UNCOVERED,
    CAUSE_DIRECTION_REVERSED,
    CAUSE_HEDGED_IN_SOURCE,
    CAUSE_NEGATED_IN_SOURCE,
    CAUSE_PAIR_DEGENERATE,
    CAUSE_PAIR_MISSING,
    CAUSE_PAIR_NOT_IN_CLAIM,
    CAUSE_PAIR_NOT_IN_QUOTE,
    CAUSE_RELATION_MISSING,
    CAUSE_RELATION_NOT_CAUSAL,
    CAUSE_RELATION_NOT_IN_SOURCE,
    CAUSE_ROLES_UNPROVEN,
    CAUSE_SLOT_DIRECTION_ONLY,
    CAUSE_SOURCE_ID_EMPTY,
    CAUSAL_ROLE_TEMPLATES,
    CLAIM_CAUSE_NEGATED_RE,
    CLAIM_CAUSE_RE,
    CLAUSE_BOUNDARY_RE,
    FLOW_CELL_JOIN,
    RELATION_CAUSAL,
    RELATION_CAUSE_KEY,
    RELATION_EFFECT_KEY,
    RELATION_FIELD_TYPE_INVALID,
    RELATION_KEY,
    RELATION_QUOTE_KEY,
    RELATION_SOURCE_KEY,
    RELATION_TYPE_KEY,
    SLOT_DIRECTION_ONLY_RE,
    SPLIT_EFFECT_CELL_TEMPLATE,
    SOURCE_CAUSAL_RE,
    SOURCE_CONCESSIVE_RE,
    SOURCE_HEDGED_NEGATION_RE,
    SOURCE_NEGATION_RE,
    SOURCE_SENTENCE_SPLIT_RE,
    QUOTE_STRIP_RE,
    SUPERLATIVE_TOKENS,
    SUPERLATIVE_WITHOUT_SOURCE,
    WHITESPACE_RE,
)


def _surface(value: str) -> str:
    """공백·인용부호를 지우고 대소문자를 통일한 대조용 표면형."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return QUOTE_STRIP_RE.sub("", WHITESPACE_RE.sub("", normalized)).casefold()


def _source_values(sources_mapping: Mapping[str, str] | Sequence[str]) -> tuple[str, ...]:
    """호출자가 매핑을 주든 목록을 주든 원문 문자열만 뽑는다."""

    if isinstance(sources_mapping, Mapping):
        return tuple(str(value) for value in sources_mapping.values())
    if isinstance(sources_mapping, (str, bytes)):
        return ()
    return tuple(str(value) for value in sources_mapping)


def superlative_attribution_problem(
    text: str, sources_mapping: Mapping[str, str] | Sequence[str]
) -> str:
    """회사 표현으로 돌린 서열·최상급이 그 후보의 인용 원문에 있는지만 본다.

    ★ 장 계약이 「회사가 직접 밝힌 최초·유일·최다·1위·최대·독자 개발·특허
      표현을 옮긴다」고 요구하므로, 그 표현이 인용 원문에 없으면 회사가 하지 않은
      말을 회사의 말로 돌린 것이다. 귀속 서술어가 없는 문장(작성자 해석)은
      검사하지 않으며, 원문에 그 표현이 한 번이라도 있으면 통과시킨다.
    """

    candidate = _surface(text)
    if not candidate or not ATTRIBUTION_RE.search(unicodedata.normalize("NFKC", str(text or ""))):
        return ""
    present = [token for token in SUPERLATIVE_TOKENS if _surface(token) in candidate]
    if not present:
        return ""
    sources = [_surface(value) for value in _source_values(sources_mapping)]
    for token in present:
        if not any(_surface(token) in source for source in sources):
            return SUPERLATIVE_WITHOUT_SOURCE
    return ""


def positive_cause_markers(claim_key: str) -> tuple[re.Match[str], ...]:
    """표면형 후보에서 «주요 원인»이라고 «긍정으로» 단언한 자리만 돌려준다.

    ★ 표지만 세면 「환율 변동은 주요 원인이 아니다」 같은 정상적인 부정 진술이
      관계 근거를 대지 못해 통째로 삭제된다. 보조 가드가 보는 것은 «긍정으로
      명시한 주요 원인»뿐이고, 문장 전체의 옳고 그름은 기존 의미 검수의 몫이다.
    """

    return tuple(marker for marker in CLAIM_CAUSE_RE.finditer(claim_key)
                 if CLAIM_CAUSE_NEGATED_RE.match(claim_key, marker.end()) is None)


def claims_cause(text: str) -> bool:
    """후보가 무엇을 «주요·주된 원인/배경/요인»이라고 «긍정으로» 단언했는가."""

    return bool(positive_cause_markers(_surface(text)))


def _relation_entries(entries: object) -> tuple[Mapping, ...]:
    """검증근거에서 관계 배열만 꺼낸다. 모양이 다르면 빈 값으로 둔다."""

    if not isinstance(entries, Mapping):
        return ()
    values = entries.get(RELATION_KEY)
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(item for item in values if isinstance(item, Mapping))


def _sentences_covering(source: str, quote_key: str) -> tuple[str, ...]:
    """제시 구절이 걸쳐 있는 원문 «문장»들을 돌려준다.

    ★ 부정을 구절 안에서만 찾으면 「…원인이」까지만 잘라 내고 「아닙니다」를
      버리는 우회가 통한다. 그래서 그 구절이 속한 문장 전체를 본다. 구절이 두
      문장에 걸쳐 있으면 걸친 문장을 모두 돌려준다.
    """

    normalized = unicodedata.normalize("NFKC", str(source or ""))
    covering: list[str] = []
    for sentence in SOURCE_SENTENCE_SPLIT_RE.split(normalized):
        sentence_key = _surface(sentence)
        if not sentence_key:
            continue
        if quote_key in sentence_key or sentence_key in quote_key:
            covering.append(sentence)
    return tuple(covering)


def _text_field(item: Mapping, key: str) -> str | None:
    """관계 항목의 칸을 «문자열일 때만» 읽는다. bool·목록을 문자열로 바꾸지 않는다."""

    value = item.get(key)
    return value if isinstance(value, str) else None


def _role_match(quote_key: str, cause_key: str, effect_key: str) -> re.Match[str] | None:
    """원문이 이 배정을 «문법 표지»로 뒷받침하는 자리를 찾는다.

    ★ 글자 순서로 방향을 정하면 틀린다 — 「이익 증가는 비용 감소가 주요 원인이다」
      처럼 결과가 먼저 오는 정상 문장이 많다. 그래서 순서가 아니라 역할 표지가
      어느 쪽에 붙었는지를 닫힌 구문 틀로 확인한다.
    ⚠️ 틀에 없는 표현은 «미증명»이다. 통과가 아니라 «확인하지 못함»으로 남는다.
    """

    for template in CAUSAL_ROLE_TEMPLATES:
        pattern = template.replace("{cause}", re.escape(cause_key)).replace(
            "{effect}", re.escape(effect_key))
        found = re.search(pattern, quote_key)
        if found is not None:
            return found
    return None


def _relation_denial_after_roles(source: str, quote_key: str, matched: re.Match[str]) -> str:
    """역할 표지가 걸린 자리부터 뒤에서만 부정·유보를 찾아 사유 코드를 돌려준다.

    ★ 구절 안에서만 보면 「…원인이」까지 자르고 「아닙니다」를 버리는 우회가 통한다.
      반대로 문장 전체를 보면 앞쪽의 «다른» 관계 부정 때문에 정상 인과까지 지워진다.
      그래서 그 구절이 속한 문장에서, 역할 표지가 시작된 지점부터 뒤를 본다.
    ★ 완곡 부정(「원인이라고 보기는 어렵다」)도 같은 자리에서 본다. 「어렵다」를
      어휘로 막지 않기 때문에 「업무가 어렵다」·「원인 규명에 어려움」은 걸리지 않는다.
    """

    for sentence in _sentences_covering(source, quote_key):
        sentence_key = _surface(sentence)
        start = sentence_key.find(quote_key)
        offset = (start if start >= 0 else 0) + matched.start()
        tail = sentence_key[offset:]
        # ★ 관계 구문이 걸린 «그 절»까지만 본다. 뒤에 이어지는 다른 독립절의
        #   부정(「…; 임금 감소는 없었다」)으로 정상 인과를 지우지 않는다.
        boundary = CLAUSE_BOUNDARY_RE.search(tail)
        clause = tail[: boundary.start()] if boundary else tail
        if SOURCE_NEGATION_RE.search(clause):
            return CAUSE_NEGATED_IN_SOURCE
        if SOURCE_HEDGED_NEGATION_RE.search(clause):
            return CAUSE_HEDGED_IN_SOURCE
    return ""


def _split_role_span(
    cells: Sequence[str] | None, claim_key: str, cause_key: str, effect_key: str,
) -> tuple[int, int] | None:
    """도식의 값 있는 인접 칸에 나뉜 닫힌 인과 구문의 자리를 찾는다.

    ★ 도식은 칸이 실제 구조다. 그런데 후보는 칸을 이어 붙인 한 문자열로 전달되고
      가드의 역할 틀은 절 경계를 넘지 않는다 — 그래서 칸으로 나뉜 정상 인과가
      원리적으로 증명되지 못했다. 칸 배열을 «따로» 받아 그 경계에서만 읽는다.
    ⚠️ 이 함수는 후보 쪽 구조만 본다. 원문이 같은 원인·결과를 실제로 그렇게
       배정했는지는 호출부의 원문 역할 대조가 그대로 다시 확인한다.
    ⚠️ 빈 칸은 주장이 없으므로 건너뛰지만, 내용 있는 다른 칸을 넘어 연결하지
       않는다. 원인·결과 칸은 각각 전체가 그 구문이어야 한다.
    """

    if not cells or not cause_key or not effect_key:
        return None
    keys = [_surface(cell) for cell in cells]
    separator = _surface(FLOW_CELL_JOIN)
    # 칸 배열에서 나온 후보가 아니면 이 구조 근거를 쓰지 않는다.
    if separator.join(keys) != claim_key:
        return None
    starts: list[int] = []
    offset = 0
    for key in keys:
        starts.append(offset)
        offset += len(key) + len(separator)
    pattern = SPLIT_EFFECT_CELL_TEMPLATE.replace("{effect}", re.escape(effect_key))
    # 렌더와 검수 프롬프트도 빈 칸은 주장으로 보지 않는다. 원래 칸 위치와
    # 후보 문자열의 오프셋은 유지하고, 내용 있는 칸 사이의 인접성만 확인한다.
    nonempty_indices = [index for index, cell in enumerate(cells) if str(cell).strip()]
    for index, next_index in zip(nonempty_indices, nonempty_indices[1:]):
        if keys[index] != cause_key:
            continue
        if re.fullmatch(pattern, keys[next_index]) is None:
            continue
        return starts[index], starts[next_index] + len(keys[next_index])
    return None


def _relation_problem(
    item: Mapping,
    claim_key: str,
    sources_mapping: Mapping[str, str],
    cells: Sequence[str] | None,
) -> tuple[str, tuple[int, int] | None]:
    """관계 항목 하나를 대조한다. 통과하면 «후보 쪽 역할 구문이 걸린 자리»를 함께 준다.

    돌려주는 자리는 그 항목이 후보의 어느 단언을 실제로 덮었는지 세는 데 쓴다.
    후보 쪽 구문을 찾지 못하면 자리는 없고(None), 덮은 단언도 없다.
    """

    cause = _text_field(item, RELATION_CAUSE_KEY)
    effect = _text_field(item, RELATION_EFFECT_KEY)
    source_id = _text_field(item, RELATION_SOURCE_KEY)
    quote = _text_field(item, RELATION_QUOTE_KEY)
    for key in (RELATION_CAUSE_KEY, RELATION_EFFECT_KEY,
                RELATION_SOURCE_KEY, RELATION_QUOTE_KEY):
        if key in item and not isinstance(item[key], str):
            return RELATION_FIELD_TYPE_INVALID, None
    if not (cause or "").strip() or not (effect or "").strip():
        return CAUSE_PAIR_MISSING, None
    cause_key, effect_key = _surface(cause), _surface(effect)
    # ⓪ 방향만 적은 칸은 «무엇이» 변했는지가 없어 관계를 특정하지 못한다.
    #    후보 「비용 감소가 이익 증가의 주요 원인」에 원문 「수요 감소가 비용 증가의
    #    주요 원인」을 대고 원인=「감소」·결과=「증가」로 적은 실측 반례를 여기서 막는다.
    if (SLOT_DIRECTION_ONLY_RE.match(cause_key) is not None
            or SLOT_DIRECTION_ONLY_RE.match(effect_key) is not None):
        return CAUSE_SLOT_DIRECTION_ONLY, None
    # ① 두 슬롯이 서로 구별되어야 한다. 같은 값이거나 한쪽이 다른 쪽의 일부면
    #    「이익」·「이익 감소」처럼 슬롯을 축소해 통과시키는 우회가 된다.
    if (cause_key == effect_key
            or cause_key in effect_key or effect_key in cause_key):
        return CAUSE_PAIR_DEGENERATE, None
    # ② 선언한 원인·결과가 «그 후보 문장»에 있고, 후보 문장의 문법이 그 배정을
    #    뒤집지 않아야 한다. 글자만 있는지 보면 후보가 말한 방향과 반대로
    #    라벨해도 통과한다(실측 반례 R1).
    if cause_key not in claim_key or effect_key not in claim_key:
        return CAUSE_PAIR_NOT_IN_CLAIM, None
    claim_matched = _role_match(claim_key, cause_key, effect_key)
    claim_span = (
        claim_matched.span() if claim_matched is not None
        else _split_role_span(cells, claim_key, cause_key, effect_key)
    )
    if (claim_span is None
            and _role_match(claim_key, effect_key, cause_key) is not None):
        return CAUSE_CLAIM_ROLES_MISMATCH, None
    # ③ 어느 인용을 봤는지 비워 두면 대조할 원문이 정해지지 않는다. 원문 매핑에
    #    빈 키가 들어 있어도 빈 근거 id는 따로 거절한다.
    source_id = (source_id or "").strip()
    if not source_id:
        return CAUSE_SOURCE_ID_EMPTY, None
    quote = quote or ""
    source = sources_mapping.get(source_id) if isinstance(sources_mapping, Mapping) else None
    if not quote.strip() or source is None:
        return CAUSE_RELATION_NOT_IN_SOURCE, None
    quote_key = _surface(quote)
    if quote_key not in _surface(source):
        return CAUSE_RELATION_NOT_IN_SOURCE, None
    # ④ 제시 구절이 «바로 그» 원인과 결과를 함께 담아야 한다. 같은 출처의
    #    다른 인과 문장을 대는 우회를 여기서 막는다.
    if cause_key not in quote_key or effect_key not in quote_key:
        return CAUSE_PAIR_NOT_IN_QUOTE, None
    normalized_quote = unicodedata.normalize("NFKC", quote)
    if (SOURCE_CONCESSIVE_RE.search(normalized_quote)
            or not SOURCE_CAUSAL_RE.search(normalized_quote)):
        return CAUSE_RELATION_NOT_CAUSAL, None
    # ⑤ 역할은 «글자 순서»가 아니라 원문의 문법 표지로 정해진다. 뒤집힌 배정이
    #    맞으면 방향 오류로, 어느 쪽도 안 맞으면 미증명으로 갈라 돌려준다.
    matched = _role_match(quote_key, cause_key, effect_key)
    if matched is None:
        if _role_match(quote_key, effect_key, cause_key) is not None:
            return CAUSE_DIRECTION_REVERSED, None
        return CAUSE_ROLES_UNPROVEN, None
    # ⑥ 부정·유보는 «구절»이 아니라 그 구절이 속한 원문 문장에서, 그리고 역할
    #    표지가 걸린 자리부터 뒤에서 찾는다.
    denial = _relation_denial_after_roles(source, quote_key, matched)
    if denial:
        return denial, None
    return "", claim_span


def causal_relation_problem(
    text: str,
    sources_mapping: Mapping[str, str],
    entries: object,
    cells: Sequence[str] | None = None,
) -> str:
    """인과를 단언한 후보만, 그 관계를 뒷받침한다는 «원문 구절»을 대조한다.

    ★ 후보가 인과를 «긍정으로» 단언하지 않으면 아무 판정도 하지 않는다. 단언했다면
      검수 응답이 관계 항목으로 어느 인용의 어느 구절이 그 관계를 뒷받침하는지 대야
      하고, 그 구절은 해당 인용 원문에 «글자 그대로» 있어야 하며, 그 구절 자체가
      인과여야 한다. 원문이 「에도 불구하고」로 붙였거나 「보기는 어렵다」로 물린
      구절을 인과라고 내면 막는다. 대조 대상은 후보가 실제로 인용한 원문뿐이라
      다른 장의 근거를 빌릴 수 없다.
    ★ 한 후보에 단언이 여럿이면 «자리마다» 뒷받침이 있어야 한다. 항목을 더 넣거나
      같은 쌍을 되풀이해도 «같은 자리»만 다시 덮을 뿐 다른 자리는 덮이지 않는다 —
      그래서 한 인과를 두 인용으로 겹쳐 대는 정상 응답은 그대로 통과한다.
    ★ 도식 후보는 칸 배열(cells)을 함께 받는다. 한 칸 안에 완결된 인과는 지금까지와
      같은 경로로 판정하고, 칸으로 나뉜 관계는 «원인 칸 → 다음 값 있는 결과 칸»의 닫힌
      꼴일 때만 후보 쪽이 증명된다. 원문 대조는 어느 쪽이든 그대로 거친다.
    ⚠️ 이 검사는 «주요 원인이라고 명시한 자리»만 본다. 표지 없이 서술한 인과,
       암시된 인과, 문장 전체의 타당성은 기존 의미 검수가 계속 판정한다.
    """

    claim_key = _surface(text)
    markers = positive_cause_markers(claim_key)
    if not markers:
        return ""
    relations = [item for item in _relation_entries(entries)
                 if str(item.get(RELATION_TYPE_KEY) or "").strip() == RELATION_CAUSAL]
    if not relations:
        return CAUSE_RELATION_MISSING
    covered: list[tuple[int, int]] = []
    for item in relations:
        problem, span = _relation_problem(item, claim_key, sources_mapping, cells)
        if problem:
            return problem
        if span is not None:
            covered.append(span)
    for marker in markers:
        if not any(start <= marker.start() and marker.end() <= end
                   for start, end in covered):
            return CAUSE_CLAIM_UNCOVERED
    return ""


def direct_support_problem(
    text: str,
    sources_mapping: Mapping[str, str],
    entries: object = None,
    cells: Sequence[str] | None = None,
) -> str:
    """두 검사를 한 번에 돌려 첫 사유를 돌려준다. 빈 문자열은 승인이 아니다.

    ★ cells 는 «도식 후보일 때만» 준다. 본문·요약은 칸 구조가 없으므로 그대로
      None 이고, 한 문장 안에서 절을 넘는 연결은 여전히 허용하지 않는다.
    """

    return (superlative_attribution_problem(text, sources_mapping)
            or causal_relation_problem(text, sources_mapping, entries, cells))


def support_entries_by_number(raw: str | None) -> dict[int, object]:
    """검수 응답 원문에서 «번호 → 검증근거»만 뽑는다. 판정은 하지 않는다.

    ★ 연결하는 쪽이 응답을 다시 파싱하지 않도록 이 파일이 같은 계약으로 한 번만
      읽어 준다. 모양이 어긋난 항목은 조용히 건너뛴다 — 형식 판정은 기존 파서가
      이미 하고 있고, 여기서 두 번 실패시키지 않는다.
    """

    from src.features.composer.grounding_constants import (
        GROUNDING_KEY, REVIEW_ENTRIES_KEY, REVIEW_NUMBER_KEY,
    )
    from src.features.composer.logic import extract_json_payload
    # verify.py의 검수 파서와 같은 번호 보정 규칙을 쓴다 — 이 함수도 raw를
    # 독자적으로 다시 읽으므로 파서를 고쳐도 이 자리는 저절로 안 따라온다.
    from src.features.composer.verdict_number import coerce_verdict_number

    payload = extract_json_payload(raw or "")
    if not isinstance(payload, Mapping):
        return {}
    entries = payload.get(REVIEW_ENTRIES_KEY)
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return {}
    out: dict[int, object] = {}
    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        number = coerce_verdict_number(entry.get(REVIEW_NUMBER_KEY))
        if number is None:
            continue
        if number in seen:
            # ★ 같은 번호가 두 번 오면 «마지막이 이기지» 않는다. 어느 쪽이 그 후보의
            #   근거인지 정할 수 없으므로 그 번호 전체를 무효로 둔다.
            out[number] = None
            continue
        seen.add(number)
        out[number] = entry.get(GROUNDING_KEY)
    return out
