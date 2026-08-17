"""칸 판정 테스트 — 실측 사례(정원엔시스·외감 H·크로넥스)를 그대로 시험지로 쓴다."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from features.cell_check.logic import (
    count_filled,
    establishment,
    facility_ok,
    news_ok,
    section_is_empty,
)

TODAY = dt.date(2026, 8, 14)


def test_정원엔시스형_해당사항없음은_빈_섹션():
    text = "X. 대주주 등과의 거래내용\n가. 해당사항 없음\n나. 해당사항 없음\n다. 해당 없음\n라. 해당사항 없음"
    assert section_is_empty(text)


def test_실제_내용이_있으면_빈_섹션_아님():
    text = "특수관계자 거래: (주)한빛테크에 대한 매출 1,234,000천원이 있으며 " + "상세 내역은 다음과 같다. " * 5
    assert not section_is_empty(text)


def test_외감H형_토지건물만은_설비_X():
    assert not facility_ok("토지 5,000,000  건물 3,200,000")


def test_기계장치와_금액2개면_설비_O():
    assert facility_ok("기계장치 1,200,000  차량운반구 45,000")


def test_크로넥스형_종목나열_기사_차단():
    assert not news_ok("생물공학 약세… 에이비엘·펩트론·휴젤·크로넥스", "크로넥스",
                       dt.date(2026, 1, 5), TODAY, other_companies_in_title=3)


def test_뉴스_3년_경계():
    assert news_ok("한빛테크, 신공장 착공", "한빛테크", dt.date(2023, 9, 1), TODAY, 0)
    assert not news_ok("한빛테크, 신공장 착공", "한빛테크", dt.date(2023, 8, 1), TODAY, 0)


def test_제목에_회사명_없으면_버린다():
    assert not news_ok("업계 실적 개선 전망", "메드트로닉코리아", dt.date(2026, 5, 1), TODAY, 0)


def test_동명_2곳이면_고유_단서가_있어야_채택():
    # 나노솔루션형 — 동명 법인이 여럿이면 본문 단서 없이는 버린다 (조건 5 · 2026-08-14)
    args = ("나노솔루션, 신공장 착공", "나노솔루션", dt.date(2026, 3, 1), TODAY, 0)
    assert not news_ok(*args, homonym_count=2, identity_hint_found=False)
    assert news_ok(*args, homonym_count=2, identity_hint_found=True)


def test_동명_없으면_단서_없이도_기존_규칙대로():
    assert news_ok("한빛테크, 수출 계약", "한빛테크", dt.date(2026, 3, 1), TODAY, 0,
                   homonym_count=1, identity_hint_found=False)


def _cells(*filled: str) -> dict[str, bool]:
    base = {c: False for c in ("1", "2", "3", "4-1", "4-2", "4-3", "9", "附")}
    for c in filled:
        base[c] = True
    return base


def test_附은_세지_않는다():
    cells = _cells("1", "3", "9", "附")  # 실질 3칸 + 附
    assert count_filled(cells) == 3
    ok, reasons = establishment(cells)
    assert not ok and any("조건1" in r for r in reasons)


def test_상응무역형_3칸은_폐기():
    ok, reasons = establishment(_cells("1", "3", "9"))
    assert not ok and any("조건3" in r for r in reasons)


def test_4칸이어도_1번_비면_폐기():
    ok, reasons = establishment(_cells("2", "3", "4-1", "9"))
    assert not ok and reasons == ["조건2 미달: 1번(뭘 팔아 돈 버나) 비어 있음"]


def test_4칸_1번O_4번중1개면_성립():
    ok, reasons = establishment(_cells("1", "3", "4-2", "9"))
    assert ok and reasons == []
