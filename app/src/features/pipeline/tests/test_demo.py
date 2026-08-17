"""데모 알맹이(저장된 기록 재생) 시험.

여기서 재는 것은 「기록을 화면이 쓸 수 있는 모양으로 옮기는 부분」이다.
실제 조사 품질은 재지 않는다 — 그건 1판 파일럿에서 이미 측정했다.
"""

from __future__ import annotations

import pytest

from src.core.constants import REPORT_CELL_ORDER
from src.features.pipeline.demo import (
    DemoPipeline,
    _clean,
    _in_report_order,
    _normalize,
    _outcome_of,
    _region_matches,
    available_companies,
)
from src.features.pipeline.port import Outcome, ReportSection, UserInput

# ══════════════════════════════════════════════════════════
# 글자 다듬기
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "raw, want",
    [
        ("강원도 강릉시 과학단지로 77-19 &nbsp;", "강원도 강릉시 과학단지로 77-19"),
        ("서울시\xa0중구", "서울시 중구"),
        ("&amp; 앰퍼샌드", "& 앰퍼샌드"),
        ("  앞뒤   공백  ", "앞뒤 공백"),
        (None, ""),
        ("", ""),
    ],
)
def test_화면에_HTML_기호가_새어나가지_않는다(raw, want):
    """파일럿이 웹에서 긁어온 값이라 `&nbsp;` 같은 기호가 섞여 있다 (문제로그 P-27)."""
    assert _clean(raw) == want


# ══════════════════════════════════════════════════════════
# 지역 대조
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "typed, address, want",
    [
        # 접미사만 다른 같은 곳 — 경고가 뜨면 안 된다 (문제로그 P-26)
        ("강원 강릉시", "강원도 강릉시 과학단지로 77-19", True),
        ("서울 성동구", "서울특별시 성동구 아차산로 84", True),
        ("인천 부평구", "인천광역시 부평구 갈산동", True),
        ("경기 성남시", "경기도 성남시 분당구 판교역로", True),
        # 진짜 다른 곳 — 경고가 떠야 한다
        ("서울 강남구", "경기도 성남시 분당구", False),
        ("부산", "서울특별시 강남구", False),
        # 두 글자 이름에서 접미사를 떼면 안 된다 (「대구」→「대」 방지)
        ("대구", "대구광역시 달서구", True),
        ("대구", "서울특별시 강남구", False),
        # 지역을 안 넣었으면 따지지 않는다
        ("", "서울특별시 강남구", True),
        ("   ", "서울특별시 강남구", True),
    ],
)
def test_지역_대조(typed, address, want):
    assert _region_matches(typed, address) is want


def test_구가_여럿이면_전부_맞아야_한다():
    assert _region_matches("서울 강남구", "서울특별시 서초구") is False


# ══════════════════════════════════════════════════════════
# 회사 이름 정규화
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "a, b",
    [
        ("주식회사 로보스타", "(주)로보스타"),
        ("㈜카카오페이", "카카오페이"),
        ("엠 투 아이", "엠투아이"),
        ("A-B-C", "ABC"),
    ],
)
def test_같은_회사는_같은_열쇠가_된다(a, b):
    assert _normalize(a) == _normalize(b)


def test_다른_회사는_다른_열쇠가_된다():
    assert _normalize("카카오") != _normalize("카카오페이")


# ══════════════════════════════════════════════════════════
# 종료 코드 옮기기
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "raw, want",
    [
        # ★ 왼쪽은 runs.jsonl에 실제로 찍힌 값이다 (문제로그 P-25)
        ("완료_성립미달", Outcome.REPORT),
        ("중단_식별실패", Outcome.NOT_FOUND),
        ("중단_게이트미달", Outcome.GATE_STOPPED),
        ("거부_거부B_외감아님", Outcome.REJECT_NO_DISCLOSURE),
        ("폐기_공고아님_2층", Outcome.POSTING_DISCARDED),
    ],
)
def test_파일럿_종료코드가_화면_종류로_옮겨진다(raw, want):
    assert _outcome_of(raw) is want


def test_모르는_코드는_오류로_본다():
    """조용히 「보고서 나옴」으로 흘려보내지 않는다."""
    assert _outcome_of("듣도보도못한코드") is Outcome.FAILED


# ══════════════════════════════════════════════════════════
# 항목 순서
# ══════════════════════════════════════════════════════════


def section(cell: str) -> ReportSection:
    return ReportSection(cell=cell, title=cell, lines=[], empty_reason="")


def test_뒤죽박죽이어도_정본_순서로_맞춘다():
    """저장된 md에서 5번이 9번 뒤에 붙어 있었다 (문제로그 P-28)."""
    got = _in_report_order([section(c) for c in ["1", "9", "5", "4-1", "3"]])
    assert [s.cell for s in got] == ["1", "3", "4-1", "5", "9"]


def test_정본에_없는_번호는_버리지_않고_뒤에_붙인다():
    got = _in_report_order([section(c) for c in ["99", "1", "88"]])
    assert [s.cell for s in got] == ["1", "99", "88"]


def test_정본_순서_자체가_이미_정렬돼_있다():
    got = _in_report_order([section(c) for c in REPORT_CELL_ORDER])
    assert [s.cell for s in got] == list(REPORT_CELL_ORDER)


# ══════════════════════════════════════════════════════════
# 데모 목록 · 재생
# ══════════════════════════════════════════════════════════


def test_데모_목록에_같은_회사가_두_번_나오지_않는다():
    names = [c["company"] for c in available_companies()]
    assert len(names) == len(set(names))


def test_데모_목록은_보고서_나오는_회사를_앞에_둔다():
    items = available_companies()
    if not items:
        pytest.skip("데모 데이터 없음")
    flags = [bool(c["is_report"]) for c in items]
    assert flags == sorted(flags, reverse=True)


def test_확인_카드까지_못_간_기록은_회사를_못_찾은_것으로_본다():
    """식별에서 끝난 기록에 억지로 카드를 만들지 않는다."""
    pipe = DemoPipeline()
    targets = [c for c in available_companies() if c["outcome"] == Outcome.NOT_FOUND.value]
    if not targets:
        pytest.skip("못 찾음 기록 없음")
    ui = UserInput(company=targets[0]["company"], job="", region="", posting_text="")
    assert pipe.find_company(ui) is None


def test_없는_회사는_카드가_안_나온다():
    pipe = DemoPipeline()
    ui = UserInput(company="존재하지않는회사zzz", job="", region="", posting_text="")
    assert pipe.find_company(ui) is None


def test_보고서가_나오는_회사는_끝까지_돈다():
    pipe = DemoPipeline()
    targets = [c for c in available_companies() if c["is_report"]]
    if not targets:
        pytest.skip("데모 데이터 없음")
    target = targets[0]
    ui = UserInput(company=target["company"], job=target["job"], region="", posting_text="")
    card = pipe.find_company(ui)
    assert card is not None
    result = pipe.run(ui, card)
    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.report.sections
