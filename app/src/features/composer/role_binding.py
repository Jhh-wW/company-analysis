# -*- coding: utf-8 -*-
"""후보가 «누가 무엇을 하는가»·«무엇에 대가를 받는가»라고 적었으면 그 결속만 대조한다.

기존 근거 검증은 수치·추세·시점·비교와 인과 단언만 원문과 맞춰 본다. 그래서
① 원문이 「판매조직」이라고만 밝힌 대상에 「기획·제작」을 얹거나, ② 원문이 말한 적 없는
「재구매·재예치·지속적 협력」을 반복 수익으로 적는 자리는 어느 단계에서도 대조되지
않았다. 이 파일은 그 두 자리만 좁게 메운다.

지키는 선:
  · 유사도 문턱·회사명·상품명·업종별 정답을 쓰지 않는다. 한국어 문법 표지의 닫힌
    목록과 문자열 포함만 본다.
  · 표지가 없으면 아무 판정도 하지 않는다 — 올바른 요약·바꿔쓰기를 이 검사로 막지 않는다.
  · 빈 문자열은 «문제 없음»이지 승인이 아니다. 나머지 판정은 기존 검수가 한다.
  · «확인하지 못함»과 «원문에 없음»을 구분한다. 사유 문구는 전자로만 적는다.

⚠️ 닫힌 검증이다. 아래는 이 가드가 «확인하지 못한다» — 통과가 아니라 미확인이다:
   주어를 생략한 원문, 목차·제목 조각, 닫힌 목록 밖 어법의 부정, 쉼표·연결어미 없이
   이어 붙인 다중 주체, 평문으로 편 원문 표에서 「|」 없이 뭉개진 칸 경계,
   §⑥ 의 두 꼴 밖에서 일어나는 대가 방향 뒤집기.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.features.composer.role_binding_constants import (
    ADJACENCY_BREAK_RE,
    CELL_SPLIT_RE,
    CLAIM_ROLE_NEGATED_RE,
    CLAUSE_SPLIT_RE,
    DENIAL_WINDOW,
    FEE_CELL_MARKER_RE,
    FEE_CONCENTRATION_SOURCE_TEMPLATE,
    FEE_MAIN_REVENUE_CLAIM_TEMPLATES,
    PREDICATE_BREAK_RE,
    PROSE_FEE_MARKER_RE,
    PROSE_REPEAT_MARKER_RE,
    PROSE_ROLE_MARKER_RE,
    QUOTE_STRIP_RE,
    RELATION_FEE,
    RELATION_KEY,
    RELATION_QUOTE_KEY,
    RELATION_ROLE,
    RELATION_ROLE_KEY,
    RELATION_SOURCE_KEY,
    RELATION_TARGET_KEY,
    RELATION_TYPE_KEY,
    REPEAT_CELL_MARKER_RE,
    ROLE_BINDING_ACTOR_BOUNDARY,
    ROLE_BINDING_CLAIM_UNCOVERED,
    ROLE_BINDING_CONDITION_DROPPED,
    ROLE_BINDING_DIRECTION_REVERSED,
    ROLE_BINDING_FIELD_TYPE_INVALID,
    ROLE_BINDING_FIELDS,
    ROLE_BINDING_KIND_MISMATCH,
    ROLE_BINDING_MISSING,
    ROLE_BINDING_NEGATED_IN_SOURCE,
    ROLE_BINDING_NOT_OWN_CITE,
    ROLE_BINDING_PAIR_DEGENERATE,
    ROLE_BINDING_PAIR_MISSING,
    ROLE_BINDING_QUOTE_NOT_IN_SOURCE,
    ROLE_BINDING_ROLE_NOT_IN_CANDIDATE,
    ROLE_BINDING_ROLE_OUTSIDE_QUOTE,
    ROLE_BINDING_TARGET_NOT_IN_CANDIDATE,
    ROLE_BINDING_TYPES,
    ROLE_BINDING_UNBOUND_IN_SOURCE,
    ROLE_AS_PREDICATE_RE,
    ROLE_CELL_MARKER_RE,
    ROLE_CONDITION_MARKERS,
    ROLE_DENIAL_MARKERS,
    SUBJECT_TOKEN_RE,
    WHITESPACE_RE,
)


#: 괄호 짝. 여는 쪽과 닫는 쪽 순서가 같아야 한다.
_BRACKET_OPEN: str = "([{（［｛「『〈《"
_BRACKET_CLOSE: str = ")]}）］｝」』〉》"


@dataclass(frozen=True)
class _Unit:
    """후보를 이루는 «주장 하나의 자리». 도식은 칸, 산문은 문장이다."""

    raw: str
    surface: str
    index: tuple[int, ...]


def _surface_unit(value: object) -> _Unit:
    """표면형과 «원문 자리 되돌림표»를 함께 만든다.

    ★ 표면형으로 일치시킨 뒤 원문에서 다시 find 하면, 못 찾았을 때 «통과»로 새기
      쉽다. 여기서는 표면형 글자마다 원문 index 를 들고 다녀 자리를 확정한다.
      확정하지 못하면 통과가 아니라 그 결속을 쓰지 않는다.
    """

    raw = unicodedata.normalize("NFKC", str(value or ""))
    letters: list[str] = []
    index: list[int] = []
    for position, char in enumerate(raw):
        if WHITESPACE_RE.fullmatch(char) or QUOTE_STRIP_RE.fullmatch(char):
            continue
        letters.append(char.casefold())
        index.append(position)
    return _Unit(raw, "".join(letters), tuple(index))


def _surface(value: object) -> str:
    return _surface_unit(value).surface


def _occurrences(haystack: str, needle: str) -> tuple[int, ...]:
    if not needle:
        return ()
    found: list[int] = []
    start = haystack.find(needle)
    while start >= 0:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return tuple(found)


def _between(unit: _Unit, left_end: int, right_start: int) -> str:
    """표면형 좌표 두 자리 «사이»의 원문 조각. 자리를 확정하지 못하면 빈 값이 아니라 None."""

    if left_end > right_start:
        return ""
    if left_end <= 0 or right_start >= len(unit.index):
        return ""
    return unit.raw[unit.index[left_end - 1] + 1: unit.index[right_start]]


def _outer_only(segment: str) -> str:
    """괄호 «안»의 글자를 지운 조각. 경계는 바깥 층에서만 센다.

    ★ 「신탁업무운용수익(수익보수, 변동대가)」의 쉼표는 다른 주체가 아니라 «그 대상에
      달린 나열»이다. 괄호가 그 나열을 대상에 묶어 두므로 안쪽 쉼표로 결속을 끊으면
      정상 근거가 지워진다(우리은행 실측). 괄호 밖 쉼표는 그대로 끊는다.
    """

    depth = 0
    kept: list[str] = []
    for char in segment:
        if char in _BRACKET_OPEN:
            depth += 1
        elif char in _BRACKET_CLOSE:
            depth = max(0, depth - 1)
        kept.append(char if depth == 0 else " ")
    return "".join(kept)


def _unbroken(unit: _Unit, span_a: tuple[int, int], span_b: tuple[int, int]) -> bool:
    """두 자리 사이를 쉼표나 «다른 주체의 닫힌 술어»가 가르지 않는가."""

    if span_a[0] < span_b[0]:
        segment = _between(unit, span_a[1], span_b[0])
    else:
        segment = _between(unit, span_b[1], span_a[0])
    outer = _outer_only(segment)
    return not (ADJACENCY_BREAK_RE.search(outer) or PREDICATE_BREAK_RE.search(outer))


def _actor_boundary_ok(
    unit: _Unit, target_span: tuple[int, int], role_span: tuple[int, int],
    target_key: str,
) -> bool:
    """그 역할의 «주체»가 결속 대상과 어긋나지 않는가.

    ★ root 실측 반례: 「가람은 나래가 제작한 제품을 판매한다」·「가람의 협력사 나래는
      제작한다」는 가람과 제작 사이에 쉼표도 닫힌 연결어미도 없어서 인접 검사만으로는
      통과했다. 그러나 그 제작의 주체는 나래다.
    ★ 갈라 보는 기준은 «역할이 서술어인가»다. 서술어면(제작한다·제작한·개발했다)
      대상과 그 서술어 «사이»에 주격·주제 조사를 단 다른 이름이 끼면 그 이름이 주체다.
      끼지 않으면 대상이 주체이거나(「가람은 제작한다」) 바로 앞 목적어이므로
      (「음반을 제작합니다」·「플랫폼을 자체 개발했다」) 그 대상의 역할로 본다.
    ★ 역할이 명사면(「변동대가」·「제작 역량」) 앞의 「-는」은 뒤 명사를 꾸미는
      관형형이라 주체 표지가 아니다 — 그래서 서술어일 때만 이 검사를 한다.
    ⚠️ 형태소 분석을 하지 않는다. 이 구분 밖의 주체 전이는 «확인하지 못함»으로 남는다.
    """

    if target_span[0] >= role_span[0]:
        return True
    if ROLE_AS_PREDICATE_RE.match(unit.surface, role_span[1]) is None:
        return True
    between = _outer_only(_between(unit, target_span[1], role_span[0]))
    for token in SUBJECT_TOKEN_RE.findall(between):
        other = _surface(token)
        if other and target_key not in other and other not in target_key:
            return False
    return True


def _bound(
    unit: _Unit, target_span: tuple[int, int], role_span: tuple[int, int],
    target_key: str,
) -> bool:
    """그 자리에서 대상과 역할이 «한 주장»으로 묶여 있는가.

    후보 쪽과 원문 쪽에 «같은» 기준을 쓴다 — 한쪽만 느슨하면 그 틈으로 샌다.
    """

    return (_unbroken(unit, target_span, role_span)
            and _actor_boundary_ok(unit, target_span, role_span, target_key))


def _local_prefix_blank(unit: _Unit, start: int) -> bool:
    """그 자리 앞이 «비어 있는가» — 쉼표 뒤부터 그 자리까지에 다른 주체가 없는가.

    ★ 도식 행은 대상 칸과 역할 칸이 구조적으로 다른 칸이다(원문 그대로가 아니라
      행이 묶는다). 그래서 칸을 넘는 결속을 허용하되, 그 역할 칸이 «자기 주체»를
      따로 데리고 있으면 허용하지 않는다. 「나래 제작」의 제작은 나래의 것이지
      다른 칸의 가람 것이 아니다.
    """

    head = unit.raw[: unit.index[start]] if start < len(unit.index) else unit.raw
    breaks = list(ADJACENCY_BREAK_RE.finditer(head))
    tail = head[breaks[-1].end():] if breaks else head
    return not QUOTE_STRIP_RE.sub("", tail).strip()


def _units(text: str, cells: Sequence[str] | None) -> tuple[_Unit, ...]:
    """후보를 주장 자리로 나눈다. 도식은 칸, 산문은 원문이 스스로 끊은 절이다."""

    if cells is not None:
        return tuple(_surface_unit(cell) for cell in cells)
    parts = [part for part in CLAUSE_SPLIT_RE.split(
        unicodedata.normalize("NFKC", str(text or ""))) if part and part.strip()]
    return tuple(_surface_unit(part) for part in (parts or [str(text or "")]))


def _source_clauses(source: str) -> tuple[_Unit, ...]:
    """원문이 «스스로 끊은» 절만 돌려준다. 쉼표는 절을 나누지 않는다."""

    normalized = unicodedata.normalize("NFKC", str(source or ""))
    clauses: list[str] = []
    for chunk in CLAUSE_SPLIT_RE.split(normalized):
        if not chunk:
            continue
        clauses.extend(part for part in CELL_SPLIT_RE.split(chunk) if part.strip())
    return tuple(_surface_unit(clause) for clause in clauses)


# ══════════════════════════════════════════════════════════
# 후보가 단언한 자리 찾기 (발동)
# ══════════════════════════════════════════════════════════


def _claim_markers(
    units: Sequence[_Unit], is_flow: bool
) -> tuple[tuple[int, int, int, str], ...]:
    """후보가 역할·대가·반복이라고 적은 자리를 (칸, 시작, 끝, 유형)으로 돌려준다.

    ★ 도식 칸은 칸 하나가 하나의 주장이라 낱말만으로 센다. 산문은 그 낱말이
      «서술어로 쓰였을 때»만 센다 — 「공연 기획, 영상 콘텐츠 제작」 같은 명사
      나열까지 결속을 요구하면 정상 문장이 대량으로 지워진다.
    ★ 후보가 스스로 부정한 자리(「제작하지 않는다」)는 단언이 아니므로 세지 않는다.
    """

    role_pattern = ROLE_CELL_MARKER_RE if is_flow else PROSE_ROLE_MARKER_RE
    fee_patterns = (
        (FEE_CELL_MARKER_RE, REPEAT_CELL_MARKER_RE) if is_flow
        else (PROSE_FEE_MARKER_RE, PROSE_REPEAT_MARKER_RE)
    )
    markers: list[tuple[int, int, int, str]] = []
    for position, unit in enumerate(units):
        for pattern, kind in (
            (role_pattern, RELATION_ROLE),
            (fee_patterns[0], RELATION_FEE),
            (fee_patterns[1], RELATION_FEE),
        ):
            for match in pattern.finditer(unit.surface):
                start, end = match.span(1)
                if CLAIM_ROLE_NEGATED_RE.match(unit.surface, end) is not None:
                    continue
                markers.append((position, start, end, kind))
    return tuple(sorted(set(markers)))


def claims_role_or_fee(text: str, cells: Sequence[str] | None = None) -> bool:
    """후보가 역할·대가·반복을 «단언»했는가. 발동 여부만 본다."""

    return bool(_claim_markers(_units(text, cells), cells is not None))


# ══════════════════════════════════════════════════════════
# 결속 항목 하나 대조
# ══════════════════════════════════════════════════════════


def _binding_entries(entries: object) -> tuple[Mapping, ...]:
    """검증근거에서 역할·과금 결속 항목만 꺼낸다. 모양이 다르면 빈 값으로 둔다."""

    if not isinstance(entries, Mapping):
        return ()
    values = entries.get(RELATION_KEY)
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(
        item for item in values
        if isinstance(item, Mapping)
        and str(item.get(RELATION_TYPE_KEY) or "").strip() in ROLE_BINDING_TYPES
    )


def _text_field(item: Mapping, key: str) -> str:
    value = item.get(key)
    return value if isinstance(value, str) else ""


def _kind_answers_role(kind: str, role_key: str) -> bool:
    """그 결속의 유형이 «후보가 실제로 한 주장»에 답하는가."""

    if kind == RELATION_ROLE:
        return ROLE_CELL_MARKER_RE.search(role_key) is not None
    return bool(FEE_CELL_MARKER_RE.search(role_key)
                or REPEAT_CELL_MARKER_RE.search(role_key))


def _candidate_binding(
    units: Sequence[_Unit], target_key: str, role_key: str, is_flow: bool
) -> tuple[str, tuple[tuple[int, int, int], ...]]:
    """후보 안에서 «그 대상에 걸린 그 역할»의 자리를 모두 모은다.

    ★ 역할 낱말만 보면 「가람 개발」·「나래 제작」을 가람 근거 하나로 둘 다 덮는다.
      그래서 자리마다 주체를 확인한다. 같은 칸에 대상이 있으면 끊기지 않아야 하고,
      대상이 다른 칸에 있으면 그 역할 앞에 다른 주체가 없어야 한다(도식만).
    돌려주는 자리는 «역할 표현이 놓인 자리»뿐이다 — 칸 전체가 아니다.

    ★ 직결된 자리와 허용된 칸-경계 자리를 «합쳐» 돌려준다. 한쪽이라도 있으면 거기서
      멈추던 예전 방식은, 같은 칸에 직결이 있으면 다른 칸의 정상 자리를 놓쳐서
      그 자리가 영영 덮이지 않았다. 다른 주체가 앞에 붙은 자리는 여전히 넣지 않는다.
    """

    target_seen = False
    role_seen = False
    spans: list[tuple[int, int, int]] = []
    for position, unit in enumerate(units):
        target_spots = _occurrences(unit.surface, target_key)
        role_spots = _occurrences(unit.surface, role_key)
        target_seen = target_seen or bool(target_spots)
        role_seen = role_seen or bool(role_spots)
        for role_start in role_spots:
            role_span = (role_start, role_start + len(role_key))
            for target_start in target_spots:
                target_span = (target_start, target_start + len(target_key))
                if target_span[0] < role_span[1] and role_span[0] < target_span[1]:
                    continue
                if _bound(unit, target_span, role_span, target_key):
                    spans.append((position, role_span[0], role_span[1]))
                    break
    if not target_seen:
        return ROLE_BINDING_TARGET_NOT_IN_CANDIDATE, ()
    if not role_seen:
        return ROLE_BINDING_ROLE_NOT_IN_CANDIDATE, ()
    if is_flow:
        for position, unit in enumerate(units):
            if target_key in unit.surface:
                continue
            for role_start in _occurrences(unit.surface, role_key):
                if _local_prefix_blank(unit, role_start):
                    spans.append((position, role_start, role_start + len(role_key)))
    if spans:
        return "", tuple(sorted(set(spans)))
    return ROLE_BINDING_ACTOR_BOUNDARY, ()


def _quote_window(clause: _Unit, quote_key: str) -> tuple[int, int] | None:
    """그 절에서 «제시한 구절»이 차지하는 자리. 절 전체가 구절 안이면 절 전체다."""

    start = clause.surface.find(quote_key)
    if start >= 0:
        return start, start + len(quote_key)
    if clause.surface and clause.surface in quote_key:
        return 0, len(clause.surface)
    return None


def _denial_mismatch(source_tail: str, candidate_tail: str) -> str:
    """원문 절이 관계를 물리는데 후보가 그 표현을 옮기지 않았는가.

    ★ 구절을 부정 앞에서 잘라 내는 우회를 막으려고 «구절»이 아니라 «구절이 속한
      원문 절»의 뒤쪽을 본다. 다만 후보가 같은 표지를 그대로 적었으면 어긋남이
      아니다 — 「수수료 없는 비대면 서비스」는 원문도 후보도 「없」을 함께 적었다.
    """

    for marker in ROLE_DENIAL_MARKERS:
        if marker in source_tail and marker not in candidate_tail:
            return ROLE_BINDING_NEGATED_IN_SOURCE
    return ""


def _condition_mismatch(source_clause: str, candidate_unit: str) -> str:
    """원문 절이 그 관계에 단 조건을 후보가 옮기지 않았는가.

    ★ 조건은 역할 «앞»에 오는 일이 많다 — 「계약을 체결한 경우에만 제작합니다」에서
      구절을 「음반을 제작합니다」로 잘라 내면 조건이 구절 밖으로 빠진다. 그래서
      조건만은 절 전체에서 찾고, 후보 쪽도 그 자리(칸·문장) 전체와 견준다.
    """

    for marker in ROLE_CONDITION_MARKERS:
        if marker in source_clause and marker not in candidate_unit:
            return ROLE_BINDING_CONDITION_DROPPED
    return ""


def _candidate_tail(units: Sequence[_Unit], span: tuple[int, int, int]) -> str:
    """후보 쪽 역할 뒤의 짧은 창. 그 칸 안에서 다음 쉼표까지만 본다."""

    unit = units[span[0]]
    tail = unit.surface[span[2]: span[2] + DENIAL_WINDOW]
    cut = ADJACENCY_BREAK_RE.search(tail)
    return tail[: cut.start()] if cut else tail


def _fee_direction_reversed(
    claim_surface: str, quote_surface: str, target_key: str, role_key: str
) -> bool:
    """원문은 «그 대가가 어디서 생기나»인데 후보는 «그 대상의 주요 수익원»인가."""

    source_form = FEE_CONCENTRATION_SOURCE_TEMPLATE.format(
        fee=re.escape(role_key), scope=re.escape(target_key))
    if re.search(source_form, quote_surface) is None:
        return False
    return any(
        re.search(template.format(scope=re.escape(target_key),
                                  fee=re.escape(role_key)), claim_surface) is not None
        for template in FEE_MAIN_REVENUE_CLAIM_TEMPLATES
    )


def _binding_problem(
    item: Mapping,
    units: Sequence[_Unit],
    own_sources: Mapping[str, str],
    is_flow: bool,
) -> tuple[str, tuple[tuple[int, int, int], ...], str,
           dict[tuple[int, int, int], str]]:
    """결속 항목 하나를 대조한다. 통과하면 «이 근거가 실제로 증명한 자리»만 준다.

    ★ 자리마다 따로 본다. 한 자리가 어긋나도 다른 자리의 유효한 증명을 지우지 않는다.
      대신 한 자리도 증명하지 못한 근거는 그 사유를 그대로 돌려준다.
    ★ 증명하지 못한 자리의 사유는 넷째 값으로 함께 준다. 그 자리가 끝내 덮이지
      않으면 부르는 쪽이 «조건을 뺐다»처럼 구체적인 사유를 그대로 쓴다.
    """

    kind = str(item.get(RELATION_TYPE_KEY) or "").strip()
    for key in ROLE_BINDING_FIELDS:
        if key in item and not isinstance(item[key], str):
            return ROLE_BINDING_FIELD_TYPE_INVALID, (), kind, {}
    target = _text_field(item, RELATION_TARGET_KEY).strip()
    role = _text_field(item, RELATION_ROLE_KEY).strip()
    source_id = _text_field(item, RELATION_SOURCE_KEY).strip()
    quote = _text_field(item, RELATION_QUOTE_KEY).strip()
    if not target or not role:
        return ROLE_BINDING_PAIR_MISSING, (), kind, {}
    target_key, role_key = _surface(target), _surface(role)
    if not target_key or not role_key:
        return ROLE_BINDING_PAIR_MISSING, (), kind, {}
    # ① 대상과 역할값은 서로 구별되어야 한다. 한쪽이 다른 쪽의 일부면 「광고」·
    #    「광고 제작」처럼 슬롯을 겹쳐 통과시키는 우회가 된다.
    if (target_key == role_key
            or target_key in role_key or role_key in target_key):
        return ROLE_BINDING_PAIR_DEGENERATE, (), kind, {}
    # ② 그 유형이 후보가 실제로 한 주장에 답해야 한다. 「과금」이라며 역할 낱말만
    #    대거나 그 반대로 적으면 다른 주장의 증명으로 쓰인다.
    if not _kind_answers_role(kind, role_key):
        return ROLE_BINDING_KIND_MISMATCH, (), kind, {}
    # ③ 대조 대상은 «그 후보가 인용한» 조각 하나뿐이다. 다른 장의 근거를 빌릴 수 없다.
    if not source_id or source_id not in own_sources:
        return ROLE_BINDING_NOT_OWN_CITE, (), kind, {}
    source = own_sources[source_id]
    quote_key = _surface(quote)
    if not quote_key or quote_key not in _surface(source):
        return ROLE_BINDING_QUOTE_NOT_IN_SOURCE, (), kind, {}
    # ④ 후보 쪽에서 «그 대상에 걸린 그 역할»의 자리를 확정한다.
    claim_problem, claim_spans = _candidate_binding(units, target_key, role_key, is_flow)
    if claim_problem or not claim_spans:
        # 자리를 확정하지 못하면 «통과»가 아니라 거절이다. assert 로 두면 -O 실행에서
        # 검사 자체가 사라져 조용히 승인될 수 있다.
        return claim_problem or ROLE_BINDING_ACTOR_BOUNDARY, (), kind, {}
    # ⑤ 원문 쪽: 그 구절이 걸린 «한 절» 안에서 대상과 역할이 끊기지 않아야 한다.
    #    자리마다 따로 본다 — 조건·부정이 자리마다 다를 수 있기 때문이다.
    proven: list[tuple[int, int, int]] = []
    span_reasons: dict[tuple[int, int, int], str] = {}
    for claim_span in claim_spans:
        reason = _span_problem(
            claim_span, units, source, quote_key, target_key, role_key, kind,
        )
        if reason:
            span_reasons[claim_span] = reason
        else:
            proven.append(claim_span)
    if proven:
        return "", tuple(proven), kind, span_reasons
    first = next(iter(span_reasons.values()), ROLE_BINDING_UNBOUND_IN_SOURCE)
    return first, (), kind, span_reasons


def _span_problem(
    claim_span: tuple[int, int, int],
    units: Sequence[_Unit],
    source: str,
    quote_key: str,
    target_key: str,
    role_key: str,
    kind: str,
) -> str:
    """후보의 자리 하나를 원문에 대조한다. 빈 문자열이면 그 자리는 증명됐다."""

    candidate_tail = _candidate_tail(units, claim_span)
    outside_quote = False
    unbound = False
    for clause in _source_clauses(source):
        window = _quote_window(clause, quote_key)
        target_spots = _occurrences(clause.surface, target_key)
        if not target_spots:
            continue
        for role_start in _occurrences(clause.surface, role_key):
            role_span = (role_start, role_start + len(role_key))
            bound = False
            for target_start in target_spots:
                target_span = (target_start, target_start + len(target_key))
                if target_span[0] < role_span[1] and role_span[0] < target_span[1]:
                    continue
                if _bound(clause, target_span, role_span, target_key):
                    bound = True
                    break
            if not bound:
                unbound = True
                continue
            if window is None or not (window[0] <= role_span[0]
                                      and role_span[1] <= window[1]):
                outside_quote = True
                continue
            tail = clause.surface[role_span[1]: role_span[1] + DENIAL_WINDOW]
            mismatch = (
                _denial_mismatch(tail, candidate_tail)
                or _condition_mismatch(clause.surface, units[claim_span[0]].surface)
            )
            if mismatch:
                return mismatch
            if kind == RELATION_FEE and _fee_direction_reversed(
                units[claim_span[0]].surface, clause.surface, target_key, role_key
            ):
                return ROLE_BINDING_DIRECTION_REVERSED
            return ""
    if outside_quote:
        return ROLE_BINDING_ROLE_OUTSIDE_QUOTE
    if unbound:
        return ROLE_BINDING_UNBOUND_IN_SOURCE
    return ROLE_BINDING_UNBOUND_IN_SOURCE


def role_binding_problem(
    text: str,
    own_sources: Mapping[str, str],
    entries: object = None,
    cells: Sequence[str] | None = None,
) -> str:
    """역할·대가·반복을 단언한 후보만, 그 결속을 «자기 인용의 한 절»과 대조한다.

    ★ 후보가 그런 단언을 하지 않으면 아무 판정도 하지 않는다. 단언했다면 검수 응답이
      결속 항목으로 «어느 인용의 어느 구절이 그 대상에 그 역할을 주는지» 대야 하고,
      그 구절은 해당 인용 원문에 이어진 그대로 있어야 하며, 그 구절이 걸린 절 안에서
      대상과 역할이 끊기지 않고 묶여야 한다.
    ★ 한 후보에 단언이 여럿이면 «자리마다» 뒷받침이 있어야 한다. 같은 대상·역할의
      반복은 한 근거가 자리마다 검증해 덮지만, 다른 주장은 덮지 못한다 —
      「수수료, 로열티」는 수수료 근거 하나로 로열티까지 승인되지 않는다.
    ★ cells 는 «도식 후보일 때만» 준다. 행은 칸이 실제 구조라 칸을 넘는 결속을
      허용하지만, 그 칸이 자기 주체를 데리고 있으면 허용하지 않는다. 산문은 한
      절 안에서만 결속한다.
    """

    units = _units(text, cells)
    markers = _claim_markers(units, cells is not None)
    if not markers:
        return ""
    bindings = _binding_entries(entries)
    if not bindings:
        return ROLE_BINDING_MISSING
    proven: list[tuple[int, int, int, str]] = []
    span_reasons: dict[tuple[int, int, int], str] = {}
    for item in bindings:
        problem, spans, kind, reasons = _binding_problem(
            item, units, own_sources, cells is not None,
        )
        if problem:
            return problem
        span_reasons.update(reasons)
        for span in spans:
            proven.append((span[0], span[1], span[2], kind))
    # ★ 후보의 «각 단언 자리»가 그 자리를 덮는 결속을 가져야 한다. 역할 낱말·칸
    #   전체·첫 대가 근거로 다른 자리를 덮지 못한다.
    for position, start, end, kind in markers:
        if not any(unit == position and low <= start and end <= high
                   and proven_kind == kind
                   for unit, low, high, proven_kind in proven):
            # 그 자리를 증명하려다 어긋난 사유가 있으면 그대로 쓴다. 「조건을 뺐다」가
            # 「덮이지 않았다」로 뭉뚱그려지면 어디를 고쳐야 할지 알 수 없다.
            return next(
                (reason for span, reason in span_reasons.items()
                 if span[0] == position and span[1] <= start and end <= span[2]),
                ROLE_BINDING_CLAIM_UNCOVERED,
            )
    return ""
