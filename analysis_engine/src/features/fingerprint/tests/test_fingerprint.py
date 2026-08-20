from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from features.fingerprint.logic import posting_fingerprint

역량 = ["SQL 활용 능력", "원가 분석 경험", "영어 회화 가능자 우대"]


def test_순서가_달라도_같은_지문():
    assert posting_fingerprint(역량) == posting_fingerprint(list(reversed(역량)))


def test_공백과_글머리표가_달라도_같은_지문():
    변형 = ["· SQL  활용 능력", "원가분석 경험", "영어 회화 가능자 우대  "]
    # 공백 제거 정규화라 "원가 분석"과 "원가분석"도 같아진다
    assert posting_fingerprint(역량) == posting_fingerprint(변형)


def test_내용이_다르면_다른_지문():
    assert posting_fingerprint(역량) != posting_fingerprint(역량[:2] + ["파이썬 경험"])


def test_빈_줄은_무시():
    assert posting_fingerprint(역량 + ["", "  "]) == posting_fingerprint(역량)
