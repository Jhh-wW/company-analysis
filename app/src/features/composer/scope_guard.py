"""명시된 상품 행의 조건을 다른 상품·넓은 범위로 옮긴 경우만 찾는다.

빈 문자열은 이 좁은 구조 검사에서 반례를 찾지 못했다는 뜻이다. 의미 검수,
수치·기간·서명·뉴스 검사를 대체하지 않으며 상품명이 누락되면 추측하지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 같은 feature 의 배선 도우미가 소유하는 context 타입(총괄 설계 채택안). 실행 시
    # import 하지 않는다 — 도우미가 이 모듈을 부르지 않아 순환도 없다.
    from src.features.composer.entity_scope_constraints import EntityScopeContext

from src.features.composer.scope_constants import (
    BROAD_SCOPE_RE, BULLET_RE, CLAUSE_RE, CONDITION_RE, LOCAL_EXAMPLE_RE, LOCAL_UNIT_RE,
    FLOW_CONDITION_END_RE, FLOW_DIRECT_SCOPE_BRIDGE_RE, FLOW_SENTENCE_SUBJECT_RE,
    GENERIC_STREAM_WORDS,
    OWNER_BRIDGE_RE, RELATIVE_OWNER_RE, ROW_SEPARATOR_RE,
    SCOPE_CONDITION_UNBOUND, TABLE_HEADERS,
    RECOGNITION_CLAUSE_RE, RECOGNITION_SOURCE_BOUNDARY_RE, RECOGNITION_COMMA_RE,
    RECOGNITION_OPEN_CONDITION_RE, RECOGNITION_DEPENDENT_START_RE,
    REVENUE_STREAM_SUBJECT_RE, REVENUE_SUBJECT_RE, RECOGNITION_RE,
    COMPLETION_RE, PROGRESS_RE, DURATION_LIMIT_RE,
    EXPLICIT_EXCLUSION_RE, ENTITY_SUBJECT_RE, CURRENT_SUBSIDIARY_RE,
    HISTORICAL_MEMBERSHIP_RE,
    ADNOMINAL_FORM_RE, CURRENT_CONSOLIDATION_RE, CURRENT_PERIOD_RE,
    ENTITY_INCLUSION_EVENT_RE,
    FOOTNOTE_MARK_RE, FOOTNOTE_REINCLUSION_RE, FOOTNOTE_TABLE_HEAD_RE,
    FOOTNOTE_TABLE_WORDS, HOLDING_APPOSITIVE_BRIDGE_RE, OBJECT_ARGUMENT_RE,
    PERIOD_MARKER_RE, PREDICATE_BOUNDARY_RE, PRIOR_PERIOD_RE, STATUS_NEGATION_RE,
    SUBJECT_ARGUMENT_RE, TOPIC_ARGUMENT_RE,
)
# 공시가 회사 자신을 가리키는 낱말 — 목적 해석 가드와 «같은» 목록을 쓴다.
from src.features.composer.direct_support_constants import SELF_REFERENCE_SUBJECTS

_NAME_CHAR_RE = re.compile(r"[A-Za-z가-힣]")
_TOKEN_EDGE_PUNCTUATION = "()[]{}<>,:;·"


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class _Unit:
    text: str
    owner: str = ""
    local: bool = False


def _units(text: str) -> list[_Unit]:
    """명시적인 표 구분자·제목 다음 bullet만 묶고 조각 사이 제목은 빌리지 않는다."""
    units: list[_Unit] = []
    previous = ""
    for raw in text.splitlines():
        line = raw.strip().strip("|").strip()
        if not line:
            continue
        pieces = ROW_SEPARATOR_RE.split(line, maxsplit=1)
        owner = ""
        if len(pieces) == 2 and not CONDITION_RE.search(pieces[0]) and pieces[0] not in TABLE_HEADERS:
            owner = pieces[0].strip()
        elif BULLET_RE.match(line) and previous and previous not in TABLE_HEADERS:
            if not CONDITION_RE.search(previous) and not BULLET_RE.match(previous) and not BROAD_SCOPE_RE.search(previous):
                owner = previous
        condition = CONDITION_RE.search(line)
        broad_subject = condition and BROAD_SCOPE_RE.search(line[:condition.start()])
        local = bool(owner or (BULLET_RE.match(line) and LOCAL_UNIT_RE.search(line) and not broad_subject))
        units.append(_Unit(line, owner, local))
        previous = line
    return units


def _conditions(text: str) -> list[tuple[tuple[str, ...], re.Match[str]]]:
    found = []
    for match in CONDITION_RE.finditer(unicodedata.normalize("NFKC", text)):
        if match["grade"]:
            key = ("grade", match["grade"].casefold(), match["grade_op"])
        elif match["number"]:
            key = ("number", match["number"].replace(",", ""), match["unit"], match["number_op"])
        else:
            key = ("target", _surface(match["target"]), _surface(match["target_op"]))
        found.append((key, match))
    return found


def _scopes(text: str) -> set[str]:
    return {_surface(match.group()) for match in BROAD_SCOPE_RE.finditer(text)}


def _owner_bound(clause: str, owner: str, condition: re.Match[str], owners: set[str]) -> bool:
    if not owner:
        return False
    for mention in re.finditer(re.escape(owner), clause, re.IGNORECASE):
        if mention.end() <= condition.start():
            bridge = clause[mention.end():condition.start()].strip()
            other_owner = any(other != owner and other in bridge for other in owners)
            if not other_owner and not OWNER_BRIDGE_RE.search(bridge) and not BROAD_SCOPE_RE.search(bridge):
                return True
        elif mention.start() >= condition.end():
            bridge = clause[condition.end():mention.start()]
            other_owner = any(other != owner and other in bridge for other in owners)
            if not other_owner and RELATIVE_OWNER_RE.search(bridge) and not BROAD_SCOPE_RE.search(bridge):
                return True
    return False


def _stream_subjects(clause: str) -> frozenset[str]:
    """절이 「<이름> 매출/수익 + 주제 조사」로 명명한 수익원 이름의 표면형."""

    return frozenset(
        stream for match in REVENUE_STREAM_SUBJECT_RE.finditer(clause)
        if (stream := _surface(match["stream"]))
        and stream not in GENERIC_STREAM_WORDS
    )


def _recognition_source_clauses(source: str) -> list[str]:
    """완료 용역의 열린 기간 조건만 쉼표 뒤 종속 인식 절에 붙인다."""
    clauses: list[str] = []
    for sentence in RECOGNITION_SOURCE_BOUNDARY_RE.split(source):
        pending: list[str] = []
        for clause in RECOGNITION_COMMA_RE.split(sentence):
            if pending:
                if RECOGNITION_DEPENDENT_START_RE.search(clause) and RECOGNITION_RE.search(clause):
                    clauses.append(", ".join((*pending, clause)))
                    pending = []
                    continue
                if RECOGNITION_OPEN_CONDITION_RE.search(clause) and not RECOGNITION_RE.search(clause):
                    pending.append(clause)
                    continue
                # 완결된 서술이나 독립 주어가 나오면 앞 조건을 빌려주지 않는다.
                clauses.extend(pending)
                pending = []
            if (DURATION_LIMIT_RE.search(clause) and COMPLETION_RE.search(clause)
                    and REVENUE_SUBJECT_RE.search(clause)
                    and RECOGNITION_OPEN_CONDITION_RE.search(clause)
                    and not RECOGNITION_RE.search(clause)):
                pending.append(clause)
            else:
                clauses.append(clause)
        clauses.extend(pending)
    return clauses


def _predicate_unit(clause: str, claim: re.Match[str]) -> tuple[int, int]:
    """단정이 속한 «술어 단위»(연결 어미 경계 사이)의 시작·끝 위치."""

    start, end = 0, len(clause)
    for boundary in PREDICATE_BOUNDARY_RE.finditer(clause):
        if boundary.end() <= claim.start():
            start = boundary.end()
        elif boundary.start() >= claim.end():
            end = boundary.start()
            break
    return start, end


def _status_claims(clause: str) -> list[re.Match[str]]:
    """절이 단정한 «현재 종속기업·연결 대상» 서술 — 그 술어에 붙은 부정은 뺀다.

    부정은 단정과 같은 술어 단위 안에서만 본다. 「…연결 대상으로 관리하며 영업
    비용은 부담하지 않는다」의 뒤 부정은 다른 술어라 단정을 지우지 않는다.
    """

    claims = [*CURRENT_SUBSIDIARY_RE.finditer(clause),
              *CURRENT_CONSOLIDATION_RE.finditer(clause)]
    kept = []
    for claim in claims:
        _start, end = _predicate_unit(clause, claim)
        if not STATUS_NEGATION_RE.search(clause, claim.end(), end):
            kept.append(claim)
    return kept


def _row_label(text: str, marker_start: int) -> str:
    """표식이 붙은 행의 법인 이름 — 같은 줄에서 표식 왼쪽의 이름 낱말 묶음.

    숫자·기호 낱말이나 닫힌 표 머리말 낱말을 만나면 멈춘다. 이름 일부만 겹치는
    다른 법인(「다온 가람 …」)과 섞이지 않도록 왼쪽 낱말을 끝까지 포함한다.
    """

    line_start = text.rfind("\n", 0, marker_start) + 1
    tokens = text[line_start:marker_start].split()
    label: list[str] = []
    for token in reversed(tokens):
        core = token.strip(_TOKEN_EDGE_PUNCTUATION)
        if not core or not _NAME_CHAR_RE.search(core) or core in FOOTNOTE_TABLE_WORDS:
            break
        label.append(token)
    return " ".join(reversed(label))


def _row_period_is_current(text: str, row_start: int, body: str) -> bool:
    """행의 표·각주가 «당기»를 명시할 때만 참 — 문자열만으로 최신이라고 가정하지 않는다.

    ① 행이 속한 소표 머리부터 행까지에 당기가 있으면 현재, 전기만 있으면 다른
       기간이다. ② 소표에 기간이 없으면 각주 본문의 당기 표지를 본다. ③ 그래도
       없으면 행 앞 원문의 가장 가까운 기간 표지가 당기일 때만 현재다. 기간 표지가
       전혀 없으면 현재 제약으로 확정하지 않는다(문서 출처·기준일을 알 수 없다).
    """

    heads = [head.start() for head in FOOTNOTE_TABLE_HEAD_RE.finditer(text, 0, row_start)]
    context = text[heads[-1] if heads else 0:row_start]
    if CURRENT_PERIOD_RE.search(context):
        return True
    if PRIOR_PERIOD_RE.search(context):
        return False
    if CURRENT_PERIOD_RE.search(body):
        return True
    nearest = [marker.group() for marker in PERIOD_MARKER_RE.finditer(text, 0, row_start)]
    return bool(nearest) and bool(CURRENT_PERIOD_RE.fullmatch(nearest[-1]))


def _sentence_names_other_entity(sentence: str, label: str) -> bool:
    """제외 문장이 자기 주어로 «다른» 법인을 명시했는가 — 자기 지칭은 같은 회사다."""

    owner = ENTITY_SUBJECT_RE.search("\n" + sentence.strip())
    if owner is None:
        return False
    name = _surface(owner["owner"])
    return name not in SELF_REFERENCE_SUBJECTS and _surface(label) not in name


def _footnote_excluded_labels(source: str) -> set[str]:
    """같은 원문 안에서 «표 행 표식 ↔ 같은 표식 각주»로 제외가 결속된 법인 이름.

    결속 조건(모두):
      · 행 표식은 이름에 붙어 있고, 결속 각주는 그 행 «뒤»의 같은 표식 첫 각주다.
        그 사이에 새 표·주석 머리(「(2)」·「2.」)가 있으면 다른 표의 각주이므로
        묶지 않는다(앞 표 각주가 발췌에서 빠지고 뒤 표가 같은 표식을 다시 쓰는 꼴).
      · 각주 본문은 다음 각주 표식이나 다음 표·주석 머리에서 끝난다 — 뒤 구획의
        다른 법인 제외 문장을 앞 각주에 합치지 않는다.
      · 본문에 이미 일어난 종속기업·연결 범위 제외가 있고, 그 문장이 자기 주어로
        다른 법인을 명시하지 않으며, 재편입으로 되돌리지 않았다.
      · 행 기간이 명시적으로 당기다(`_row_period_is_current`).
    """

    text = unicodedata.normalize("NFKC", source)
    markers = list(FOOTNOTE_MARK_RE.finditer(text))
    definitions = [marker for marker in markers
                   if not marker.start() or text[marker.start() - 1].isspace()]
    heads = [head.start() for head in FOOTNOTE_TABLE_HEAD_RE.finditer(text)]
    labels: set[str] = set()
    for row in markers:
        if row in definitions:
            continue
        definition = next((item for item in definitions
                           if item.group() == row.group() and item.start() > row.end()), None)
        if definition is None or any(row.end() <= head < definition.start() for head in heads):
            continue
        stops = [item.start() for item in definitions if item.start() > definition.start()]
        stops += [head for head in heads if head > definition.end()]
        body = text[definition.end():min(stops, default=len(text))]
        if FOOTNOTE_REINCLUSION_RE.search(body):
            continue
        label = _row_label(text, row.start())
        if len(_surface(label)) < 2:
            continue
        excluded = any(
            EXPLICIT_EXCLUSION_RE.search(sentence)
            and not _sentence_names_other_entity(sentence, label)
            for sentence in re.split(r"(?<=다)\.\s*", body)
        )
        if excluded and _row_period_is_current(text, row.start(), body):
            labels.add(label)
    return labels


def _label_pattern(label: str) -> str:
    """법인 이름을 띄어쓰기 차이까지 허용해 낱말 경계로 찾는 정규식."""

    return (r"(?<![A-Za-z0-9가-힣])"
            + r"\s+".join(re.escape(token) for token in label.split())
            + r"(?![A-Za-z0-9])")


def _nearest_argument(pattern: re.Pattern[str], clause: str, start: int, end: int) -> int | None:
    """[start, end) 안에서 end에 가장 가까운 격조사 낱말의 «조사 시작» 위치."""

    found = [match.start("particle") for match in pattern.finditer(clause, start, end)
             if not ADNOMINAL_FORM_RE.search(match.group())]
    return found[-1] if found else None


def _claim_argument_end(clause: str, claim: re.Match[str]) -> int | None:
    """단정의 주체·대상 이름이 끝나는 위치(조사 시작) — 확실한 결속만 돌려준다.

    · 「…으로(두다·관리하다)」·「…에 포함하다/해당하다」: 같은 술어 단위 안에서
      가장 가까운 목적격(을/를)이 대상이다. 목적격이 없으면 주제·주격으로 넘어간다.
    · 「…이다」·목적격 없는 경우: 같은 술어 단위의 «국소» 주제(은/는), 없으면 국소
      주격(이/가). 「…이다」 앞의 목적격은 관형절 안의 것이라(「회사가 지분을 보유한
      종속기업이다」) 대상으로 쓰지 않는다.
    · 국소 주격(이/가)은 조사 끝에서 단정 시작까지 «공백만» 있을 때(「B가 연결 대상이다」)
      만 주체로 쓴다. 사이에 다른 내용이 있으면(「가람이 지분을 보유한 종속기업이다」 —
      관형절 주격일 수 있다) 판정을 보류해 None 을 돌려준다. 앞 주제로 되돌아가
      추측하지 않는다 — 그 문장은 의미 검수 범위에 남는다(총괄 채택 최소 축소).
    · 국소 주체가 전혀 없을 때만 앞 술어 단위의 주제(없으면 주격)를 물려받는다 —
      「A는 …하고 B가 연결 대상이다」의 단정 주체는 뒤 단위의 B다(독립 검증 대칭 반례).
    """

    unit_start, _unit_end = _predicate_unit(clause, claim)
    if not claim.group().endswith("이다"):
        object_end = _nearest_argument(OBJECT_ARGUMENT_RE, clause, unit_start, claim.start())
        if object_end is not None:
            return object_end
    local_topic = _nearest_argument(TOPIC_ARGUMENT_RE, clause, unit_start, claim.start())
    if local_topic is not None:
        return local_topic
    local_subject = _nearest_argument(SUBJECT_ARGUMENT_RE, clause, unit_start, claim.start())
    if local_subject is not None:
        # 주격 조사는 한 글자(이/가)다. 조사 뒤부터 단정 앞까지 공백만이어야 직접 결속이다.
        if clause[local_subject + 1:claim.start()].strip():
            return None
        return local_subject
    for pattern in (TOPIC_ARGUMENT_RE, SUBJECT_ARGUMENT_RE):
        inherited = _nearest_argument(pattern, clause, 0, unit_start)
        if inherited is not None:
            return inherited
    return None


def _claim_binds_label(clause: str, label: str, claim: re.Match[str]) -> bool:
    """현재 관계 단정이 «그 법인»에 대한 것인가 — 주체·대상과 술어를 같은 자리로 묶는다.

    · 관형 수식 「연결 대상인·종속기업인 <법인>」: 단정 바로 뒤의 이름.
    · 보유 서술 「<법인> (등) 종속기업을 보유」: 단정 바로 앞의 이름.
    · 그 밖: `_claim_argument_end` 가 고른 주체·대상이 그 이름으로 끝나야 한다.
      다른 법인의 술어(「A를 지원하고 B를 연결 대상으로 관리한다」의 B)를 빌려
      A에 붙이지 않는다.
    관계자 표의 분류 이름을 붙인 「종속기업 <법인>에 대여」는 단정이 아니다.
    """

    mentions = list(re.finditer(_label_pattern(label), clause, re.IGNORECASE))
    if claim.group().endswith("인"):
        return any(claim.end() <= mention.start()
                   and not clause[claim.end():mention.start()].strip()
                   for mention in mentions)
    if "보유" in claim.group():
        return any(mention.end() <= claim.start()
                   and HOLDING_APPOSITIVE_BRIDGE_RE.fullmatch(clause[mention.end():claim.start()])
                   for mention in mentions)
    argument_end = _claim_argument_end(clause, claim)
    return argument_end is not None and any(
        mention.end() == argument_end for mention in mentions)


def _inclusion_recorded(label: str, sources: Mapping[str, str]) -> bool:
    """인용한 원문 어느 문장이든 같은 법인의 편입·재편입 사건을 적었는가.

    제외와 편입이 서로 다른 조각에 있으면 문자열만으로 선후를 정할 수 없다 —
    과거 제약을 현재 사실로 확정하지 않는다.
    """

    pattern = re.compile(_label_pattern(label), re.IGNORECASE)
    for source in sources.values():
        for sentence in re.split(r"(?<=다)\.\s*|[;\n]", unicodedata.normalize("NFKC", source)):
            if pattern.search(sentence) and ENTITY_INCLUSION_EVENT_RE.search(sentence):
                return True
    return False


def _claims_bound_to(text: str, label: str) -> bool:
    """후보의 어느 절이든 그 법인에 결속된 현재 관계 단정이 있는가(과거 서술 제외)."""

    for clause in re.split(r"[.;。\n,，]", text):
        if HISTORICAL_MEMBERSHIP_RE.search(clause):
            continue
        if any(_claim_binds_label(clause, label, claim) for claim in _status_claims(clause)):
            return True
    return False


def _footnote_exclusion_problem(text: str, sources: Mapping[str, str]) -> str:
    """인용한 각주가 제외한 법인을 현재 종속·연결 대상이라고 단정했는지 본다.

    대조는 «같은 원문 조각» 안의 행 표식·각주 결속에만 기댄다 — 다른 조각·다른
    법인·다른 기간의 제외를 빌리지 않는다. 과거 관계 서술은 막지 않는다.
    """

    labels = {label for source in sources.values()
              for label in _footnote_excluded_labels(source)}
    for label in labels:
        if not _inclusion_recorded(label, sources) and _claims_bound_to(text, label):
            return SCOPE_CONDITION_UNBOUND
    return ""


def _narrative_scope_problem(text: str, sources: Mapping[str, str]) -> str:
    """한정이 직접 붙은 인식 방식과 명시된 제외 주체만 대조한다.

    일반적인 의미 일치 판정이 아니다. 다른 상품의 조건이나 다른 법인의
    제외를 공유 단어만으로 연결하지 않으며, 모호한 주체는 의미 검수에 남긴다.
    """
    source_clauses = [clause for source in sources.values()
                      for clause in _recognition_source_clauses(source)]
    # 인식 기준(완료·진행)을 단정한 절은 «같은 수익원에 그 기준을 단» 인식
    # 서술이 자기 인용 절에 실제로 있어야 한다. 4차 실측 — 수익인식 절이 아예
    # 없는 회사개요 인용만으로 「용역 매출은 완료한 날에 수익으로 인식」이
    # 참으로 승인됐고, 독립 검토는 다른 수익원(콘텐츠)의 기준을 용역 매출로
    # 옮겨 적는 빌림 반례를 추가로 확정했다. 수익원 낱말 겹침은 술어 근거가
    # 아니며, 이름 있는 수익원의 기준은 같은 원문 절에서 이름과 함께 결속한다.
    # 기준 표지가 없는 문장(사업 서술·「시점에 인식」류)은 여기서 판정하지
    # 않는다 — 기존 의미 검수 몫이다.
    for clause in RECOGNITION_CLAUSE_RE.split(text):
        if not (REVENUE_SUBJECT_RE.search(clause) and RECOGNITION_RE.search(clause)):
            continue
        streams = _stream_subjects(clause)
        for basis_re in (COMPLETION_RE, PROGRESS_RE):
            if not basis_re.search(clause):
                continue
            grounded = [
                source_clause for source_clause in source_clauses
                if basis_re.search(source_clause) and RECOGNITION_RE.search(source_clause)
            ]
            # 후보가 이름 붙인 수익원은 «다른 수익원을 명명한» 원문 절에서 기준을
            # 빌릴 수 없다. 주어 없는 원문 절은 이름 경합이 없으므로 막지 않는다
            # — 문장 경계로 조건이 끊긴 기존 통과 사례를 바꾸지 않기 위해서다.
            if not grounded or any(
                all(stream not in _surface(source_clause)
                    and _stream_subjects(source_clause)
                    for source_clause in grounded)
                for stream in streams
            ):
                return SCOPE_CONDITION_UNBOUND
    completion_sources = [clause for clause in source_clauses
                          if COMPLETION_RE.search(clause) and RECOGNITION_RE.search(clause)]
    limited_sources = [clause for clause in completion_sources if DURATION_LIMIT_RE.search(clause)]
    # 원칙(진행)과 한정된 특례(완료)가 실제 자기 인용에 모두 있을 때만 검사한다.
    if (limited_sources and all(DURATION_LIMIT_RE.search(clause) for clause in completion_sources)
        and any(PROGRESS_RE.search(clause) and RECOGNITION_RE.search(clause) for clause in source_clauses)):
        source_limits = {(m["value"], m["unit"]) for clause in limited_sources
                         for m in DURATION_LIMIT_RE.finditer(clause)}
        for clause in RECOGNITION_CLAUSE_RE.split(text):
            if not (REVENUE_SUBJECT_RE.search(clause) and COMPLETION_RE.search(clause)
                    and RECOGNITION_RE.search(clause)):
                continue
            candidate_limits = {(m["value"], m["unit"]) for m in DURATION_LIMIT_RE.finditer(clause)}
            if not candidate_limits.intersection(source_limits):
                return SCOPE_CONDITION_UNBOUND
    for candidate_clause in re.split(r"[.;。\n,，]", text):
        if not _status_claims(candidate_clause) or HISTORICAL_MEMBERSHIP_RE.search(candidate_clause):
            continue
        for source in sources.values():
            for clause in re.split(r"[.;。\n]", source):
                if not EXPLICIT_EXCLUSION_RE.search(clause):
                    continue
                owner_match = ENTITY_SUBJECT_RE.search(clause)
                # 제외된 법인이 «그 단정의 주체·대상»일 때만 막는다(같은 절에 이름만
                # 있는 것으로는 부족하다). 인용한 다른 원문에 같은 법인의 편입·재편입
                # 사건이 있으면 선후를 알 수 없어 막지 않는다.
                if (owner_match
                        and not _inclusion_recorded(owner_match["owner"], sources)
                        and _claims_bound_to(candidate_clause, owner_match["owner"])):
                    return SCOPE_CONDITION_UNBOUND
    # 표 행 표식(「법인(*)」)과 같은 표식 각주로 결속된 제외 — 공시 주석의 실제 꼴.
    return _footnote_exclusion_problem(text, sources)


def _context_texts(value: Mapping[str, str] | None) -> tuple[str, ...]:
    """context 칸(조각 id → 원문 그대로)의 비어 있지 않은 원문들."""

    if not isinstance(value, Mapping):
        return ()
    return tuple(text for text in value.values() if isinstance(text, str) and text.strip())


def document_entity_scope_problem(
    candidate_text: str, contexts: Sequence[EntityScopeContext],
) -> str:
    """같은 공시 문서의 등록된 제외 각주를 «제약으로만» 소비하는 진입점.

    후보가 그 각주를 인용하지 않았어도, 인용한 조각과 같은 문서(비어 있지 않은
    ``document_identity``)의 관계법인 회계범위 각주가 명시적 당기 표의 행에 제외를
    결속했고, 후보가 그 법인을 현재 종속·연결 대상이라고 단정했으면
    `SCOPE_CONDITION_UNBOUND` 를 돌려준다. 새 사유 코드는 없다.

    지키는 선(총괄 설계 채택안):
      · 제약 원문은 판정에만 쓴다 — 긍정 근거·인용·원문 어느 것도 바꾸지 않는다.
      · 문서 신원이 빈 context 는 쓰지 않는다. 문서를 이름·제목으로 추정하지 않는다.
      · 행 표식·각주 결속, 표 구획, 명시 기간, 법인-술어 결속, 부정·과거 서술 판단은
        `scope_problem` 과 «같은» 함수를 쓴다(중복 parser 없음).
      · 인용 원문이나 제약 원문 어디든 같은 법인의 편입·재편입 사건이 있으면 선후를
        알 수 없어 막지 않는다.
    context 의 세 칸(``document_identity``·``cited_sources``·``constraint_sources``,
    조각 id → 원문)은 `entity_scope_constraints.EntityScopeContext` 계약을 따른다.
    """

    text = unicodedata.normalize("NFKC", candidate_text)
    known_sources: dict[str, str] = {}
    labels: set[str] = set()
    for index, context in enumerate(contexts):
        if not str(context.document_identity or "").strip():
            continue
        cited = _context_texts(context.cited_sources)
        constraints = _context_texts(context.constraint_sources)
        for position, source in enumerate((*cited, *constraints)):
            known_sources[f"{index}:{position}"] = source
        for constraint in constraints:
            labels.update(_footnote_excluded_labels(constraint))
    for label in sorted(labels):
        if not _inclusion_recorded(label, known_sources) and _claims_bound_to(text, label):
            return SCOPE_CONDITION_UNBOUND
    return ""


def scope_problem(candidate_text: str, sources_mapping: Mapping[str, str]) -> str:
    """고정 코드 또는 빈 문자열을 반환하는 무호출·무저장 범위 방어다.

    실제 조건이 있는 좁은 원문 행을 찾은 다음에만 범위 확대를 판정한다.
    조건이나 채널 키워드만으로 문장을 제거하지 않는다. 원문 자체가 같은
    넓은 주어에 조건을 직접 붙이거나 정확한 상품 예시를 유지하면 통과한다.
    """
    text = unicodedata.normalize("NFKC", candidate_text)
    narrative_problem = _narrative_scope_problem(text, sources_mapping)
    if narrative_problem:
        return narrative_problem
    units = [unit for source in sources_mapping.values() for unit in _units(unicodedata.normalize("NFKC", source))]
    owners = {unit.owner for unit in units if unit.owner}
    for clause in CLAUSE_RE.split(text):
        for key, condition in _conditions(clause):
            matching = [unit for unit in units if any(source_key == key for source_key, _ in _conditions(unit.text))]
            local = [unit for unit in matching if unit.local]
            if not local:
                continue
            if any(_owner_bound(clause, unit.owner, condition, owners) for unit in local):
                continue
            # 제목이 없어도 동일 원문에 있는 상품 유형에 조건을 직접 붙인 예시는 남긴다.
            examples = [match["owner"] for match in LOCAL_EXAMPLE_RE.finditer(clause, condition.end())]
            if any(owner in unit.text and _owner_bound(clause, owner, condition, owners)
                   for owner in examples for unit in local):
                continue
            candidate_scopes = _scopes(clause[:condition.start()])
            # 한 원문 전체에 같은 단어가 있다는 사실로 다른 행의 조건을 승인하지 않는다.
            if any(not unit.local and candidate_scopes & _scopes(unit.text) for unit in matching):
                continue
            different_owner = any(_owner_bound(clause, owner, condition, owners) for owner in owners)
            if candidate_scopes or different_owner:
                return SCOPE_CONDITION_UNBOUND
    return ""


def flow_scope_problem(cells: Sequence[str], sources_mapping: Mapping[str, str]) -> str:
    """한 행의 대상명 칸 → 인접한 자격 조건 칸에서만 적용 범위를 대조한다.

    칸·문장·원문을 이어 붙이지 않는다. 완전한 문장이나 중간의 다른 칸을 넘어
    주어를 빌리지 않으며, 알려지지 않은 조건·불명확한 칸 관계는 기존 검수에 남긴다.
    빈 결과는 전체 도식 승인이나 수치·시점·JSON 검증의 면제를 뜻하지 않는다.
    """
    values = [unicodedata.normalize("NFKC", cell).strip() for cell in cells]
    for cell in values:
        problem = scope_problem(cell, sources_mapping)
        if problem:
            return problem
    units = [unit for source in sources_mapping.values() for unit in _units(unicodedata.normalize("NFKC", source))]
    owners = {unit.owner for unit in units if unit.owner}
    for index, cell in enumerate(values):
        if not index or not FLOW_CONDITION_END_RE.search(cell) or len(CLAUSE_RE.split(cell)) != 1:
            continue
        target = values[index - 1]
        target_key = _surface(target)
        broad = bool(BROAD_SCOPE_RE.fullmatch(target))
        if not broad and not any(target_key == _surface(owner) for owner in owners):
            continue
        for key, condition in _conditions(cell):
            # 자체 주어를 가진 설명 문장에는 앞 칸의 대상을 덧씌우지 않는다.
            if FLOW_SENTENCE_SUBJECT_RE.search(cell[:condition.start()]):
                continue
            matching = [unit for unit in units if any(source_key == key for source_key, _ in _conditions(unit.text))]
            local = [unit for unit in matching if unit.local]
            if not local:
                continue
            if any(target_key == _surface(unit.owner) for unit in local if unit.owner):
                continue
            if any(_owner_bound(cell, unit.owner, condition, owners) for unit in local):
                continue
            direct = False
            for unit in matching:
                for clause in CLAUSE_RE.split(unit.text):
                    for source_key, source_condition in _conditions(clause):
                        if source_key != key:
                            continue
                        prefix = clause[:source_condition.start()]
                        for scope in BROAD_SCOPE_RE.finditer(prefix):
                            bridge = _surface(prefix[scope.end():])
                            if target_key == _surface(scope.group()) and FLOW_DIRECT_SCOPE_BRIDGE_RE.fullmatch(bridge):
                                direct = True
            if not direct:
                return SCOPE_CONDITION_UNBOUND
    return ""
