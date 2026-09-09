"""6장 「회사가 밝힌 성장 계획」 표의 «현재·완료 사실 → 미래 계획» 오분류만 막는다.

지금까지 이 표에는 결정론 게이트가 하나도 없었다 — 프롬프트 문장 두 곳이 전부였고,
검수 AI가 「참」이라고 하면 현재 진행 서술이 그대로 미래 계획 표에 실렸다. 실측에서
「런칭하여 매출 증대가 이뤄지고 있습니다」가 「여행 상품 확대 계획」으로,
「확대하고 있으며 … 대응하고 있습니다」가 「글로벌 진출 강화 계획」으로 올라왔다.

이 파일이 하는 일은 한 가지다 — 그 표의 «값이 있는 모든 줄»에 대해, 검수 응답이
스스로 댄 근거를 실제 입력에 다시 결속한다:

  ① 근거 id 는 «그 줄이 인용한» 조각 정확히 하나여야 한다.
  ② 원문 구절은 «그 하나»의 연속 부분문자열이어야 한다 (여러 근거 이어붙이기 금지).
  ③ 대상·활동은 후보의 «같은 칸»과 그 구절의 «같은 문장»에 의미 있는 경계로 있어야 한다.
  ④ 미래 양태는 그 문장에서 «활동 바로 뒤»의 첫 표지여야 한다. 뒤쪽 다른 목적어의
     계획을 끌어오지 못한다.
  ⑤ 전망은 후보에서도 전망으로, 부정·축소는 후보에서도 같은 방향으로 남아야 한다.

지키는 선:
  · 회사명 목록·유사도 문턱·조각번호·발행일을 쓰지 않는다. 문법 표지와 문자열
    포함, 그리고 «닫힌» 조사 목록만 본다.
  · 모델의 선언(「이건 계획입니다」)만으로는 아무것도 통과하지 않는다. 선언한
    양태가 원문에서 실제로 확인되는 것과 다르면 그 자체가 실패다.
  · 빈 문자열은 «이 검사에서 반례를 찾지 못했다»는 뜻이지 그 줄의 승인이 아니다.
    나머지 판정은 기존 수치·추세·시점·범위·인과·문화 검수가 그대로 한다.
  · 표 계약은 그대로다. 6장 «본문 문장»은 회사를 주어로 세운 계획·전망 주장이
    있을 때만 같은 근거를 요구한다(`future_plan_prose_problem`). 그 밖의 후보에는
    아무 판정도 하지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from src.features.composer.future_plan_constants import (
    CANDIDATE_DONE_RE,
    CANDIDATE_OUTLOOK_RE,
    CITATION_RE,
    FUTURE_ACTIVITY_KEY,
    FUTURE_ACTIVITY_NOT_IN_CANDIDATE,
    FUTURE_ACTIVITY_NOT_IN_QUOTE,
    FUTURE_ACTIVITY_TOO_SHORT,
    FUTURE_CANDIDATE_STATES_CURRENT,
    FUTURE_CLAIM_CELL_INCONSISTENT,
    FUTURE_EVIDENCE_MISSING,
    FUTURE_FIELD_KEYS,
    FUTURE_FIELD_TYPE_INVALID,
    FUTURE_KEY,
    FUTURE_MODALITY_NOT_BOUND,
    FUTURE_MODE_INVALID,
    FUTURE_MODE_KEY,
    FUTURE_MODE_MISDECLARED,
    FUTURE_MODE_OUTLOOK,
    FUTURE_MODE_PLAN,
    FUTURE_MODES,
    FUTURE_OUTLOOK_HARDENED,
    FUTURE_PLAN_DENIED_IN_SOURCE,
    FUTURE_POLARITY_FLIPPED,
    FUTURE_QUOTE_KEY,
    FUTURE_QUOTE_MISSING,
    FUTURE_QUOTE_NOT_IN_SOURCE,
    FUTURE_QUOTE_TOO_SHORT,
    FUTURE_SECOND_CLAIM_UNPROVEN,
    FUTURE_SLOTS_DEGENERATE,
    FUTURE_SLOTS_MISSING,
    FUTURE_SLOTS_SPLIT_ACROSS_CELLS,
    FUTURE_SOURCE_ID_EMPTY,
    FUTURE_SOURCE_KEY,
    FUTURE_SOURCE_NOT_CITED,
    FUTURE_SOURCE_STATES_CURRENT,
    FUTURE_SUBJECT_MISMATCH,
    FUTURE_TARGET_GENERIC,
    FUTURE_TARGET_KEY,
    FUTURE_TARGET_NOT_BOUND,
    FUTURE_TARGET_NOT_IN_CANDIDATE,
    FUTURE_TARGET_NOT_IN_QUOTE,
    FUTURE_TARGET_TOO_SHORT,
    FUTURE_TEMPORAL_RE,
    GENERIC_SUBJECTS,
    GENERIC_TARGETS,
    MIN_ACTIVITY_CHARS,
    MIN_QUOTE_CHARS,
    MIN_TARGET_CHARS,
    MODALITY_FUTURE_KINDS,
    PROSE_SUBJECT_RE,
    MEANS_BRIDGE_RE,
    MODALITY_RE,
    CANDIDATE_NEGATION_RE,
    CANDIDATE_SEGMENT_RE,
    CLAUSE_BOUNDARY_RE,
    SOURCE_NEGATION_RE,
    SOURCE_PLAN_DENIAL_RE,
    OBJECT_TAIL_RE,
    PARTICLE_DELIMITER,
    PARTICLE_TAIL,
    PHRASE_GAP,
    QUOTE_CHARACTERS,
    SENTENCE_SPLIT_RE,
    TARGET_ACTIVITY_BRIDGE_RE,
    THING_HEAD_NOUNS,
    THIRD_PARTY_SUBJECTS,
    TOPIC_RE,
    VERBALIZER,
    VERB_ENDING_HEAD,
    WHITESPACE_RE,
    WORD_CHAR,
)

#: 경계 있는 부분문자열 찾기에 쓰는 조립 규칙. 왼쪽은 낱말 글자가 아니어야 한다.
#: 오른쪽은 대상(명사)이면 «닫힌 조사 하나»까지만, 활동이면 거기에 더해 «하다·되다
#: 계열의 첫 음절 + 닫힌 어미 첫 글자»까지 벗겨 준다.
#:
#: ★ 왜 활동만 다른가 — 「확대」는 「확대를」처럼 명사로도, 「확대하고」처럼 동사로도
#:   쓰인다. 조사만 벗기면 원문의 동사형을 영영 찾지 못해 정상 계획 줄이 전부
#:   증명 실패로 빠진다(실측: 우리은행 「마케팅을 확대하고」).
#: ⚠️ 어미 첫 글자를 닫힌 목록으로 확인하므로 「확대」가 「확대해석」에 걸리지 않는다.
_NOUN_RIGHT: str = (
    r"(?:" + PARTICLE_DELIMITER + r"|(?:" + PARTICLE_TAIL + r")?(?!"
    + WORD_CHAR + r"))"
)
_VERBAL_RIGHT: str = (
    r"(?:" + VERBALIZER + r"(?:" + VERB_ENDING_HEAD + r"|(?!" + WORD_CHAR + r"))"
    r"|" + _NOUN_RIGHT + r")"
)


def _normalized(value: object) -> str:
    """대조용 표면형 — 호환문자 통일, 인용부호·인용표지 제거, 공백 한 칸.

    ★ 공백을 «없애지» 않는다. 없애면 「마케팅 확대」와 「마케팅확대회사」의 경계가
      구별되지 않아 「의미 있는 경계」라는 요구 자체가 성립하지 않는다.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = CITATION_RE.sub(" ", text)
    for character in QUOTE_CHARACTERS:
        text = text.replace(character, " ")
    return WHITESPACE_RE.sub(" ", text).strip().casefold()


def _compact(value: object) -> str:
    """공백까지 지운 표면형 — «구절이 원문에 통째로 있나»만 볼 때 쓴다."""

    return WHITESPACE_RE.sub("", _normalized(value))


def _bounded_spans(
    haystack: str, needle: str, *, verbal: bool = False
) -> tuple[tuple[int, int], ...]:
    """경계를 지킨 등장 자리를 모두 돌려준다. 없으면 빈 튜플.

    ★ 왼쪽이 낱말 글자면 「케팅」이 「마케팅」에 걸린다. 오른쪽은 닫힌 목록으로만
      벗긴다 — 임의 접미사를 벗기면 「마케팅」이 「마케팅화」에도 걸려 다른 대상을
      같은 대상으로 만든다.
    ★ ``verbal=True`` 는 활동 전용이다. 하다·되다 계열의 동사형까지 같은 활동으로
      본다. 돌려주는 자리의 «끝»은 언제나 활동 명사의 끝이므로, 뒤이어 오는
      어미(「하고 있」·「할 계획」)는 그대로 양태 판정의 꼬리에 남는다.
    """

    if not needle:
        return ()
    right = _VERBAL_RIGHT if verbal else _NOUN_RIGHT
    # ★ 낱말 사이에는 «닫힌 조사»만 끼어들 수 있다. 「이종산업 제휴」가 원문의
    #   「이종산업과의 제휴」와 같은 구임을 알아보되, 내용어가 끼면 다른 구다.
    tokens = [re.escape(token) for token in needle.split() if token]
    if not tokens:
        return ()
    phrase = PHRASE_GAP.join(tokens)
    pattern = re.compile(
        r"(?<!" + WORD_CHAR + r")(?P<phrase>" + phrase + r")" + right
    )
    return tuple(match.span("phrase") for match in pattern.finditer(haystack))


def _has_bounded(haystack: str, needle: str, *, verbal: bool = False) -> bool:
    return bool(_bounded_spans(haystack, needle, verbal=verbal))


def _sentences(text: str) -> tuple[str, ...]:
    return tuple(part for part in SENTENCE_SPLIT_RE.split(text) if part.strip())


def _first_modality(segment: str):
    """구간에서 «처음 만나는» 양태 표지. 없으면 None."""

    return MODALITY_RE.search(segment)


def _modality_kind(match: re.Match[str]) -> str:
    for name in ("outlook", "plan", "ongoing", "done"):
        if match.group(name) is not None:
            return name
    return "done"


def _entries(evidence: object) -> object:
    """검증근거에서 미래근거 배열만 꺼낸다. 모양이 어긋나면 그대로 돌려 보낸다."""

    if not isinstance(evidence, Mapping):
        return None
    return evidence.get(FUTURE_KEY)


def _slot_problem(target: str, activity: str) -> str:
    """대상·활동 칸 자체의 결함만 본다 (원문 대조 전 단계)."""

    if not target or not activity:
        return FUTURE_SLOTS_MISSING
    if len(target.replace(" ", "")) < MIN_TARGET_CHARS:
        return FUTURE_TARGET_TOO_SHORT
    if len(activity.replace(" ", "")) < MIN_ACTIVITY_CHARS:
        return FUTURE_ACTIVITY_TOO_SHORT
    if target.replace(" ", "") in GENERIC_TARGETS:
        return FUTURE_TARGET_GENERIC
    # 한쪽이 다른 쪽을 품으면 「마케팅」·「마케팅 확대」처럼 슬롯을 겹쳐 놓고
    # 실제로는 낱말 하나로 통과시키는 우회가 된다.
    if target == activity or target in activity or activity in target:
        return FUTURE_SLOTS_DEGENERATE
    return ""


def _candidate_cell(
    cells: Sequence[str], target: str, activity: str
) -> tuple[int, str, str]:
    """대상·활동이 «둘 다» 들어 있는 칸을 찾는다. (칸, 사유코드) 를 돌려준다.

    ★ 칸을 넘어 하나씩 가져오면 「공시된 내용」 칸의 대상에 「계획」 칸의 활동을
      붙여 원래 그 줄이 하지 않은 주장을 만들 수 있다. 표는 칸이 실제 구조다.
    """

    normalized = [_normalized(cell) for cell in cells]
    target_cells = {index for index, cell in enumerate(normalized)
                    if _has_bounded(cell, target)}
    activity_cells = {index for index, cell in enumerate(normalized)
                      if _has_bounded(cell, activity, verbal=True)}
    if not target_cells:
        return -1, "", FUTURE_TARGET_NOT_IN_CANDIDATE
    if not activity_cells:
        return -1, "", FUTURE_ACTIVITY_NOT_IN_CANDIDATE
    shared = sorted(target_cells & activity_cells)
    if not shared:
        return -1, "", FUTURE_SLOTS_SPLIT_ACROSS_CELLS
    # 가장 앞 칸이 그 줄의 주장 칸이다 (성장 계획 표의 1번 칸 = 「계획」).
    return shared[0], normalized[shared[0]], ""


def _pair_bridge_kind(
    text: str, target: str, activity_span: tuple[int, int]
) -> str:
    """대상과 활동을 잇는 «방식»을 돌려준다. 못 이으면 빈 문자열.

    ★ ``adjacent`` — 사이에 조사·접속 부호뿐. 「마케팅을 확대」.
    ★ ``means``    — 「…를 통해 …」처럼 대상이 활동의 수단으로 붙은 꼴.
      실측 정상 행 「이종산업 제휴를 통한 신수익원 발굴」이 이 꼴이라 필요하다.
    ⚠️ 종류를 «돌려주는» 이유는 후보 칸과 원문이 «같은 방식»으로 이어졌는지
       부르는 쪽이 대조하기 위해서다. 그러지 않으면 「A를 통해 B를 확대할 계획」
       원문으로 「A 확대」 행을 승인하게 된다 — A 가 확대되는 것이 아닌데도.
    """

    activity_start, activity_end = activity_span
    for target_start, target_end in _bounded_spans(text, target):
        if target_end <= activity_start:
            bridge = text[target_end:activity_start]
        elif activity_end <= target_start:
            bridge = text[activity_end:target_start]
        else:
            continue
        stripped = bridge.strip()
        if TARGET_ACTIVITY_BRIDGE_RE.fullmatch(stripped):
            return "adjacent"
        if MEANS_BRIDGE_RE.fullmatch(stripped):
            return "means"
    return ""


def _target_bound_to_activity(
    sentence: str, target: str, activity_span: tuple[int, int]
) -> str:
    """대상이 이 활동에 결속됐는가 — 인접 다리 또는 문장 주제 두 경로만 인정한다.

    ★ 인접 다리: 대상과 활동 사이에 조사·접속 부호밖에 없을 때. 「마케팅을 확대」.
    ★ 문장 주제: 대상이 「…은/는」으로 문장 주제일 때. 단, 활동 «바로 앞»에 다른
      목적어(「B를」)가 있으면 그 계획은 B의 것이므로 주제 경로를 쓰지 않는다 —
      「A는 완료했고 B를 확대할 계획」에서 A에 확대를 붙이는 것을 막는다.
    """

    activity_start, _activity_end = activity_span
    kind = _pair_bridge_kind(sentence, target, activity_span)
    if kind:
        return kind
    for topic in TOPIC_RE.finditer(sentence):
        if topic.group(1) != target or topic.end() > activity_start:
            continue
        bridge = sentence[topic.end():activity_start]
        if OBJECT_TAIL_RE.search(bridge):
            continue
        if any(other.group(1) != target for other in TOPIC_RE.finditer(bridge)):
            continue
        return "topic"
    return ""


def _subject_problem(sentence: str, target: str, activity_start: int,
                     candidate_cells: Sequence[str]) -> str:
    """이 계획을 «누가» 하겠다고 했는지 확인한다. 남의 계획을 빌려 오지 못한다.

    ★ 주제가 하나뿐이어도 검사한다. 「경쟁사는 해외 공장을 증설할 계획입니다」는
      주제가 하나지만 그 계획은 이 회사의 것이 아니다 — ROOT 중간 반례
      ``other_company_single_subject`` 가 이 구멍을 실제로 통과했다.
    ★ 통과하는 경우는 셋뿐이다. ① 주어가 생략됐다(공시문에서 자기 서술의 기본형),
      ② 주어가 「당사·회사·당행」 같은 일반 주어다, ③ 그 주어가 이 줄이 실제로
      말하는 대상이거나 이 줄의 칸에 그대로 적혀 있다.
    ⚠️ 회사 이름 목록을 쓰지 않는다 — 어떤 고유명사인지 «알아보는» 것이 아니라,
       명시된 주어가 이 줄에 결속돼 있는지만 본다. 그래서 모든 회사·업종에 같다.
    """

    topics = [topic for topic in TOPIC_RE.finditer(sentence)
              if topic.end() <= activity_start]
    if not topics:
        return ""
    nearest = topics[-1].group(1)
    # ① 관계 명사로 «남»이라고 밝힌 주어면, 주제가 하나뿐이어도 남의 계획이다.
    if nearest in THIRD_PARTY_SUBJECTS:
        return FUTURE_SUBJECT_MISMATCH
    if nearest in GENERIC_SUBJECTS:
        return ""
    if nearest == target or nearest in target or target in nearest:
        return ""
    if any(_has_bounded(_normalized(cell), nearest) for cell in candidate_cells):
        return ""
    # ② 주어가 «사물»이면 남의 계획을 빌려 온 것이 아니다. 회사는 자기 서비스·부문을
    #    주어로 자주 쓴다(실측 SM 광고 원문의 「…마케팅 커뮤니케이션 서비스는」).
    if nearest.endswith(THING_HEAD_NOUNS):
        return ""
    # ③ 그 밖의 이름난 주어는 «주제가 하나뿐이어도» 거절한다. 주제가 하나라고
    #    안전한 것이 아니다 — ROOT 반례에서 원문 주어는 「나래기업」인데 행은
    #    「가람기업」의 계획인 것처럼 적혀 있었고, 그것이 그대로 통과했다.
    return FUTURE_SUBJECT_MISMATCH


def _plan_denied_after_marker(tail: str, marker_end: int) -> bool:
    """양태 표지 «뒤»에서 그 계획 자체를 취소했는가.

    ★ 「…확대할 계획은 없습니다」를 「…확대할 계획」에서 끊어 내면 부정이 사라진다.
      ROOT 중간 반례 ``truncated_plan_denial`` 이 이 우회를 실제로 통과했다.
      그래서 표지 뒤를 «그 절 안에서» 본다 — 다음 절의 무관한 부정은 보지 않는다.
    ⚠️ 표지 «앞»의 부정은 여기서 보지 않는다. 그건 「하지 않을 계획」이라는 정상적인
       부정 계획이며 극성 검사가 따로 맞춘다.
    """

    rest = tail[marker_end:]
    boundary = CLAUSE_BOUNDARY_RE.search(rest)
    clause = rest[: boundary.start()] if boundary else rest
    return SOURCE_PLAN_DENIAL_RE.search(clause) is not None


def _source_modality(sentence: str, activity_span: tuple[int, int]) -> tuple[str, str, str]:
    """활동 바로 뒤 첫 표지로 양태를 정한다. (양태, 사유코드, 부정여부) 를 돌려준다.

    ★ 뒤에 표지가 하나도 없을 때만 «활동 앞»의 미래 시점 부사를 본다. 그 사이에
      진행·완료 표지가 끼어 있으면 쓰지 않는다.
    """

    tail = sentence[activity_span[1]:]
    match = _first_modality(tail)
    if match is not None:
        kind = _modality_kind(match)
        if kind not in MODALITY_FUTURE_KINDS:
            return "", FUTURE_SOURCE_STATES_CURRENT, ""
        if _plan_denied_after_marker(tail, match.end()):
            return "", FUTURE_PLAN_DENIED_IN_SOURCE, ""
        negated = bool(SOURCE_NEGATION_RE.search(tail[:match.start()]))
        mode = FUTURE_MODE_OUTLOOK if kind == "outlook" else FUTURE_MODE_PLAN
        return mode, "", "negated" if negated else ""
    head = sentence[:activity_span[0]]
    adverbs = list(FUTURE_TEMPORAL_RE.finditer(head))
    if adverbs:
        bridge = head[adverbs[-1].end():]
        blocking = _first_modality(bridge)
        if blocking is None or _modality_kind(blocking) in MODALITY_FUTURE_KINDS:
            negated = bool(SOURCE_NEGATION_RE.search(bridge))
            return FUTURE_MODE_PLAN, "", "negated" if negated else ""
    return "", FUTURE_MODALITY_NOT_BOUND, ""


def _candidate_polarity(cell: str, activity: str) -> bool:
    """후보 칸이 이 활동을 «계획 자체를 뒤집는» 방향으로 적었는가.

    ★ 후보 칸은 명사구 조각이라 「수수료 없는 비대면 서비스」처럼 수식어의 부정이
      흔하다. 그것을 계획의 부정으로 읽으면 정상 줄이 극성 불일치로 빠진다 —
      그래서 후보 쪽은 좁은 닫힌 목록만 본다.
    ★ 활동 낱말 자체가 축소·중단이면 극성은 활동에 담겨 있다.
    """

    if CANDIDATE_NEGATION_RE.search(activity):
        return True
    spans = _bounded_spans(cell, activity, verbal=True)
    if not spans:
        return bool(CANDIDATE_NEGATION_RE.search(cell))
    start, end = spans[0]
    return bool(CANDIDATE_NEGATION_RE.search(cell[end:])
                or CANDIDATE_NEGATION_RE.search(cell[:start]))


def _covering_sentences(source: str, quote_text: str) -> tuple[str, ...]:
    """제시 구절이 걸쳐 있는 «원문 문장»들을 돌려준다.

    ★ 구절만 보면 「…할 계획은 없습니다」를 「…할 계획」에서 끊어 내 부정을 버리는
      우회가 통한다(ROOT 중간 반례 ``truncated_plan_denial``). 그래서 판정은 언제나
      그 구절이 «속한 원문 문장» 위에서 한다 — 구절은 어느 문장을 볼지 «가리키는»
      역할이고, 실제로 읽는 것은 원문이다.
    ⚠️ 구절이 두 문장에 걸쳐 있으면 걸친 문장을 모두 돌려준다.
    """

    quote_key = _compact(quote_text)
    covering: list[str] = []
    for sentence in _sentences(_normalized(source)):
        sentence_key = _compact(sentence)
        if not sentence_key:
            continue
        if quote_key in sentence_key or sentence_key in quote_key:
            covering.append(sentence)
    return tuple(covering)


def _segments(cell: str) -> tuple[tuple[int, int, str], ...]:
    """후보 칸을 «별개 주장 조각»으로 가른다. 닫힌 접속 표지에서만 자른다."""

    spans: list[tuple[int, int]] = []
    start = 0
    for separator in CANDIDATE_SEGMENT_RE.finditer(cell):
        if separator.start() > start:
            spans.append((start, separator.start()))
        start = separator.end()
    if start < len(cell):
        spans.append((start, len(cell)))
    return tuple((first, last, cell[first:last])
                 for first, last in spans if cell[first:last].strip())


def _segment_index(segments: Sequence[tuple[int, int, str]], position: int) -> int:
    for index, (first, last, _text) in enumerate(segments):
        if first <= position < last:
            return index
    return -1


def _one_item_problem(
    item: Mapping,
    cells: Sequence[str],
    sources_mapping: Mapping[str, str],
) -> tuple[str, tuple[int, int] | None]:
    """미래근거 항목 하나를 실제 입력에 결속한다.

    통과하면 (빈 문자열, (주장 칸 번호, 그 항목이 덮은 조각 번호))를 돌려준다.
    부르는 쪽이 그 자리를 모아 «칸의 모든 조각이 증명됐는지»를 다시 확인한다.
    """

    for key in FUTURE_FIELD_KEYS:
        if key in item and not isinstance(item[key], str):
            return FUTURE_FIELD_TYPE_INVALID, None
    target = _normalized(item.get(FUTURE_TARGET_KEY))
    activity = _normalized(item.get(FUTURE_ACTIVITY_KEY))
    slot_problem = _slot_problem(target, activity)
    if slot_problem:
        return slot_problem, None

    cell_index, cell, cell_problem = _candidate_cell(cells, target, activity)
    if cell_problem:
        return cell_problem, None
    # ★ 후보 칸이 스스로 「완료」라고 말하면 성장 계획 줄이 아니다.
    if CANDIDATE_DONE_RE.search(cell):
        return FUTURE_CANDIDATE_STATES_CURRENT, None

    source_id = str(item.get(FUTURE_SOURCE_KEY) or "").strip()
    if not source_id:
        return FUTURE_SOURCE_ID_EMPTY, None
    if not isinstance(sources_mapping, Mapping) or source_id not in sources_mapping:
        return FUTURE_SOURCE_NOT_CITED, None
    source = sources_mapping[source_id]

    quote = str(item.get(FUTURE_QUOTE_KEY) or "")
    if not quote.strip():
        return FUTURE_QUOTE_MISSING, None
    quote_compact = _compact(quote)
    if len(quote_compact) < MIN_QUOTE_CHARS:
        return FUTURE_QUOTE_TOO_SHORT, None
    # ★ «그 하나»의 연속 부분문자열이어야 한다. 여러 원문을 이어 붙여 검사하면
    #   서로 다른 출처를 짜깁기한 구절이 통과한다.
    if quote_compact not in _compact(source):
        return FUTURE_QUOTE_NOT_IN_SOURCE, None

    quote_text = _normalized(quote)
    if not _has_bounded(quote_text, target):
        return FUTURE_TARGET_NOT_IN_QUOTE, None
    if not _has_bounded(quote_text, activity, verbal=True):
        return FUTURE_ACTIVITY_NOT_IN_QUOTE, None

    declared = str(item.get(FUTURE_MODE_KEY) or "").strip()
    if declared not in FUTURE_MODES:
        return FUTURE_MODE_INVALID, None

    segments = _segments(cell)
    candidate_activity = _bounded_spans(cell, activity, verbal=True)
    # ★ 판정은 구절이 «속한 원문 문장» 위에서 한다. 구절 자체만 읽으면 잘라 낸
    #   부정·유보가 사라진다. 걸친 문장을 못 찾을 때만 구절로 물러선다.
    sentences = _covering_sentences(source, quote_text) or _sentences(quote_text)
    problems: list[str] = []
    for sentence in sentences:
        for activity_span in _bounded_spans(sentence, activity, verbal=True):
            kind = _target_bound_to_activity(sentence, target, activity_span)
            if not kind:
                problems.append(FUTURE_TARGET_NOT_BOUND)
                continue
            # ★ 후보 칸도 «같은 방식»으로 이어져 있어야 한다. 원문이 「A를 통해 B를
            #   확대」인데 후보가 「A 확대」면, 확대되는 것은 A 가 아니라 B 다.
            if kind in ("adjacent", "means"):
                if not candidate_activity:
                    problems.append(FUTURE_ACTIVITY_NOT_IN_CANDIDATE)
                    continue
                if _pair_bridge_kind(cell, target, candidate_activity[0]) != kind:
                    problems.append(FUTURE_TARGET_NOT_BOUND)
                    continue
            subject_problem = _subject_problem(
                sentence, target, activity_span[0], cells
            )
            if subject_problem:
                problems.append(subject_problem)
                continue
            mode, modality_problem, negation = _source_modality(sentence, activity_span)
            if modality_problem:
                problems.append(modality_problem)
                continue
            if mode != declared:
                problems.append(FUTURE_MODE_MISDECLARED)
                continue
            cell_spans = _bounded_spans(cell, activity, verbal=True)
            if not cell_spans:
                problems.append(FUTURE_ACTIVITY_NOT_IN_CANDIDATE)
                continue
            segment_index = _segment_index(segments, cell_spans[0][0])
            # ★ 전망을 확정 계획으로 굳히지 못한다. 그 한정은 «같은 조각»에서
            #   «그 활동 뒤»에 남아야 한다 — 칸 어딘가의 무관한 「기대」를 빌려 오면
            #   다른 주장의 한정으로 이 활동을 승인하게 된다(ROOT 중간 반례
            #   ``unrelated_outlook_qualifier``).
            if mode == FUTURE_MODE_OUTLOOK:
                if segment_index < 0:
                    problems.append(FUTURE_OUTLOOK_HARDENED)
                    continue
                first, _last, segment_text = segments[segment_index]
                offset = cell_spans[0][0] - first
                if not CANDIDATE_OUTLOOK_RE.search(segment_text, offset):
                    problems.append(FUTURE_OUTLOOK_HARDENED)
                    continue
            # ★ 부정·축소 계획은 후보도 같은 방향이어야 한다. 반대로 뒤집지 못한다.
            if bool(negation) != _candidate_polarity(cell, activity):
                problems.append(FUTURE_POLARITY_FLIPPED)
                continue
            return "", (cell_index, segment_index)
    if problems:
        return problems[0], None
    return FUTURE_TARGET_NOT_BOUND, None


def future_plan_problem(
    cells: Sequence[str],
    sources_mapping: Mapping[str, str],
    evidence: object,
) -> str:
    """성장 계획 표 한 줄의 미래 근거를 결속한다. 사유 코드 또는 빈 문자열.

    ★ 부르는 쪽이 «6장 성장 계획 표의 줄일 때만» 부른다. 본문 문장·다른 장 도식은
      대상이 아니다 — 이 함수는 그 판정을 하지 않는다.
    ★ 각 근거 항목은 자기 인용 하나의 연속 구절이어야 한다. 한 줄의 여러 주장을
      각각 다른 자기 인용이 뒷받침할 수 있다.
    ★ 주장 칸이 접속 표지로 여러 조각이면 «조각마다» 증명이 있어야 한다. 하나만
      증명하고 나머지를 얹는 것을 막는다(ROOT 중간 반례 ``second_unproven_plan``).
    ⚠️ 빈 문자열은 승인이 아니라 «이 검사가 반례를 찾지 못했다»는 뜻이다.
    """

    values = [str(cell) for cell in cells]
    if not any(value.strip() for value in values):
        return ""
    items = _entries(evidence)
    if items is None:
        return FUTURE_EVIDENCE_MISSING
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return FUTURE_FIELD_TYPE_INVALID
    entries = [item for item in items if isinstance(item, Mapping)]
    if len(entries) != len(items):
        return FUTURE_FIELD_TYPE_INVALID
    if not entries:
        return FUTURE_EVIDENCE_MISSING
    # ★ 항목마다 «자기 인용 하나»의 연속 구절이면 된다. 한 줄이 두 주장을 담고
    #   각 주장을 서로 «다른» 자기 인용이 뒷받침하는 것은 정상이다 — 예전에는
    #   이것을 근거 모호로 거절했는데, 요구가 아니었다(ROOT 정정).
    #   짜깁기 금지는 그대로다: 한 구절이 여러 원문에 걸치면 항목 검사에서 막힌다.
    proofs: list[tuple[int, int]] = []
    for item in entries:
        problem, proof = _one_item_problem(item, values, sources_mapping)
        if problem:
            return problem
        if proof is not None:
            proofs.append(proof)
    claim_cells = {cell_index for cell_index, _segment in proofs}
    if len(claim_cells) != 1:
        return FUTURE_CLAIM_CELL_INCONSISTENT
    claim_cell = _normalized(values[claim_cells.pop()])
    covered = {segment_index for _cell, segment_index in proofs}
    if any(index not in covered for index in range(len(_segments(claim_cell)))):
        return FUTURE_SECOND_CLAIM_UNPROVEN
    return ""


def _prose_plan_claims(text: str) -> tuple[tuple[int, str, int, int], ...]:
    """산문에서 «회사를 주어로 세운 계획·전망 주장»의 자리만 돌려준다.

    돌려주는 값: (문장 번호, 그 문장, 표지 시작, 표지 끝). 하나도 없으면 이 검사는 아무 판정도
    하지 않는다 — 산업·시장의 현재 서술, 회사의 진행·완료 서술, 배경 설명은 여기서
    걸러져 원래대로 기존 검수의 몫으로 남는다.

    ★ 문 두 개가 «같은 문장»에서 함께 열려야 한다.
      ① 명시된 주어가 일반 주어(회사·당사·당행…)다. 고유명사를 알아보지 않는다.
      ② 그 문장에 계획 또는 전망 표지가 있다(진행·완료는 아니다).
    ⚠️ 주어가 생략된 문장은 발동하지 않는다. 6장 본문에는 산업 서술이 함께 오는데,
       주어 없는 문장까지 회사 계획으로 보면 정상 배경 설명이 대량으로 걸린다.
    """

    claims: list[tuple[int, str, int, int]] = []
    for index, sentence in enumerate(_sentences(_normalized(text))):
        subjects = [match.group(1) for match in PROSE_SUBJECT_RE.finditer(sentence)]
        if not any(subject in GENERIC_SUBJECTS for subject in subjects):
            continue
        markers = [match for match in MODALITY_RE.finditer(sentence)
                   if _modality_kind(match) in MODALITY_FUTURE_KINDS]
        # ★ 「향후·내년·앞으로」처럼 시점만 말하는 부사는 그 자체가 «별도의 계획
        #   주장»이 아니다. 그 주장은 같은 문장의 서술어 표지(「…할 계획이다」)가
        #   진다. 부사를 주장 자리로 세우면 활동이 언제나 그 «뒤»에 오므로,
        #   원문이 완벽히 뒷받침하는 정상 문장까지 영영 증명될 수 없게 된다.
        predicates = [match for match in markers
                      if FUTURE_TEMPORAL_RE.fullmatch(match.group()) is None]
        # 서술어 표지가 하나도 없으면 그 부사가 문장 전체의 미래성을 진다. 이때는
        # 문장 어디에 있는 활동으로도 증명할 수 있도록 자리를 문장 끝에 둔다.
        for match in (predicates or markers[-1:]):
            start = match.start() if predicates else len(sentence)
            claims.append((index, sentence, start, match.end()))
    return tuple(claims)


def _one_prose_item_problem(
    item: Mapping,
    sentences: Sequence[str],
    sources_mapping: Mapping[str, str],
) -> tuple[str, tuple[tuple[int, int], ...]]:
    """미래근거 항목 하나를 «본문 문장»에 결속한다. 표 항목 검사와 같은 잣대다.

    통과하면 (빈 문자열, 결속된 자리 전부)를 돌려준다. 자리는 (문장 번호, 그
    문장에서 활동이 놓인 시작 위치)다.

    ★ 표와 다른 점은 후보 쪽이 «칸»이 아니라 «문장»이라는 것뿐이다. 원문 쪽 검사
      (구절이 자기 인용 하나의 연속 부분인가 · 대상과 활동이 한 문장에 결속됐나 ·
      활동 바로 뒤 표지가 미래인가 · 주어가 남이 아닌가 · 전망을 굳히지 않았나 ·
      부정 방향이 같은가)는 표와 «같은 함수»를 그대로 쓴다.
    ⚠️ 주체 검사에 후보 문장을 넘기지 않는다. 표에서는 「주어가 이 줄의 칸에 적혀
       있으면 통과」가 안전한 완화였지만, 산문은 문장이 길어 원문의 «남의 주어»가
       후보 문장 어딘가에 우연히 들어 있기 쉽다. 실측 반례에서 원문 주어 「구조」가
       후보의 「산업의 구조 변화」에 그대로 있어 그 완화가 그대로 구멍이 된다.
    """

    for key in FUTURE_FIELD_KEYS:
        if key in item and not isinstance(item[key], str):
            return FUTURE_FIELD_TYPE_INVALID, ()
    target = _normalized(item.get(FUTURE_TARGET_KEY))
    activity = _normalized(item.get(FUTURE_ACTIVITY_KEY))
    slot_problem = _slot_problem(target, activity)
    if slot_problem:
        return slot_problem, ()

    holders = [
        (index, sentence, spans)
        for index, sentence in enumerate(sentences)
        if _has_bounded(sentence, target)
        and (spans := _bounded_spans(sentence, activity, verbal=True))
    ]
    if not holders:
        if not any(_has_bounded(sentence, target) for sentence in sentences):
            return FUTURE_TARGET_NOT_IN_CANDIDATE, ()
        return FUTURE_ACTIVITY_NOT_IN_CANDIDATE, ()

    source_id = str(item.get(FUTURE_SOURCE_KEY) or "").strip()
    if not source_id:
        return FUTURE_SOURCE_ID_EMPTY, ()
    if not isinstance(sources_mapping, Mapping) or source_id not in sources_mapping:
        return FUTURE_SOURCE_NOT_CITED, ()
    source = sources_mapping[source_id]

    quote = str(item.get(FUTURE_QUOTE_KEY) or "")
    if not quote.strip():
        return FUTURE_QUOTE_MISSING, ()
    quote_compact = _compact(quote)
    if len(quote_compact) < MIN_QUOTE_CHARS:
        return FUTURE_QUOTE_TOO_SHORT, ()
    if quote_compact not in _compact(source):
        return FUTURE_QUOTE_NOT_IN_SOURCE, ()

    quote_text = _normalized(quote)
    if not _has_bounded(quote_text, target):
        return FUTURE_TARGET_NOT_IN_QUOTE, ()
    if not _has_bounded(quote_text, activity, verbal=True):
        return FUTURE_ACTIVITY_NOT_IN_QUOTE, ()

    declared = str(item.get(FUTURE_MODE_KEY) or "").strip()
    if declared not in FUTURE_MODES:
        return FUTURE_MODE_INVALID, ()

    source_sentences = _covering_sentences(source, quote_text) or _sentences(quote_text)
    problems: list[str] = []
    positions: list[tuple[int, int]] = []
    # ★ 같은 대상·활동이 여러 자리에 되풀이될 수 있다. 한 자리만 보고 끝내면
    #   「앞 문장은 현재 서술, 뒤 문장이 계획」처럼 흔한 글에서 정상 근거가 앞
    #   자리에만 묶여, 정작 증명해야 할 계획 자리가 미증명으로 몰린다.
    #   그래서 «결속되는 자리를 모두» 모은다. 자리를 나눠 배정하는 일은 부르는
    #   쪽이 맡는다 — 한 항목이 두 주장을 겹쳐 덮지 못하게 하는 것도 거기서 한다.
    for sentence_index, candidate_sentence, candidate_spans in holders:
        for candidate_span in candidate_spans:
            if _bind_prose_position(
                source_sentences, candidate_sentence, candidate_span,
                target, activity, declared, problems,
            ):
                positions.append((sentence_index, candidate_span[0]))
    if positions:
        return "", tuple(positions)
    if problems:
        return problems[0], ()
    return FUTURE_TARGET_NOT_BOUND, ()


def _bind_prose_position(
    source_sentences: Sequence[str],
    candidate_sentence: str,
    candidate_span: tuple[int, int],
    target: str,
    activity: str,
    declared: str,
    problems: list[str],
) -> bool:
    """후보 문장의 «이 자리 하나»가 원문으로 결속되는지 본다. 잣대는 표와 같다.

    막힌 사유는 `problems` 에 쌓아 둔다 — 어느 자리도 결속되지 않았을 때 처음
    사유를 그대로 돌려주기 위해서다(사유 코드는 새로 만들지 않는다).
    """

    for source_sentence in source_sentences:
        for activity_span in _bounded_spans(source_sentence, activity, verbal=True):
            kind = _target_bound_to_activity(source_sentence, target, activity_span)
            if not kind:
                problems.append(FUTURE_TARGET_NOT_BOUND)
                continue
            if kind in ("adjacent", "means") and (
                _pair_bridge_kind(candidate_sentence, target, candidate_span) != kind
            ):
                problems.append(FUTURE_TARGET_NOT_BOUND)
                continue
            subject_problem = _subject_problem(
                source_sentence, target, activity_span[0], ()
            )
            if subject_problem:
                problems.append(subject_problem)
                continue
            mode, modality_problem, negation = _source_modality(
                source_sentence, activity_span)
            if modality_problem:
                problems.append(modality_problem)
                continue
            if mode != declared:
                problems.append(FUTURE_MODE_MISDECLARED)
                continue
            # ★ 전망을 확정 계획으로 굳히지 못한다. 한정은 후보 문장에서도 그 활동
            #   «뒤»에 남아야 한다 — 문장 앞쪽의 무관한 「기대」를 빌려 오지 않는다.
            if mode == FUTURE_MODE_OUTLOOK and not CANDIDATE_OUTLOOK_RE.search(
                candidate_sentence, candidate_span[1]
            ):
                problems.append(FUTURE_OUTLOOK_HARDENED)
                continue
            if bool(negation) != _candidate_polarity(candidate_sentence, activity):
                problems.append(FUTURE_POLARITY_FLIPPED)
                continue
            return True
    return False


def future_plan_prose_problem(
    text: str,
    sources_mapping: Mapping[str, str],
    evidence: object,
) -> str:
    """성장 전략 본문 문장이 «회사의 계획·전망»을 명시했을 때만 미래 근거를 결속한다.

    ★ 부르는 쪽이 «6장 성장 전략의 본문 문장일 때만» 부른다. 표는 기존
      `future_plan_problem` 이 그대로 맡고 계약이 바뀌지 않는다.
    ★ 발동은 좁다 — 그 문장이 회사를 주어로 세우고 계획·전망 표지를 함께 쓸 때만이다.
      산업·시장의 현재 변화, 회사가 이미 하고 있는 일, 배경 설명은 발동하지 않는다.
    ★ 발동한 «주장 자리마다» 증명이 있어야 한다. 한 자리를 증명하고 나머지를 얹지
      못한다. 항목마다 자기 인용 하나의 연속 구절이어야 하는 것도 표와 같다.
    ⚠️ 빈 문자열은 승인이 아니라 «이 검사가 반례를 찾지 못했다»는 뜻이다.
    """

    claims = _prose_plan_claims(text)
    if not claims:
        return ""
    items = _entries(evidence)
    if items is None:
        return FUTURE_EVIDENCE_MISSING
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return FUTURE_FIELD_TYPE_INVALID
    entries = [item for item in items if isinstance(item, Mapping)]
    if len(entries) != len(items):
        return FUTURE_FIELD_TYPE_INVALID
    if not entries:
        return FUTURE_EVIDENCE_MISSING

    sentences = _sentences(_normalized(text))
    proofs: list[tuple[tuple[int, int], ...]] = []
    for item in entries:
        problem, positions = _one_prose_item_problem(item, sentences, sources_mapping)
        if problem:
            return problem
        proofs.append(positions)
    # ★ 표지마다 «그 표지에 딸린» 활동이 증명돼야 한다. 앞 표지가 이미 쓴 활동으로
    #   뒤 표지를 덮지 못한다 — 「A를 확대할 계획이며, B도 늘릴 방침」에서 A 하나만
    #   증명하고 B 를 얹는 우회를 막는다. 표 쪽 조각별 증명 요구와 같은 취지다.
    # ★ 문장 번호는 주장을 찾을 때 «그 자리에서» 받아 둔다. 같은 문장이 두 번
    #   나오면 문자열로 되찾는 방식은 언제나 앞 자리를 가리켜, 뒤 주장이 증명돼도
    #   미증명으로 몰린다.
    choices: list[set[int]] = []
    previous_end: dict[int, int] = {}
    for index, _sentence, marker_start, marker_end in claims:
        low = previous_end.get(index, 0)
        choices.append({
            item_index
            for item_index, positions in enumerate(proofs)
            if any(sentence_index == index and low <= start <= marker_start
                   for sentence_index, start in positions)
        })
        previous_end[index] = marker_end
    if not _every_claim_has_its_own_item(choices):
        return FUTURE_SECOND_CLAIM_UNPROVEN
    return ""


def _every_claim_has_its_own_item(choices: Sequence[set[int]]) -> bool:
    """주장 자리마다 «서로 다른» 근거 항목을 하나씩 배정할 수 있는지 본다.

    ★ 항목 하나가 여러 자리에 결속될 수 있으므로(같은 대상·활동의 되풀이),
      앞에서부터 아무거나 집으면 배정이 가능한데도 실패로 볼 수 있다. 그래서
      이미 배정된 항목을 «밀어내며» 다시 시도한다(증가 경로 탐색).
    ⚠️ 이것은 완화가 아니다. 항목 하나가 두 주장을 겹쳐 덮는 것은 여전히 막힌다 —
      배정은 «일대일»이다.
    """

    owner: dict[int, int] = {}

    def assign(claim_index: int, tried: set[int]) -> bool:
        for item_index in sorted(choices[claim_index]):
            if item_index in tried:
                continue
            tried.add(item_index)
            if item_index not in owner or assign(owner[item_index], tried):
                owner[item_index] = claim_index
                return True
        return False

    return all(assign(claim_index, set()) for claim_index in range(len(choices)))


def future_plan_entries_by_number(raw: str | None) -> dict[int, object]:
    """검수 응답 원문에서 «번호 → 검증근거»만 뽑는다. 판정은 하지 않는다.

    ★ 기존 관계 근거와 «같은» 파서를 쓴다. 같은 응답을 두 벌의 규칙으로 읽으면
      한쪽만 고쳐지는 사고가 난다(실제로 JSON 꺼내기가 세 벌이던 때 그랬다).
      같은 번호가 두 번 오면 그 번호 전체가 무효(None)이므로, 이 가드에서는
      «근거 없음»과 같게 취급되어 그 줄이 제외된다 — fail-closed다.
    """

    from src.features.composer.direct_support import support_entries_by_number

    return support_entries_by_number(raw)
