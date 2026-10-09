"""요청 전에 검증한 미래 산문 증명을 모델의 명시 선택으로만 복원한다."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
from uuid import uuid4

from src.features.composer import future_proof_selection_constants as c
from src.features.composer.future_plan_constants import (
    FUTURE_KEY, FUTURE_SECTION_FORWARD_RE, STATED_PLAN_SLOT, MODALITY_RE,
    MODALITY_FUTURE_KINDS,
    SOURCE_NEGATION_RE,
)
from src.features.composer.future_plan_guard import (
    _bounded_spans, _normalized, _sentences, _slot_problem, _modality_kind,
    future_plan_prose_problem,
)
from src.features.composer.grounding_constants import GROUNDING_KEY, REVIEW_ENTRIES_KEY, TABLE_SOURCE_ID
from src.features.composer.port import CollectedFragment
from src.features.composer.verdict_number import coerce_verdict_number
from src.features.composer.business_population_scope import section_investment_plan_problem
from src.features.composer.source_actor_scope import source_actor_problem


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _fragment_snapshot(fragment: CollectedFragment) -> dict:
    snapshot = asdict(fragment)
    snapshot['text'] = _sha(fragment.text)
    return snapshot


@dataclass(frozen=True)
class FutureProofCandidate:
    number: int
    section_id: str
    claim_slot: str
    text: str
    fragments: tuple[CollectedFragment, ...]


@dataclass(frozen=True)
class FutureProofOption:
    number: int
    section_id: str
    claim_slot: str
    candidate_sha256: str
    request_sha256: str
    source_fingerprints: tuple[tuple[str, str], ...]
    binding_json: str
    proof_json: str

    @property
    def option_id(self) -> str:
        return 'fp1_' + _sha(_json({'version': c.FUTURE_SELECTION_VERSION,
            'number': self.number, 'section': self.section_id, 'slot': self.claim_slot,
            'candidate': self.candidate_sha256, 'request': self.request_sha256,
            'sources': self.source_fingerprints, 'binding': self.binding_json,
            'proof': self.proof_json}))


def _shared_phrases(quote: str, candidate: str, *, verbal: bool) -> tuple[str, ...]:
    """원문 구간만 열거한다. 경계·조사 동치는 기존 검사기의 계약을 따른다."""
    words = tuple(c.WORD_RE.finditer(candidate))
    if len(words) > c.MAX_WORDS:
        return ()
    normalized_quote, normalized_candidate = _normalized(quote), _normalized(candidate)
    quote_index = tuple(index for index, char in enumerate(normalized_quote) if not char.isspace())
    compact_quote = ''.join(normalized_quote[index] for index in quote_index)
    phrases = set()
    for left in words:
        for right in words:
            if right.start() < left.start():
                continue
            # 단어 뒤 조사·활용 어미를 제외할 수 있지만 실제 원문 좌표만 사용한다.
            for end in range(right.start() + 2, right.end() + 1):
                phrase = candidate[left.start():end]
                # 기존 PHRASE_GAP이 허용하는 조사만 대조 표기로 제외할 수 있다.
                # 실제 구간·활동 양태와 후보 양쪽 결속은 다시 검사한다.
                stripped = ' '.join(
                    token if ''.join(_normalized(token).split()) in compact_quote
                    else c.TRAILING_PARTICLE_RE.sub(r'\1', token)
                    for token in phrase.split()
                )
                for variant in dict.fromkeys((phrase, stripped)):
                    normalized = _normalized(variant)
                    compact = ''.join(normalized.split())
                    position = compact_quote.find(compact)
                    if c.LEADING_CONNECTIVE_RE.match(variant) or not compact or position < 0:
                        continue
                    end = quote_index[position + len(compact) - 1] + 1
                    if verbal and not (
                        (marker := MODALITY_RE.search(normalized_quote, end))
                        and _modality_kind(marker) in MODALITY_FUTURE_KINDS
                        and c.MODALITY_GAP_RE.fullmatch(normalized_quote[end:marker.start()])
                    ):
                        continue
                    phrases.add(variant)
    return tuple(sorted(phrases, key=lambda value: (-len(value), value))[:c.MAX_SHARED_PHRASES])


def prepare_future_proof_options(
    candidates: Mapping[int, FutureProofCandidate],
) -> dict[int, tuple[FutureProofOption, ...]]:
    """미래 전략의 stated_plan 산문만 선택지를 만든다. 제목 메타는 구절로 쓰지 않는다."""
    request_sha = _sha(_json({'nonce': uuid4().hex, 'rows': [
        {'number': n, 'section': row.section_id, 'slot': row.claim_slot, 'text': row.text,
         'fragments': [_fragment_snapshot(f) for f in row.fragments]}
        for n, row in sorted(candidates.items())]}))
    result = {}
    for number, row in candidates.items():
        if (number != row.number or row.section_id != c.FUTURE_SELECTION_SECTION
                or row.claim_slot != STATED_PLAN_SLOT or not (
                    FUTURE_SECTION_FORWARD_RE.search(row.text)
                    or c.REPORTED_FUTURE_DISCOVERY_RE.search(row.text))):
            continue
        own = {f.fragment_id: f.text for f in row.fragments}
        if len(own) != len(row.fragments):
            continue
        if section_investment_plan_problem(row.text, own, {
            f.fragment_id: f.section_context_json for f in row.fragments if f.section_context_json
        }) or any(f.source_context_json and source_actor_problem(
            row.text, f.source_context_json, own,
        ) for f in row.fragments):
            continue
        fingerprints = tuple(sorted((fid, _sha(text)) for fid, text in own.items()))
        options, checked = [], 0
        for fragment in row.fragments:
            if len(fragment.text) > c.MAX_SOURCE_CHARS:
                continue
            for raw_quote in _sentences(fragment.text):
                quote = raw_quote.strip()
                if (not quote or len(quote) > c.MAX_SENTENCE_CHARS
                        or not (FUTURE_SECTION_FORWARD_RE.search(quote)
                                or c.REPORTED_FUTURE_DISCOVERY_RE.search(quote))
                        or SOURCE_NEGATION_RE.search(quote)):
                    continue
                start = fragment.text.find(quote)
                if start < 0 or fragment.text.find(quote, start + 1) >= 0:
                    continue
                activities = _shared_phrases(quote, row.text, verbal=True)
                for activity in activities:
                    spans = _bounded_spans(_normalized(row.text), _normalized(activity), verbal=True)
                    if not spans:
                        continue
                    # 대상은 이 활동 앞 구간에서만 찾는다. 전체 문장의 모든 부분구절을
                    # 동적 정규식으로 검사해 전역 패턴 캐시를 채우지 않는다.
                    targets = _shared_phrases(quote, _normalized(row.text)[:spans[0][0]], verbal=False)
                    for target in targets:
                        if _slot_problem(_normalized(target), _normalized(activity)):
                            continue
                        if checked >= c.MAX_PROOF_CHECKS:
                            break
                        checked += 1
                        for mode in ('계획', '전망'):
                            proof = {'근거': fragment.fragment_id, '대상': target,
                                     '활동': activity, '원문': quote, '양태': mode}
                            if future_plan_prose_problem(row.text, own, {FUTURE_KEY: [proof]},
                                                         claim_slot=row.claim_slot):
                                continue
                            binding = {'fragment': _fragment_snapshot(fragment),
                                       'sources': {f.fragment_id: _fragment_snapshot(f) for f in row.fragments},
                                       'quote_start': start, 'quote_end': start + len(quote),
                                       'quote_sha256': _sha(quote)}
                            options.append(FutureProofOption(number, row.section_id, row.claim_slot,
                                _sha(row.text), request_sha, fingerprints, _json(binding), _json(proof)))
                            break
                        if len(options) >= c.MAX_OPTIONS:
                            break
                    if len(options) >= c.MAX_OPTIONS or checked >= c.MAX_PROOF_CHECKS:
                        break
                if len(options) >= c.MAX_OPTIONS or checked >= c.MAX_PROOF_CHECKS:
                    break
            if len(options) >= c.MAX_OPTIONS or checked >= c.MAX_PROOF_CHECKS:
                break
        if options:
            result[number] = tuple(options)
    return result


def future_proof_option_hint(options: tuple[FutureProofOption, ...]) -> str:
    if not options:
        return ''
    return c.SELECTION_GUIDE + '  미래증명 선택지(JSON): ' + _json([
        {'ID': option.option_id, '번호': option.number, **json.loads(option.proof_json)}
        for option in options]) + '\n'


def restore_future_proof_selections(raw, options_by_number, candidates, sections, slots, fragments):
    """명시 ID만 새 검수 입력으로 복원한다. 기존 원응답·자유형 증명은 고치지 않는다."""
    from src.features.composer.logic import extract_json_payload
    payload = extract_json_payload(raw or '')
    if not isinstance(payload, Mapping) or not isinstance(payload.get(REVIEW_ENTRIES_KEY), list):
        return raw, frozenset()
    rows = payload[REVIEW_ENTRIES_KEY]
    if not any(isinstance(row, Mapping) and isinstance(row.get(GROUNDING_KEY), Mapping)
               and c.FUTURE_SELECTION_KEY in row[GROUNDING_KEY] for row in rows):
        return raw, frozenset()
    numbers = [coerce_verdict_number(row.get('번호')) for row in rows if isinstance(row, Mapping)]
    duplicates = {number for number in numbers if numbers.count(number) > 1}
    output, failures, seen = [], set(), set()
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get(GROUNDING_KEY), Mapping):
            output.append(row)
            continue
        evidence = row[GROUNDING_KEY]
        if c.FUTURE_SELECTION_KEY not in evidence:
            output.append(row)
            continue
        number = coerce_verdict_number(row.get('번호'))
        selection = evidence[c.FUTURE_SELECTION_KEY]
        expanded = dict(evidence)
        expanded.pop(c.FUTURE_SELECTION_KEY)
        output.append({**row, GROUNDING_KEY: expanded})
        option = next((opt for opt in options_by_number.get(number, ())
                       if isinstance(selection, str) and opt.option_id == selection), None)
        actual = candidates.get(number)
        valid = bool(option and actual and number not in seen and number not in duplicates
                     and FUTURE_KEY not in evidence)
        seen.add(number)
        if valid:
            text, own = actual
            # 평문 검수의 보조 실적표는 자기 인용 조각이 아니다. 수치 검사는 원래 입력을 쓴다.
            own = {fid: value for fid, value in own.items() if fid != TABLE_SOURCE_ID}
            valid = (option.number == number and option.section_id == sections.get(number)
                     and option.claim_slot == slots.get(number) == STATED_PLAN_SLOT
                     and option.candidate_sha256 == _sha(text)
                     and option.source_fingerprints == tuple(sorted((fid, _sha(value))
                                                                   for fid, value in own.items())))
        if valid:
            proof = json.loads(option.proof_json)
            binding = json.loads(option.binding_json)
            source = own.get(proof['근거'], '')
            fragment = fragments.get(proof['근거'])
            valid = (fragment is not None
                     and _json(binding['fragment']) == _json(_fragment_snapshot(fragment))
                     and proof['근거'] != TABLE_SOURCE_ID
                     and set(own).issubset(fragments)
                     and _json(binding['sources']) == _json({fid: _fragment_snapshot(fragments[fid])
                                                            for fid in own})
                     and source[binding['quote_start']:binding['quote_end']] == proof['원문']
                     and _sha(proof['원문']) == binding['quote_sha256']
                     and not future_plan_prose_problem(actual[0], own, {FUTURE_KEY: [proof]},
                                                       claim_slot=option.claim_slot))
        if valid:
            expanded[FUTURE_KEY] = [proof]
        else:
            failures.add(number)
    return _json({**payload, REVIEW_ENTRIES_KEY: output}), frozenset(failures)
