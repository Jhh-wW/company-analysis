"""수치의 항목과 기간을 원문에 결속한 뒤 비교 연산을 검산한다.

모델은 대응 구절을 제안할 뿐이며 승인 여부는 실제 입력 조각에서 다시 검사한다.
모든 산문을 정형화하지 않고 기존 검수가 놓친 의미 변형에만 추가 근거를 요구한다.
"""

from __future__ import annotations

from src.features.composer.direct_support import claims_cause, direct_support_problem, support_entries_by_number
from src.features.composer.direct_support_constants import RELATION_KEY
from src.features.composer.future_plan_constants import FUTURE_KEY
from src.features.composer.grounding_constants import REVIEW_SUPPORT_CANDIDATE_VERDICTS

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
import re
import unicodedata

from src.features.composer.modality_guard import modality_problem
from src.features.composer.role_binding import claims_role_or_fee, role_binding_problem
from src.features.composer.numeric_quote_refs import resolve_numeric_quote_refs
from src.features.composer.scope_guard import scope_problem

from src.features.composer.grounding_constants import (
    COMPARATIVE_RE, CONTINUOUS_RE, DOWN_RE, GROUNDING_INVALID, GROUNDING_KEY,
    GROUNDING_MISSING,
    MIN_CONTINUOUS_POINTS, MIN_TREND_POINTS, NUMERIC_KEY, PARENTHETICAL_RE,
    PARTICLE_RE, PLANNED_END_RE, RETROSPECTIVE_RE, SENTENCE_SPLIT_RE, TIME_KEY,
    TREND_DIRECTIONS, TREND_KEY, YEAR_RE, PAIR_SEPARATOR_RE, PRESENT_PERIOD,
    PRESENT_RE, REVIEW_ENTRIES_KEY, REVIEW_NUMBER_KEY, REVIEW_REJECTED,
    REVIEW_GROUNDING_REJECTED, WORD_CHARACTER_RE, VALUE_CONTINUATION_RE,
    APPROXIMATE_RE, RATIO_METRIC_RE, COMPOUND_AMOUNT_RE, TIME_STOPWORDS,
    TOKEN_RE, UP_RE,
    NUMERIC_BRIDGE_RE, RELATIVE_YEAR_RE, COUNT_UNIT_RE, ORDINAL_RE,
    RATIO_QUALIFIER_RE, QUARTER_RE, PREVIOUS_YEAR_OFFSET, MIN_METRIC_STEM_LENGTH,
    OBSERVATION_PERIOD_RE, QUARTERS_PER_YEAR, PERIOD_COMPARATIVE_RE,
    NEGATIVE_SIGNS, SURFACE_SIGN_CHARACTERS,
)


def _surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    # 문장부호는 무시하지만 1.2와 12, -100과 100은 같은 사실이 아니다.
    return "".join(c for index, c in enumerate(normalized) if not c.isspace() and (
        not unicodedata.category(c).startswith("P") or c in SURFACE_SIGN_CHARACTERS
        or (c == "." and index and index + 1 < len(normalized)
            and normalized[index - 1].isdigit() and normalized[index + 1].isdigit())
    ))


def _amounts(text: str) -> tuple:
    # 기존 단위 환산과 같은 숫자 파서를 써서 두 검증기의 배율이 갈라지지 않는다.
    from src.features.composer.verify import _extract_numbers, _NUMBER_UNIT_RE, _DATE_EXPR_RE
    def combine(match: re.Match) -> str:
        parts = _extract_numbers(match.group())
        return str(sum(n.token * n.scale for n in parts)) + "원"
    text = COMPOUND_AMOUNT_RE.sub(combine, text)
    parsed = tuple(n for n in _extract_numbers(text) if n.unit_marked and not n.is_year)
    dates = [match.span() for match in _DATE_EXPR_RE.finditer(text)]
    positions = [match.start() for match in _NUMBER_UNIT_RE.finditer(text)
                 if (match.group("mag") or match.group("tail"))
                 and not any(match.start() < end and start < match.end() for start, end in dates)]
    return tuple(replace(number, token=-number.token) if _negative_at(text, position) else number
                 for number, position in zip(parsed, positions, strict=True))


def _negative_at(text: str, position: int) -> bool:
    return (position > 0 and text[position - 1] in NEGATIVE_SIGNS
            and (position < 2 or not text[position - 2].isdigit()))


def _amount_values(text: str) -> Counter:
    return Counter(n.token * n.scale for n in _amounts(text))


def _direct_text(text: str, sources: Sequence[str]) -> bool:
    candidate = _surface(text)
    return bool(candidate) and any(candidate in _surface(source) for source in sources)


def _trend_spans(text: str) -> tuple[str, ...]:
    matches = [*CONTINUOUS_RE.finditer(text), *COMPARATIVE_RE.finditer(text),
               *PERIOD_COMPARATIVE_RE.finditer(text)]
    return tuple(match.group() for match in sorted(matches, key=lambda item: item.start()))


def _content_tokens(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for match in TOKEN_RE.finditer(text):
        token = _label(match.group())
        if len(token) >= 3 and token not in {_label(value) for value in TIME_STOPWORDS}:
            tokens.add(token)
    return frozenset(tokens)


def _claim_overlap(left: str, right: str) -> bool:
    """긴 고유 구절 둘 이상이 겹칠 때만 같은 활동 문맥으로 본다."""

    left_tokens = _content_tokens(left)
    right_tokens = _content_tokens(right)
    matched = {
        token
        for token in left_tokens
        if any(
            min(len(token), len(other)) >= 4
            and (token.startswith(other) or other.startswith(token))
            for other in right_tokens
        )
    }
    return len(matched) >= 2


def _retrospective_years(text: str, sources: Sequence[str]) -> frozenset[str]:
    years: set[str] = set()
    for source in sources:
        for match in RETROSPECTIVE_RE.finditer(source):
            # 같은 조각에 과거 표와 현재 설명이 함께 있다는 이유만으로 현재
            # 설명을 막지 않는다. 과거 문맥 이후의 실제 주장과 겹쳐야 한다.
            context = source[match.start():]
            clauses = SENTENCE_SPLIT_RE.split(context)
            historical: list[str] = []
            for index, clause in enumerate(clauses):
                # 과거 활동의 뒤쪽에 있는 별개 현재 문장을 과거 문맥으로
                # 흡수하지 않는다. 첫 절 안의 과거연도는 그대로 유지한다.
                if index and PRESENT_RE.search(clause):
                    break
                historical.append(clause)
            context = " ".join(historical)
            if _claim_overlap(text, context):
                years.add(match.group("year"))
    return frozenset(years)


def grounding_requirements(text: str, sources: Sequence[str]) -> tuple[str, ...]:
    """검증 필요를 식별하며, 어휘 등장만으로 후보를 거절하지 않는다."""
    required: list[str] = []
    direct = _direct_text(text, sources)
    if _amounts(text) and not direct:
        required.append(NUMERIC_KEY)
    # 수치와 함께 쓴 연속·전년 대비 방향만 재계산한다. 정성 설명과 계획은
    # 기존 의미 검수에 남겨 두어 추론을 일괄 삭제하지 않는다.
    if (_amounts(text) and _trend_spans(text) and not direct
        and not PLANNED_END_RE.search(text)):
        required.append(TREND_KEY)
    years = _retrospective_years(text, sources)
    if (years and not direct
        and (not years.intersection(YEAR_RE.findall(text)) or PRESENT_RE.search(text))):
        required.append(TIME_KEY)
    return tuple(required)


def _quote(entry: Mapping, sources: Mapping[str, str]) -> str | None:
    fragment_id, quote = entry.get("근거"), entry.get("원문")
    if not isinstance(fragment_id, str) or not isinstance(quote, str) or not quote.strip():
        return None
    source = sources.get(fragment_id)
    if source is None:
        return None
    for match in re.finditer(re.escape(quote), source):
        # 낱말·숫자의 중간을 잘라 다른 항목명이나 값처럼 제시하지 못한다.
        if (match.start() and WORD_CHARACTER_RE.fullmatch(quote[0])
            and WORD_CHARACTER_RE.fullmatch(source[match.start() - 1])):
            continue
        if (match.end() < len(source) and quote[-1].isdigit()
            and VALUE_CONTINUATION_RE.fullmatch(source[match.end()])):
            continue
        return quote
    return None


def _label(value: str) -> str:
    return _surface(PARTICLE_RE.sub("", value.strip()))


def _metric_matches(left: str, right: str) -> bool:
    # 항목의 뜻을 모델이 선언한 별칭으로 바꾸지 않는다. 원문 표면을 보존한
    # 공백·조사만 정리하며 독립 검수는 나머지 문장의 의미를 계속 판단한다.
    a, b = _label(left).removesuffix("규모"), _label(right).removesuffix("규모")
    if not a or not b:
        return False
    # 금액 지표의 '액' 생략만 허용한다. 순액/총액·비율 등 다른 뜻의
    # 지표를 모델이 선언한 별칭만으로 같다고 취급하지 않는다.
    return a == b or (min(len(a), len(b)) >= MIN_METRIC_STEM_LENGTH
                     and (a == b + "액" or b == a + "액"))


@dataclass(frozen=True)
class _BoundAmount:
    start: int
    end: int
    text: str
    value: Decimal
    dimension: str


def _amount_spans(text: str) -> tuple[_BoundAmount, ...]:
    """환산 파서와 같은 숫자를 읽되 각각의 원문 위치를 보존한다."""
    from src.features.composer.verify import _DATE_EXPR_RE, _NUMBER_UNIT_RE

    spans: list[tuple[int, int]] = [match.span() for match in COMPOUND_AMOUNT_RE.finditer(text)]
    dates = [match.span() for match in _DATE_EXPR_RE.finditer(text)]
    for match in _NUMBER_UNIT_RE.finditer(text):
        if not (match.group("mag") or match.group("tail")):
            continue
        if any(match.start() < end and start < match.end() for start, end in (*spans, *dates)):
            continue
        end = match.end()
        count_unit = COUNT_UNIT_RE.match(text, end)
        if count_unit:
            end = count_unit.end()
        start = match.start() - 1 if _negative_at(text, match.start()) else match.start()
        spans.append((start, end))
    spans = [(start - 1 if _negative_at(text, start) else start, end) for start, end in spans]
    result: list[_BoundAmount] = []
    for start, end in sorted(spans):
        value_text = text[start:end]
        values = _amount_values(value_text)
        if sum(values.values()) == 1:
            result.append(_BoundAmount(start, end, value_text, next(iter(values)), _dimension(value_text)))
    return tuple(result)


def _period_at(text: str, position: int) -> tuple[int, int | None] | None:
    """해당 값 앞의 명시 기간을 읽고, 연도가 없는 전년만 상대 계산한다."""
    from src.features.composer.verify import _DATE_EXPR_RE

    prefix = text[:position]
    years = list(_DATE_EXPR_RE.finditer(prefix))
    if not years:
        return None
    last_year = years[-1]
    year = int(last_year.group("year"))
    tail = prefix[last_year.end():]
    if RELATIVE_YEAR_RE.search(tail):
        year -= PREVIOUS_YEAR_OFFSET
    quarters = list(QUARTER_RE.finditer(tail))
    return year, int(quarters[-1].group(1)) if quarters else None


def _metric_value_spans(metric: str, value: str, text: str) -> tuple[_BoundAmount, ...]:
    """다년도 열거를 허용하되 중간에 새 항목이 나오면 결속을 끊는다."""
    from src.features.composer.verify import _DATE_EXPR_RE

    results: list[_BoundAmount] = []
    for clause_match in re.finditer(r"[^;\n]+", text):
        clause = clause_match.group()
        for anchor in re.finditer(re.escape(metric), clause):
            if anchor.start() and WORD_CHARACTER_RE.fullmatch(clause[anchor.start() - 1]):
                continue
            for number in _amount_spans(clause):
                if _surface(number.text) != _surface(value):
                    continue
                if number.end <= anchor.start():
                    # '37.02% 점유율'처럼 단위값 바로 뒤에 지표를 쓰는
                    # 도식·명사구도 정상이다. 다른 명사나 숫자는 건너지 않는다.
                    between = clause[number.end:anchor.start()]
                    following = [item for item in _amount_spans(clause) if item.start >= anchor.end()]
                    own_following_value = any(not PAIR_SEPARATOR_RE.sub("", NUMERIC_BRIDGE_RE.sub(
                        "", _DATE_EXPR_RE.sub("", clause[anchor.end():item.start])
                    )) for item in following)
                    if (not own_following_value
                        and not PAIR_SEPARATOR_RE.sub("", PARTICLE_RE.sub("", between.strip()))):
                        offset = clause_match.start()
                        results.append(_BoundAmount(number.start + offset, number.end + offset,
                                                    number.text, number.value, number.dimension))
                    continue
                if number.start < anchor.end():
                    continue
                between = clause[anchor.end():number.start]
                remainder = _DATE_EXPR_RE.sub("", between)
                remainder = QUARTER_RE.sub("", remainder)
                for previous in reversed(_amount_spans(remainder)):
                    remainder = remainder[:previous.start] + remainder[previous.end:]
                remainder = ORDINAL_RE.sub("", remainder)
                if number.dimension == "비율":
                    remainder = RATIO_QUALIFIER_RE.sub("", remainder)
                remainder = NUMERIC_BRIDGE_RE.sub("", remainder)
                remainder = PAIR_SEPARATOR_RE.sub("", remainder)
                if remainder:
                    continue
                offset = clause_match.start()
                results.append(_BoundAmount(number.start + offset, number.end + offset,
                                            number.text, number.value, number.dimension))
    return tuple(dict.fromkeys(results))


def _bound_period(metric: str, number: _BoundAmount, text: str) -> tuple[int, int | None] | None:
    period_position = number.start
    # 금액 비교 뒤의 변화율은 직전 비교대상 연도가 아닌 주지표 기간이다.
    # 비율 자체를 비교하는 '2024년 3.4% -> 2025년 7.9%'에는 적용하지 않는다.
    anchors = [m for m in re.finditer(re.escape(metric), text[:number.start])]
    if anchors and number.dimension == "비율":
        previous = [n for n in _amount_spans(text[anchors[-1].end():number.start])
                    if n.dimension == "금액"]
        if previous:
            period_position = anchors[-1].end() + previous[0].start
    return _period_at(text, period_position)


def _bound_value(entry: Mapping, quote: str) -> Decimal | None:
    metric, value = entry.get("원문항목", entry.get("항목")), entry.get("원문값")
    if not isinstance(metric, str) or not isinstance(value, str) or not metric or not value:
        return None
    if metric not in quote or value not in quote:
        return None
    values = _amount_values(value)
    # 비율 지표의 원시 소수만 백분율 환산 입력으로 허용한다.
    if not values and RATIO_METRIC_RE.search(_label(metric)):
        try:
            values = Counter({Decimal(value): 1})
        except Exception:
            return None
    if sum(values.values()) != 1:
        return None
    if _metric_value_spans(metric, value, quote):
        return next(iter(values))
    # 단위 없는 원시 비율은 다른 숫자를 건너뛰지 않는 직접 결속만 허용한다.
    if not _amounts(value) and RATIO_METRIC_RE.search(_label(metric)):
        for clause in SENTENCE_SPLIT_RE.split(quote):
            for match in re.finditer(re.escape(metric), clause):
                end = clause.find(value, match.end())
                if end < match.end():
                    continue
                between = clause[match.end():end]
                if not PAIR_SEPARATOR_RE.sub("", PARTICLE_RE.sub("", between.strip())):
                    return next(iter(values))
    return None


def _dimension(text: str) -> str:
    from src.features.composer.verify import _NUMBER_UNIT_RE
    units = {m.group("tail") for m in _NUMBER_UNIT_RE.finditer(text) if m.group("tail")}
    if units.intersection({"%", "퍼센트"}):
        return "비율"
    if "배" in units:
        return "배수"
    return "금액"


def _numeric_valid(text: str, entries: object, sources: Mapping[str, str]) -> bool:
    if not isinstance(entries, list) or not entries:
        return False
    covered: Counter = Counter()
    used_spans: list[tuple[int, int]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            return False
        expression, metric, source_metric = (entry.get(k) for k in ("표현", "항목", "원문항목"))
        quote = _quote(entry, sources)
        if (not all(isinstance(x, str) and x for x in (expression, metric, source_metric))
            or expression not in text or metric not in expression or quote is None
            or not _metric_matches(metric, source_metric)):
            return False
        expression_amounts = _amount_spans(expression)
        candidate_value = entry.get("후보값")
        if candidate_value is None and len(expression_amounts) == 1:
            candidate_value = expression_amounts[0].text
        if not isinstance(candidate_value, str):
            return False
        candidate_bindings = _metric_value_spans(metric, candidate_value, text)
        expression_spans = [m.span() for m in re.finditer(re.escape(expression), text)]
        candidate = next((number for number in candidate_bindings if any(
            start <= number.start and number.end <= end for start, end in expression_spans
        ) and all(number.end <= start or end <= number.start for start, end in used_spans)), None)
        span = (candidate.start, candidate.end) if candidate else None
        if span is None:
            return False
        used_spans.append(span)
        value = _bound_value(entry, quote)
        values = _amount_values(candidate_value)
        from src.features.composer.verify import _number_matches_by_math
        same_dimension = _dimension(candidate_value) == _dimension(entry["원문값"])
        ratio_conversion = (RATIO_METRIC_RE.search(_label(metric))
                            and not _amounts(entry["원문값"]) and _dimension(candidate_value) == "비율")
        if (value is None or sum(values.values()) != 1
            or not _number_matches_by_math(_amounts(candidate_value)[0], frozenset({value}))
            or not (same_dimension or ratio_conversion)):
            return False
        candidate_period = _bound_period(metric, candidate, text)
        source_bindings = _metric_value_spans(source_metric, entry["원문값"], quote)
        source_periods = {_bound_period(source_metric, number, quote) for number in source_bindings}
        if ratio_conversion:
            source_periods.add(_period_at(quote, quote.find(entry["원문값"])))
        if candidate_period and candidate_period not in source_periods:
            return False
        # 괄호 속 소계 이름만 골라 괄호 바깥의 다른 항목명을 지우지 못한다.
        for match in PARENTHETICAL_RE.finditer(text):
            if (value in _amount_values(match.group("body"))
                and metric in match.group("body")):
                if match.group() not in expression or not _metric_matches(match.group("label"), metric):
                    return False
        covered.update(values)
    return covered == _amount_values(text)


def _trend_valid(text: str, entries: object, sources: Mapping[str, str]) -> bool:
    if not isinstance(entries, list) or not entries:
        return False
    required_spans = list(_trend_spans(text))
    covered: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            return False
        expression, metric, direction = (entry.get(k) for k in ("표현", "항목", "방향"))
        points = entry.get("관측")
        if (not isinstance(expression, str) or not expression or expression not in text
            or not isinstance(metric, str) or not metric or metric not in text
            or direction not in TREND_DIRECTIONS or not isinstance(points, list)):
            return False
        continuous = bool(CONTINUOUS_RE.search(expression))
        if direction.startswith("지속") != continuous:
            return False
        has_up = bool(UP_RE.search(expression))
        has_down = bool(DOWN_RE.search(expression))
        if has_up == has_down or direction.endswith("증가") != has_up:
            return False
        observed: dict[tuple[int, int | None], Decimal] = {}
        dimensions: set[str] = set()
        for point in points:
            if not isinstance(point, Mapping):
                return False
            quote = _quote(point, sources)
            period, source_metric = point.get("기간"), point.get("항목")
            parsed_period = OBSERVATION_PERIOD_RE.fullmatch(period) if isinstance(period, str) else None
            if (quote is None or parsed_period is None
                or not isinstance(source_metric, str)
                or not _metric_matches(metric, source_metric)):
                return False
            value = _bound_value(point, quote)
            period_key = (int(parsed_period.group(1)),
                          int(parsed_period.group(2)) if parsed_period.group(2) else None)
            bindings = _metric_value_spans(source_metric, point.get("원문값", ""), quote)
            if (value is None or period_key in observed or not any(
                _bound_period(source_metric, number, quote) == period_key
                for number in bindings
            )):
                return False
            observed[period_key] = value
            dimensions.add(_dimension(point["원문값"]))
        minimum = MIN_CONTINUOUS_POINTS if direction.startswith("지속") else MIN_TREND_POINTS
        if (len(observed) < minimum or len(dimensions) != 1
            or len({quarter is None for _, quarter in observed}) != 1):
            return False
        ordered = sorted(observed)
        if not set(map(int, YEAR_RE.findall(text))).issubset({year for year, _ in observed}):
            return False
        def ordinal(period_key: tuple[int, int | None]) -> int:
            year, quarter = period_key
            return year if quarter is None else year * QUARTERS_PER_YEAR + quarter

        # 연속 증가에 중간 하락 연도를 빼는 근거 골라쓰기를 허용하지 않는다.
        if direction.startswith("지속") and any(ordinal(b) - ordinal(a) != 1
                                                  for a, b in zip(ordered, ordered[1:])):
            return False
        pairs = list(zip(ordered, ordered[1:])) if direction.startswith("지속") else [(ordered[0], ordered[-1])]
        if not continuous and (RELATIVE_YEAR_RE.search(expression)
                               or "전분기" in expression or "전기" in expression):
            current = ordered[-1]
            distance = (QUARTERS_PER_YEAR if current[1] is not None
                        and RELATIVE_YEAR_RE.search(expression) else 1)
            previous = next((period for period in ordered if ordinal(current) - ordinal(period) == distance), None)
            if previous is None:
                return False
            pairs = [(previous, current)]
        if any((observed[b] > observed[a]) != direction.endswith("증가")
               or observed[b] == observed[a] for a, b in pairs):
            return False
        covered.update(span for span in required_spans if span in expression)
    return covered == set(required_spans)


def _time_valid(text: str, entries: object, sources: Mapping[str, str]) -> bool:
    if not isinstance(entries, list) or not entries:
        return False
    required_spans: list[str] = []
    for clause in SENTENCE_SPLIT_RE.split(text):
        matches = list(PRESENT_RE.finditer(clause))
        if not matches:
            continue
        explicit = next((match for match in matches if match.group() in ("현재", "지금")), None)
        # 시점 표지나 회사 이름만 포함한 인용으로 뒤의 다른 활동을 승인할
        # 수 없다. 공개 후보가 말한 현재 활동의 나머지 구절까지 확인한다.
        claim = clause[explicit.start():] if explicit else clause
        required_spans.append(claim.rstrip(".!? "))
    # 활동연도가 빠진 과거형 문장은 검수 JSON에 연도를 써 넣는다고 공개
    # 문장 자체가 고쳐지지 않는다. 재작성된 문장만 다음 검수에서 통과한다.
    if not required_spans:
        return False
    covered: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            return False
        quote, period, expression = _quote(entry, sources), entry.get("기간"), entry.get("표현")
        if (quote is None or period != PRESENT_PERIOD or not isinstance(expression, str)
            or expression not in text or not PRESENT_RE.search(expression)
            or not PRESENT_RE.search(quote) or not _direct_text(expression, (quote,))
            or not _content_tokens(PRESENT_RE.sub("", expression))):
            return False
        covered.update(span for span in required_spans if _direct_text(span, (expression,)))
    return covered == set(required_spans)


def _reported_comparison_valid(text: str, entries: object, sources: Mapping[str, str]) -> bool:
    """원문이 직접 보도한 비교율을 같은 항목의 결속된 위치에서만 재사용한다."""
    if not isinstance(entries, list):
        return False
    claims = _trend_spans(text)
    if not claims:
        return False
    for claim in claims:
        rates = [number for number in _amount_spans(claim) if number.dimension == "비율"]
        if CONTINUOUS_RE.search(claim) or len(rates) != 1:
            return False
        claim_direction = (bool(UP_RE.search(claim)), bool(DOWN_RE.search(claim)))
        if claim_direction[0] == claim_direction[1]:
            return False
        matched = False
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            quote = _quote(entry, sources)
            metric, source_metric = entry.get("항목"), entry.get("원문항목")
            source_value = entry.get("원문값")
            if (quote is None or not all(isinstance(value, str) for value in (metric, source_metric, source_value))
                or not _metric_matches(metric, source_metric)):
                continue
            candidate_numbers = _metric_value_spans(metric, rates[0].text, text)
            claim_positions = [match.span() for match in re.finditer(re.escape(claim), text)]
            if not any(start <= number.start and number.end <= end
                       for number in candidate_numbers for start, end in claim_positions):
                continue
            for reported in _trend_spans(quote):
                if (bool(UP_RE.search(reported)), bool(DOWN_RE.search(reported))) != claim_direction:
                    continue
                if bool("전분기" in claim) != bool("전분기" in reported):
                    continue
                if bool(RELATIVE_YEAR_RE.search(claim)) != bool(RELATIVE_YEAR_RE.search(reported)):
                    continue
                reported_rates = [n for n in _amount_spans(reported) if n.dimension == "비율"]
                if len(reported_rates) != 1 or reported_rates[0].value != rates[0].value:
                    continue
                positions = [match.span() for match in re.finditer(re.escape(reported), quote)]
                source_numbers = _metric_value_spans(source_metric, source_value, quote)
                if any(start <= number.start and number.end <= end and number.value == rates[0].value
                       for number in source_numbers for start, end in positions):
                    matched = True
        if not matched:
            return False
    return True


def grounding_problem(text: str, sources: Mapping[str, str], entry: Mapping) -> str:
    """필요 근거 누락과 결속·연산 실패를 구분하며 호출이나 저장을 하지 않는다."""
    # 원문 한정이 사라진 후보는 검수 모델의 참·애매나 추가 근거 JSON으로
    # 승인하지 않는다. 다른 문장과 기존 수치·시점 검증 경로는 그대로 둔다.
    scope_issue = modality_problem(text, sources) or scope_problem(text, sources)
    if scope_issue:
        return scope_issue
    required = grounding_requirements(text, tuple(sources.values()))
    evidence = entry.get(GROUNDING_KEY)
    if not required and evidence is None:
        return ""
    if not isinstance(evidence, Mapping):
        return GROUNDING_MISSING
    # 같은 판정 번호의 수치 배열 안에서만 원문참조를 원문으로 되돌린다 — 다른
    # 배열(추세·시점)이나 다른 판정 번호는 이 함수가 한 번에 한 후보만 받으므로
    # 애초에 섞이지 않는다. 입력 evidence는 바꾸지 않고 되돌린 사본만 쓴다.
    numeric_entries = (
        resolve_numeric_quote_refs(evidence[NUMERIC_KEY])
        if NUMERIC_KEY in evidence else None
    )
    if (TREND_KEY in required and NUMERIC_KEY in evidence
        and _numeric_valid(text, numeric_entries, sources)
        and _reported_comparison_valid(text, numeric_entries, sources)):
        required = tuple(kind for kind in required if kind != TREND_KEY)
    validators = {NUMERIC_KEY: _numeric_valid, TREND_KEY: _trend_valid, TIME_KEY: _time_valid}
    for kind in required:
        if kind not in evidence:
            return GROUNDING_MISSING
    for kind, payload in evidence.items():
        # 관계 근거는 아래 constrain_verdicts가 번호 중복을 함께 확인한 뒤
        # 자기 인용에 결속한다. 기존 수치·추세·시점 검증은 전부 유지한다.
        if kind == RELATION_KEY:
            if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
                return GROUNDING_INVALID
            continue
        # 미래 근거는 6장 성장 계획 표에서만 쓰이며, future_plan_guard 가 그 줄의
        # 칸·인용에 따로 결속한다. 여기서는 모양만 보고 넘긴다 — 관계 근거와 같다.
        if kind == FUTURE_KEY:
            if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
                return GROUNDING_INVALID
            continue
        if kind not in required and payload == []:
            continue
        if kind not in validators:
            return GROUNDING_INVALID
        effective_payload = numeric_entries if kind == NUMERIC_KEY else payload
        if not validators[kind](text, effective_payload, sources):
            return GROUNDING_INVALID
    return ""


def grounding_hint(
    text: str, sources: Mapping[str, str], cells: Sequence[str] | None = None,
) -> str:
    """그 후보에 어떤 추가 근거가 필요한지 검수 프롬프트에 한 줄로 적는다.

    ★ 인과와 역할·과금은 같은 «관계» 배열을 쓴다. 요구 항목 이름을 나누지 않아야
      파서·프롬프트·가드가 한 이름으로 맞물린다.
    ★ cells 는 도식 후보일 때만 준다 — 칸은 낱말만으로도 주장이지만, 산문은 그
      낱말이 서술어로 쓰였을 때만 주장이다.
    """

    required = grounding_requirements(text, tuple(sources.values()))
    if claims_cause(text) or claims_role_or_fee(text, cells):
        required += (RELATION_KEY,)
    return "  추가 검증 필요: " + (", ".join(required) or "없음") + "\n"


def constrain_verdicts(
    raw: str | None,
    verdicts: Mapping[int, str],
    candidates: Mapping[int, tuple[str, Mapping[str, str]]],
    *,
    cells_by_number: Mapping[int, Sequence[str]] | None = None,
) -> tuple[dict[int, str], dict[int, str]]:
    """같은 검수 응답의 근거를 실제 입력에 결속한다. 추가 AI 호출은 없다.

    거짓 판정의 기존 재작성 기회는 유지한다. 참·애매의 결속 실패는 별도
    처분으로 반환해 해석 강등이나 형식 재시도로 우회하지 못하게 한다.
    """
    from src.features.composer.logic import extract_json_payload

    result = dict(verdicts)
    payload = extract_json_payload(raw or "")
    entries = payload.get(REVIEW_ENTRIES_KEY, []) if isinstance(payload, Mapping) else []
    problems: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        number = entry.get(REVIEW_NUMBER_KEY)
        if isinstance(number, bool) or not isinstance(number, int) or number not in candidates:
            continue
        if number not in verdicts or verdicts[number] == REVIEW_REJECTED:
            continue
        text, sources = candidates[number]
        problem = grounding_problem(text, sources, entry)
        if problem:
            result[number] = REVIEW_GROUNDING_REJECTED
            problems[number] = problem
    relation_evidence = support_entries_by_number(raw)
    for number, (text, sources) in candidates.items():
        if result.get(number) not in REVIEW_SUPPORT_CANDIDATE_VERDICTS or number in problems:
            continue
        # 도식 후보만 칸 경계를 함께 준다. 본문·요약은 None 이므로 한 문장
        # 안에서 절을 넘는 연결이 새로 허용되지 않는다.
        cells = (cells_by_number or {}).get(number)
        # 인과 결속과 역할·과금 결속은 같은 «관계» 배열을 읽는다. 어느 쪽이든 첫
        # 사유를 그대로 돌려 참·애매가 근거 없이 공개로 새지 않게 한다.
        problem = (
            direct_support_problem(
                text, sources, relation_evidence.get(number), cells,
            )
            or role_binding_problem(
                text, sources, relation_evidence.get(number), cells,
            )
        )
        if problem:
            result[number] = REVIEW_GROUNDING_REJECTED
            problems[number] = problem
    return result, problems
