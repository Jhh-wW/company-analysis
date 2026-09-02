"""W1~W4 테스트 — W3 예시 3종을 그대로 시험지로 쓴다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from features.draft_check.logic import DraftItem, check_draft, sentence_in_source

원문 = "당사는 반도체 제조용 장비의 제조 및 판매를 영위하고 있습니다. 매출액은 1조 2,431억원입니다."
FRAGMENTS = {2: 원문}
요구역량 = ["SQL 활용 능력", "원가 분석 경험"]


def test_어미만_다르면_통과():
    assert sentence_in_source("반도체 제조용 장비를 제조한다", 원문) is True


def test_숫자를_바꾸면_걸린다():
    assert sentence_in_source("매출액은 1조 2,000억원입니다", 원문) is False


def test_원문에_없는_문장은_걸린다():
    assert sentence_in_source("향후 성장이 기대됩니다", 원문) is False


def test_W1_번호_없는_문장_삭제():
    r = check_draft([DraftItem("아무 문장", None, "1")], FRAGMENTS, 요구역량)
    assert r.deleted and "W1" in r.deleted[0][1]


def test_W2_없는_번호_삭제():
    r = check_draft([DraftItem("반도체 장비를 제조한다", 99, "1")], FRAGMENTS, 요구역량)
    assert r.deleted and "W2" in r.deleted[0][1]


def test_W3_지어낸_문장_삭제_정상문장_유지():
    items = [
        DraftItem("반도체 제조용 장비를 제조한다", 2, "1"),
        DraftItem("글로벌 1위 기업으로 도약하고 있다", 2, "1"),
    ]
    r = check_draft(items, FRAGMENTS, 요구역량)
    assert len(r.kept) == 1 and len(r.deleted) == 1 and "W3" in r.deleted[0][1]


def test_W4_공고_블록은_요구역량_목록만_허용():
    items = [
        DraftItem("SQL 활용 능력", 2, "5"),          # 목록 그대로 → 유지
        DraftItem("SQL을 잘 다루는 능력", 2, "5"),    # 다듬음 → W4 되돌림
    ]
    r = check_draft(items, FRAGMENTS, 요구역량)
    assert len(r.kept) == 1 and "W4" in r.deleted[0][1]
