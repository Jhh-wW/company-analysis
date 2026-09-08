"""실측 공시 원문 → 3장 표까지의 무과금 재현 — 결정적 이름 표 편.

★ 왜 필요했나 (2026-09-06) — 안내문이 「카드 하나는 «반드시» 그 이름들을
  담는다」로 강제했는데도 운영 실행 2회에서 작가 AI가 부문 카드 두 개만 냈다.
  그래서 composer가 결정적 줄을 덧붙이게 했는데, 첫 설계는 그 줄을 «작가
  카드»로 만들어 첫 이름을 제목 칸에 올렸다. 카드 제목 칸은 «종류»를 적는
  자리라, 엔터사에서는 제목이 그룹 하나가 되고 제조사에서는 제품 하나가 됐다.
  지금은 종류 라벨을 「구분」 열에 둔 «표»를 붙인다. 그 표가 가공 픽스처가
  아니라 «실제 공시 원문»에서도 제대로 서는지를 여기서 본다.

★ AI·네트워크 0회. 원문 사본이 로컬에 없으면 그 시험만 건너뛴다.
★ 이름은 시험 코드에 «리터럴로 적지 않는다» — 원문에서 뽑은 값을 그대로
  쓴다. 실존 이름을 시험에 박으면 지워도 되돌아온다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.core import revenue_table_switch
from src.features.composer.constants import (
    PORTFOLIO_TABLE_CAPTION,
    PORTFOLIO_TABLE_HEADERS,
    PORTFOLIO_TABLE_SECTION_ID,
)
from src.features.composer.diagram_check import (
    FLOW_REVIEW_PROMPT_HEADER,
    FLOW_REVIEW_ROW_NUMBER_PATTERN,
)
from src.features.composer.pipeline import run_v2
from src.features.composer.port import filing_meta_from_raw, fragments_from_raw
from src.features.composer.portfolio_name_table import (
    NAME_TABLE_CAPTION_PREFIX,
    NAME_TABLE_HEADERS,
    NAME_TABLE_MAX_NAMES_PER_KIND,
    NAME_TABLE_NAME_SEPARATOR,
)
from src.features.composer.portfolio_names import (
    MIN_REPRESENTATIVE_NAMES_FOR_CARD,
    representative_name_sources,
)
from src.features.pipeline import real
from src.features.pipeline.tests.test_product_name_fragments import (
    CORP_ID,
    GENERATION_SHA256,
    _filing,
    _legacy_fragments,
    _typed_anchor,
)


#: 로컬에만 두는 실측 원문(공개 DART 사업보고서 사본). 없으면 건너뛴다.
#: 키는 회사명이 아니라 «업종 설명»이다 — 회사명 문자열을 코드에 안 남긴다.
_RUNS_ROOT = Path(
    "C:/Users/jh-wo/.claude/workspace/기업분석2/app/.local_evaluation_runs"
)
_RECEIPTS = {
    "상장 엔터사": "20260320000802",
    "대형 제조사": "20260310002820",
}


def _local_filing(receipt: str) -> Path | None:
    found = sorted(
        _RUNS_ROOT.glob(f"*/analysis_engine/pilot/raw_filings/{receipt}.xml")
    )
    return found[0] if found else None


@pytest.fixture
def 스위치_켬():
    """운영과 같은 새 안내문 경로로 고정한다(render.yaml: REVENUE_TABLE_V2=1)."""

    revenue_table_switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    revenue_table_switch.freeze_process_revenue_table_switch(
        revenue_table_switch.RevenueTableSwitch.ON
    )
    try:
        yield
    finally:
        revenue_table_switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


def _frags_from_real_filing(receipt: str) -> dict[int, dict[str, object]]:
    path = _local_filing(receipt)
    assert path is not None
    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        _legacy_fragments(),
        filing_text="",
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=[],
        raw_path=str(path),
    )
    assert added > 0, "실측 원문에서 이름 조각이 하나도 안 나왔습니다"
    return frags


class _Writer:
    """3장에만 카드를 내는 가짜 작가.

    ``names``가 비면 이름을 한 글자도 안 쓴 «부문 카드»만 낸다 — 2026-09-06
    운영 실측에서 실제 작가가 낸 모양이다.
    """

    def __init__(
        self, names: tuple[str, ...] = (), citations: tuple[str, ...] = ("1",)
    ) -> None:
        self._names = names
        self._citations = citations
        self.portfolio_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        if "핵심 요약" in prompt:
            return json.dumps(
                {
                    "문장들": [
                        {"글": f"요약 {mark} 문장이다.", "인용": ["1"], "등급": "확인"}
                        for mark in ("가", "나", "다")
                    ]
                },
                ensure_ascii=False,
            )
        payload: dict[str, object] = {
            "문장들": [
                {"글": "회사는 사업부문 하나를 운영한다.", "인용": ["1"], "등급": "확인"}
            ]
        }
        if PORTFOLIO_TABLE_HEADERS[0] in prompt:
            self.portfolio_prompts.append(prompt)
            cells = (
                [self._names[0], "·".join(self._names), "출시했다", "주력"]
                if self._names
                else ["사업부문 하나", "부문 설명", "부문을 운영한다", "주력"]
            )
            payload["경로표"] = [{"칸": cells, "인용": list(self._citations)}]
        return json.dumps(payload, ensure_ascii=False)


def _reviewer(prompt: str) -> str:
    if FLOW_REVIEW_PROMPT_HEADER in prompt:
        numbers = re.findall(
            FLOW_REVIEW_ROW_NUMBER_PATTERN, prompt, flags=re.MULTILINE
        )
    else:
        numbers = re.findall(r"\[(\d+)\] \(등급: [^,\n]+, 인용:", prompt)
    return json.dumps(
        {"판정": [{"번호": int(value), "결과": "참"} for value in numbers]},
        ensure_ascii=False,
    )


def _run(frags: dict[int, dict[str, object]], writer: _Writer):
    return run_v2(
        "가나다회사",
        frags,
        None,
        writer_ask=writer,
        reviewer_ask=_reviewer,
        corp_type="상장사",
        as_of_date="2026-09-06",
        filing_meta=filing_meta_from_raw(_filing()),
    )


def _portfolio_rows(report) -> list[list[str]]:
    section = next(
        section
        for section in report.sections
        if section.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    table = next(
        (
            table
            for table in section.tables
            if table.caption == PORTFOLIO_TABLE_CAPTION
        ),
        None,
    )
    return [] if table is None else [list(row) for row in table.rows]


def _name_table(report):
    section = next(
        section
        for section in report.sections
        if section.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    return next(
        (
            table
            for table in section.tables
            if table.caption.startswith(NAME_TABLE_CAPTION_PREFIX)
        ),
        None,
    )


# ══════════════════════════════════════════════════════════
# ① 실측 원문의 이름이 «종류 라벨 행»으로 선다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("업종", sorted(_RECEIPTS))
def test_실측_원문에서_이름_표의_구분칸은_종류_라벨이다(
    업종: str, 스위치_켬
) -> None:
    receipt = _RECEIPTS[업종]
    if _local_filing(receipt) is None:
        pytest.skip("로컬 공시 원문 사본이 없는 환경입니다")
    frags = _frags_from_real_filing(receipt)
    expected = representative_name_sources(fragments_from_raw(frags))
    assert len(expected) >= MIN_REPRESENTATIVE_NAMES_FOR_CARD
    실측라벨 = [source.label for source in expected]
    실측이름 = {source.name for source in expected}
    writer = _Writer()

    output = _run(frags, writer)

    assert writer.portfolio_prompts, "3장 프롬프트가 한 번도 안 왔다"
    assert output.portfolio_name_table_name_count > 0, (
        f"이름 표가 안 붙었습니다 — 사유: "
        f"{output.portfolio_name_table_blocked_reason!r}"
    )
    table = _name_table(output.report)
    assert table is not None
    assert table.headers == list(NAME_TABLE_HEADERS)
    # 이 작가는 공시된 실제 이름 없이 부문 설명만 카드로 냈다. 이름 검증 뒤
    # 빈 제품 카드를 남기지 않되, 원문에서 결정적으로 만든 이름 표는 보존한다.
    assert _portfolio_rows(output.report) == []

    for 구분, 이름칸 in table.rows:
        # ★ 이 단언이 이번 수정의 핵심이다 — 「구분」 칸은 실측 원문의 종류
        #   라벨이지 그 종류의 «보기» 하나가 아니다. 앞선 설계에서는 여기에
        #   첫 이름이 올라갔다.
        assert 구분 in 실측라벨, (구분, sorted(set(실측라벨)))
        assert 구분 not in 실측이름, 구분
        보인이름 = 이름칸.split(" 외 ")[0].split(NAME_TABLE_NAME_SEPARATOR)
        assert len(보인이름) <= NAME_TABLE_MAX_NAMES_PER_KIND
        # 실린 이름은 전부 실측 원문에서 온 것이다(지어낸 글자가 없다).
        assert all(name in 실측이름 for name in 보인이름), 이름칸
    # 부록에 실린 번호는 본문 어디선가 인용된 번호와 1:1이다(validate_v2 계약).
    assert output.report.citations


# ══════════════════════════════════════════════════════════
# ② 작가가 이미 이름을 써도 표는 그대로 선다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("업종", sorted(_RECEIPTS))
def test_실측_원문에서_작가가_이름을_써도_표는_남는다(
    업종: str, 스위치_켬
) -> None:
    """예측 가능성 — 같은 회사를 두 번 돌렸을 때 화면이 달라지면 안 된다.

    ★ 앞선 설계는 「작가가 안 썼을 때만」 붙였다. 그래서 작가의 그날 기분에
      따라 3장에 이름이 있기도 없기도 했다. 그 조건을 없앤 것을 여기서 잠근다.
    """

    receipt = _RECEIPTS[업종]
    if _local_filing(receipt) is None:
        pytest.skip("로컬 공시 원문 사본이 없는 환경입니다")
    frags = _frags_from_real_filing(receipt)
    expected = representative_name_sources(fragments_from_raw(frags))
    사용할이름 = tuple(source.name for source in expected[:2])
    인용 = tuple(source.fragment_id for source in expected[:2])
    writer = _Writer(names=사용할이름, citations=인용)

    output = _run(frags, writer)

    assert writer.portfolio_prompts
    # 작가가 썼으므로 미사용 표식은 꺼진다.
    assert output.unused_portfolio_name_count == 0
    # 그래도 이름 표는 «그대로» 붙는다.
    assert output.portfolio_name_table_name_count > 0, (
        f"작가가 썼다고 표가 사라졌습니다 — 사유: "
        f"{output.portfolio_name_table_blocked_reason!r}"
    )
    rows = _portfolio_rows(output.report)
    assert len(rows) == 1, rows
    assert rows[0][0] == 사용할이름[0]
    assert _name_table(output.report) is not None
