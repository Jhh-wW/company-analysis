"""같은 사건 행의 금액 증명을 선택 ID로 전달하고 별도 검수 입력에 복원한다."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from uuid import uuid4

from src.features.composer import numeric_proof_selection_constants as c
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_event_scope import _event_rows, _days, _surface
from src.features.composer.challenge_event_constants import UNKNOWN_EVENT_ACTORS
from src.features.composer.challenge_event_writer_constants import EVENT_WRITER_UNKNOWN_ACTORS
from src.features.composer.grounding import _numeric_valid
from src.features.composer.grounding_constants import GROUNDING_KEY, NUMERIC_KEY, REVIEW_ENTRIES_KEY
from src.features.composer.port import CollectedFragment
from src.features.composer.source_actor_scope import source_actor_problem
from src.features.composer.verdict_number import coerce_verdict_number


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class NumericProofCandidate:
    number: int
    section_id: str
    text: str
    fragments: tuple[CollectedFragment, ...]


@dataclass(frozen=True)
class NumericProofOption:
    number: int
    section_id: str
    candidate_sha256: str
    request_sha256: str
    source_fingerprints: tuple[tuple[str, str], ...]
    binding_json: str
    proof_json: str

    @property
    def option_id(self) -> str:
        return 'np1_' + _sha(_json({
            'version': c.NUMERIC_SELECTION_VERSION, 'number': self.number,
            'section': self.section_id, 'candidate': self.candidate_sha256,
            'request': self.request_sha256, 'sources': self.source_fingerprints,
            'binding': self.binding_json, 'proof': self.proof_json,
        }))


def _fragment_snapshot(fragment: CollectedFragment) -> dict[str, str]:
    snapshot = {key: getattr(fragment, key) for key in (
        'fragment_id', 'kind', 'text', 'source_url', 'location', 'document_identity',
        'document_content_sha256', 'formal_source_kind', 'source_document_id',
        'identity_binding', 'source_context_json',
    )}
    if fragment.section_context_json:
        snapshot['section_context_json'] = fragment.section_context_json
    return snapshot


def _eligible(fragment: CollectedFragment) -> bool:
    return (fragment.formal_source_kind in c.NUMERIC_SELECTION_DART_KINDS
            and bool(fragment.document_identity and fragment.identity_binding)
            and bool(c.SHA256_RE.fullmatch(fragment.document_content_sha256)))


def prepare_numeric_proof_options(
    candidates: Mapping[int, NumericProofCandidate],
) -> dict[int, tuple[NumericProofOption, ...]]:
    """검수 요청마다 새 지문을 만든다. 모호한 행은 선택지를 만들지 않는다."""
    snapshot = [{'number': number, 'section': item.section_id, 'text': item.text,
                 'fragments': [_fragment_snapshot(fragment) for fragment in item.fragments]}
                for number, item in sorted(candidates.items())]
    request_sha = _sha(_json({'nonce': uuid4().hex, 'candidates': snapshot}))
    result = {}
    for number, item in candidates.items():
        if item.section_id != c.NUMERIC_SELECTION_SECTION or item.number != number:
            continue
        candidate_amounts = tuple(c.CURRENCY_VALUE_RE.finditer(item.text))
        candidate_metrics = tuple(c.PENALTY_CURRENCY_RE.finditer(item.text))
        if (len(candidate_amounts) != 1 or len(candidate_metrics) != 1
                or c.UNSUPPORTED_PENALTY_RE.search(item.text)):
            continue
        match = candidate_metrics[0]
        metric, value = match.group('metric', 'value')
        own = {fragment.fragment_id: fragment.text for fragment in item.fragments}
        # 과거 사건의 정확 수치도 선택할 수 있다. 당면 과제 적격은 최종 5장 검사에서 별도로 닫는다.
        if len(own) != len(item.fragments) or challenge_business_problem(item.text, own, require_current=False):
            continue
        if any(fragment.source_context_json and source_actor_problem(
            item.text, fragment.source_context_json, own,
        ) for fragment in item.fragments):
            continue
        fingerprints = tuple(sorted((fid, _sha(text)) for fid, text in own.items()))
        options = []
        for fragment in item.fragments:
            if not _eligible(fragment):
                continue
            for row in _event_rows(fragment.text):
                days = _days(row.date)
                actor = _surface(row.actor).casefold()
                unknown_actors = {_surface(value).casefold() for value in UNKNOWN_EVENT_ACTORS | EVENT_WRITER_UNKNOWN_ACTORS}
                if (row.category != 'sanction' or not row.columns_unambiguous or not row.event
                        or actor in unknown_actors or actor.isdigit()
                        or row.actor not in item.text or row.date not in item.text
                        or len(days) != 1 or len(_days(item.text)) != 1
                        or fragment.text[row.start:row.end] != row.raw_row):
                    continue
                try:
                    date(*next(iter(days)))
                except ValueError:
                    continue
                row_metrics = tuple(c.PENALTY_CURRENCY_RE.finditer(row.raw_row))
                currency_cells = tuple(cell for cell in row.raw_row.split('|')
                                       if c.PENALTY_CURRENCY_RE.search(cell))
                if (len(row_metrics) != 1 or len(tuple(c.CURRENCY_VALUE_RE.finditer(row.raw_row))) != 1
                        or row_metrics[0].group('metric', 'value') != (metric, value)
                        or len(currency_cells) != 1
                        or c.UNSUPPORTED_PENALTY_RE.search(currency_cells[0])):
                    continue
                proof = {'표현': item.text[match.start():match.end()], '항목': metric, '근거': fragment.fragment_id,
                         '원문': row.raw_row, '원문항목': metric, '원문값': value, '후보값': value}
                # 모든 숫자의 차원·시점·상하한·포함 관계를 기존 검사기로 미리 검사한다.
                if not _numeric_valid(item.text, [proof], own):
                    continue
                binding = {**_fragment_snapshot(fragment), 'text': _sha(fragment.text),
                           'row_start': row.start, 'row_end': row.end, 'row_sha256': _sha(row.raw_row),
                           'actor': row.actor, 'date': row.date}
                options.append(NumericProofOption(number, item.section_id, _sha(item.text),
                    request_sha, fingerprints, _json(binding), _json(proof)))
        # 같은 주체·날짜라도 둘 이상의 행/출처가 맞으면 자동으로 하나를 고르지 않는다.
        if len(options) == 1:
            result[number] = tuple(options)
    return result


def numeric_proof_option_hint(options: tuple[NumericProofOption, ...]) -> str:
    if not options:
        return ''
    rows = []
    for option in options:
        proof, binding = json.loads(option.proof_json), json.loads(option.binding_json)
        rows.append({'ID': option.option_id, '번호': option.number, '근거': proof['근거'],
                     '항목': proof['항목'], '값': proof['후보값'],
                     '주체': binding['actor'], '원문날짜': binding['date']})
    return c.NUMERIC_SELECTION_GUIDE + '  수치선택 후보(JSON): ' + _json(rows) + '\n'


def restore_numeric_proof_selections(
    raw: str | None, options_by_number: Mapping[int, tuple[NumericProofOption, ...]],
    candidates: Mapping[int, tuple[str, Mapping[str, str]]],
    sections_by_number: Mapping[int, str],
) -> tuple[str | None, frozenset[int]]:
    """원응답을 바꾸지 않고 새 검수 입력을 만든다. 잘못된 선택은 후보를 닫는다."""
    from src.features.composer.logic import extract_json_payload
    payload = extract_json_payload(raw or '')
    if not isinstance(payload, Mapping) or not isinstance(payload.get(REVIEW_ENTRIES_KEY), list):
        return raw, frozenset()
    rows = payload[REVIEW_ENTRIES_KEY]
    if not any(isinstance(row, Mapping) and isinstance(row.get(GROUNDING_KEY), Mapping)
               and c.NUMERIC_SELECTION_KEY in row[GROUNDING_KEY] for row in rows):
        return raw, frozenset()
    output, failures = [], set()
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get(GROUNDING_KEY), Mapping):
            output.append(row)
            continue
        evidence = row[GROUNDING_KEY]
        if c.NUMERIC_SELECTION_KEY not in evidence:
            output.append(row)
            continue
        number = coerce_verdict_number(row.get('번호'))
        selection = evidence[c.NUMERIC_SELECTION_KEY]
        expanded = dict(evidence)
        expanded.pop(c.NUMERIC_SELECTION_KEY)
        copied = {**row, GROUNDING_KEY: expanded}
        output.append(copied)
        if selection == '' and isinstance(selection, str) and NUMERIC_KEY not in evidence:
            continue
        option = next((option for option in options_by_number.get(number, ())
                       if isinstance(selection, str) and selection == option.option_id), None)
        actual = candidates.get(number)
        valid = bool(option and actual and NUMERIC_KEY not in evidence)
        if valid:
            text, own = actual
            valid = (option.number == number and option.section_id == sections_by_number.get(number)
                     and option.candidate_sha256 == _sha(text)
                     and option.source_fingerprints == tuple(sorted((fid, _sha(value)) for fid, value in own.items())))
        if valid:
            proof = json.loads(option.proof_json)
            valid = _numeric_valid(actual[0], [proof], actual[1])
        if valid:
            expanded[NUMERIC_KEY] = [proof]
        else:
            # 해당 번호의 실제 판정은 호출자가 실패 목록과 함께 닫는다.
            expanded[NUMERIC_KEY] = [{}]
            if number in candidates:
                failures.add(number)
    return _json({**payload, REVIEW_ENTRIES_KEY: output}), frozenset(failures)
