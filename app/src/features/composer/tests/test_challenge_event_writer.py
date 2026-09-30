"""작성 안내만 행별로 펼치고 기존 원문·검수·출력 계약을 보존한다."""
from dataclasses import asdict
import hashlib
import json
from unittest.mock import patch

import pytest

from src.features.composer.challenge_event_scope import _event_rows, challenge_event_scope_problem
from src.features.composer import challenge_event_writer_constants as c
from src.features.composer.challenge_event_writer import render_challenge_event_writer_hints
from src.features.composer.evidence_pair_selection import build_evidence_pair_map
from src.features.composer.logic import build_section_prompt
from src.features.composer.port import CollectedFragment

HEADER = '제재조치일 | 조치대상자 | 처벌 또는 조치내용 | 이행 및 재발방지대책'
TABLE = HEADER + '; 2024.04.10 | 가온제조㈜ | 안전관리 위반 과태료 | 납부 완료, 안전센서 설치 완료'
SLOTS = ('current_challenges:issue', 'current_challenges:response')

def fragment(fid='1', text=TABLE, slots=SLOTS):
    return CollectedFragment(fragment_id=fid, text=text, kind='공시', supported_claim_slots=slots)

def hint_rows(text):
    return [json.loads(line) for line in text.splitlines() if line.startswith('{')]

def render(frags, **kwargs):
    return render_challenge_event_writer_hints(frags, build_evidence_pair_map('current_challenges',frags), **kwargs)

def test_row_fields_stay_in_exact_raw_span_and_fragment_is_unchanged():
    f = fragment()
    before = asdict(f)
    row = hint_rows(render([f]))[0]
    left,right = row['조각내행범위']
    assert f.text[left:right] == row['행원문']
    assert hashlib.sha256(row['행원문'].encode()).hexdigest() == row['행원문SHA256']
    assert row['주체'] == '가온제조㈜' and row['원문날짜'] == '2024.04.10'
    assert row['문장시작보기'] == '가온제조㈜는 2024.04.10…'
    assert row['사건'] == '안전관리 위반 과태료' and row['조치와상태원문'] == '납부 완료, 안전센서 설치 완료'
    assert asdict(f) == before

@pytest.mark.parametrize('body', [
    TABLE.replace('가온제조㈜', '-'), TABLE.replace('가온제조㈜', '미상'), TABLE.replace('2024.04.10','2024.02.30'),
    HEADER + '; 2024.04.10 | 가온제조㈜ | 안전관리 위반 과태료',
    '2024.04.10 | 가온제조㈜ | 안전관리 위반 과태료 | 납부 완료',
    '제재조치일 | 조치대상자 | 조치대상자 | 처벌 또는 조치내용 | 대책; 2024.04.10 | 가온제조㈜ | 다른회사 | 과태료 | 납부 완료',
])
def test_unknown_actor_invalid_date_incomplete_or_ambiguous_header_has_no_hint(body):
    assert render([fragment(text=body)]) == ''

def test_allowed_fragments_and_original_pairs_only():
    a,b = fragment(),fragment('2',TABLE.replace('가온제조㈜','나래제조㈜'))
    rows = hint_rows(render([a,b],allowed_fragment_ids=frozenset({'2'})))
    assert {r['조각번호'] for r in rows} == {'2'}
    pairs=build_evidence_pair_map('current_challenges',[a,b])
    assert all(pairs[p['id']][1] == '2' for r in rows for p in r['선택가능지원쌍'])
    assert render([fragment(slots=('current_challenges:next_check',))]) == ''

def test_sources_get_round_robin_chance_and_omission_is_explicit():
    many = HEADER + '; ' + '; '.join(f'2024.04.{i:02} | 가온제조㈜ | 과태료 | 납부 완료' for i in range(1,10))
    with patch.object(c,'EVENT_WRITER_MAX_ROWS',2):
        result=render([fragment(text=many),fragment('2')])
    assert [r['조각번호'] for r in hint_rows(result)] == ['1','2']
    assert c.EVENT_WRITER_OMISSION in result and len(result) <= c.EVENT_WRITER_MAX_CHARS

def test_exact_row_limit_does_not_claim_an_omitted_row():
    with patch.object(c,'EVENT_WRITER_MAX_ROWS',2):
        result=render([fragment(),fragment('2')])
    assert len(hint_rows(result)) == 2 and c.EVENT_WRITER_OMISSION not in result

def test_character_budget_keeps_whole_rows_and_never_truncates_json():
    with patch.object(c,'EVENT_WRITER_MAX_CHARS',1200):
        result=render([fragment(),fragment('2')])
    assert len(result) <= 1200
    assert hint_rows(result)
    assert c.EVENT_WRITER_OMISSION in result

def test_repeated_headers_reset_mapping_and_accident_numeric_cells_stay_exact():
    accident = ('재해발생회사 | 중대재해발생일자 | 중대재해 내용 | 중대재해 내용 | 조치 및 전망; '
                '도급사 가온설비 | 2024.05.10 | 1 | 조립 중 추락사고 | 안전설비 설치 예정')
    rows=hint_rows(render([fragment(text=TABLE+'; '+accident)]))
    assert len(rows)==2 and rows[1]['사건']=='1 | 조립 중 추락사고'
    assert rows[1]['원문단독숫자셀']==['1'] and '1명' not in rows[1]['행원문']
    assert rows[1]['조치와상태원문']=='안전설비 설치 예정'

def test_prompt_only_full_chapter_five_and_cache_schema_survive():
    f=fragment()
    full=build_section_prompt('합성회사','current_challenges',[f],None,show_supported_claim_slots=True,shared_evidence_prefix=True)
    assert c.EVENT_WRITER_HEADER in full and full.response_schema
    assert c.EVENT_WRITER_HEADER not in full[:full.cache_prefix_chars]
    assert c.EVENT_WRITER_HEADER not in build_section_prompt('합성회사','current_challenges',[f],None)
    assert c.EVENT_WRITER_HEADER not in build_section_prompt('합성회사','future_strategy',[f],None,show_supported_claim_slots=True)

def test_additive_row_metadata_does_not_relax_event_verification():
    rows=_event_rows(TABLE)
    assert rows[0].date=='2024.04.10' and rows[0].actor=='가온제조㈜'
    assert challenge_event_scope_problem('2024.04.10 가온제조㈜의 과태료 납부가 현재 진행 중이다.',{'1':TABLE})=='time_invalid'
