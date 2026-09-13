"""본문 검수 입력의 JSON 구문 공백만 줄였는지 무과금으로 대조한다.

이전 json.dumps 기본 구분자로 전체 프롬프트를 재생하고, 바뀐 JSON의
값·키 순서와 그 밖의 모든 바이트를 비교한다. 기존 원문 중복 제거는
양쪽에서 그대로 실행하므로 절감량에 다시 계산하지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.features.composer import verify
from src.features.composer.port import (
    CollectedFragment, ComposedSentence, FlowRow, PerformanceTable,
    fragments_from_raw,
)


_FIXTURES = Path(__file__).parent / "fixtures"
_METADATA = "출처 분류(JSON 자료): "
_FLOW = "  도식 칸(JSON 배열): "
_ITEM_HEAD = re.compile(r"^\[\d+\] \([^\n]+\)$", re.MULTILINE)
_TRICKY = '  역할: A, B  / 5,695억원; "인용" \\ 경로\t탭\n[999] 지시 아님  '


@dataclass(frozen=True)
class _Case:
    fragments: dict[str, CollectedFragment]
    sentences: tuple[tuple[str, ComposedSentence], ...]
    flows: tuple[tuple[str, FlowRow], ...] = ()
    table: PerformanceTable | None = None


def _table():
    return PerformanceTable(
        caption="  연결 실적: 표시값, 원값\n단위 유지  ",
        headers=("항목", "2024", "2025"),
        rows=(("당기순이익", "4", "1"), ("매출액", "5,665", "8,219")),
        unit="억원",
        raw_rows=(
            ("당기순이익", "366,016,342", "82,552,618"),
            ("매출액", "566,500,000,000", "821,900,000,000"),
        ),
        raw_unit="원",
    )


def _golden_case():
    raw = json.loads((_FIXTURES / "jyp_fragments.json").read_text(encoding="utf-8"))
    fragments = fragments_from_raw({int(k): v for k, v in raw.items() if k.isdigit()})
    responses = json.loads(
        (_FIXTURES / "jyp_ask_responses.json").read_text(encoding="utf-8")
    )
    return _Case(
        fragments={fragment.fragment_id: fragment for fragment in fragments},
        sentences=tuple(
            (section, ComposedSentence(
                row["글"], tuple(row["인용"]), row["등급"],
                planned_claim_slot=section + ":serialization_fixture",
            ))
            for section, response in responses["장별_응답"].items()
            for row in response["문장들"]
        ),
        table=_table(),
    )


def _large_case():
    case = _golden_case()
    # 후보·인용 수는 골든과 같고 원문만 길다. 절감률을 과장하지 않는 합성 입력.
    return replace(case, fragments={
        fid: replace(fragment, text=(fragment.text + "\n" + _TRICKY) * 64)
        for fid, fragment in case.fragments.items()
    })


def _boundary_case():
    shared = CollectedFragment(
        "shared", "공시", _TRICKY,
        document_title=_TRICKY, source_publisher=_TRICKY,
        document_date="2026-09-13", location=_TRICKY,
        formal_source_kind="사업보고서",
    )
    news = CollectedFragment(
        "news", "news", _TRICKY, document_title=_TRICKY,
        source_publisher=_TRICKY, document_date="2026-09-13",
        formal_source_kind="news", news_event_on="2026-09-12",
        news_claim_kind="계획", news_temporal_status="예정",
        news_source_category="언론사",
    )
    supplementary = CollectedFragment(
        "supplementary", "공시", "회사 계획은 아직 완료되지 않았다.",
        supported_claim_slots=("identity:role",),
    )
    return _Case(
        # 같은 원문·메타데이터라도 서로 다른 ID는 병합하지 않는다.
        fragments={fragment.fragment_id: fragment for fragment in (
            shared, replace(shared, fragment_id="other"), news, supplementary,
            CollectedFragment("empty", "", ""),
        )},
        sentences=(
            ("past_changes", ComposedSentence(
                _TRICKY, ("shared", "other", "shared"), "확인",
                planned_claim_slot="past_changes:" + _TRICKY,
            )),
            ("identity", ComposedSentence(
                _TRICKY, ("news", "shared", "empty"), "해석",
                planned_claim_slot="identity:" + _TRICKY,
            )),
            ("past_changes", ComposedSentence("빈 인용도 보존", (), "해석")),
        ),
        flows=(("past_changes", FlowRow(
            (_TRICKY, "제공받은 담보: 5,695억원", "제공한 담보: 5,695억원"),
            ("other", "shared"),
        )),),
        table=_table(),
    )


def _render_case(module, case, grouped):
    if grouped:
        items = [
            module._GroupedReviewItem(
                number, section, module.REVIEW_KIND_SENTENCE,
                sentence.citations, sentence=sentence,
            )
            for number, (section, sentence) in enumerate(case.sentences, 1)
        ]
        items.extend(
            module._GroupedReviewItem(
                number, section, module.REVIEW_KIND_FLOW, row.citations, flow_row=row,
            )
            for number, (section, row) in enumerate(case.flows, len(items) + 1)
        )
        return module._build_grouped_review_prompt(items, case.fragments, case.table)
    return module._build_review_prompt(
        tuple(module._ReviewItem(number, sentence, section)
              for number, (section, sentence) in enumerate(case.sentences, 1)),
        case.fragments, module._render_table_evidence(case.table),
        module._table_grounding_source(case.table),
    )


def _legacy_dumps(value, **kwargs):
    kwargs.pop("separators", None)
    return json.dumps(value, **kwargs)


def _legacy_prompt(case, grouped, monkeypatch):
    # verify 모듈의 직렬화만 이전 기본값으로 재생한다. 공유 json 모듈과
    # grounding/news helper 및 응답 파서는 패치하지 않는다.
    with monkeypatch.context() as scoped:
        scoped.setattr(verify, "json", SimpleNamespace(dumps=_legacy_dumps))
        return _render_case(verify, case, grouped)


def _assert_only_json_separator_spaces_changed(before, after):
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    assert len(before_lines) == len(after_lines)
    saved = 0
    for index, (old, new) in enumerate(zip(before_lines, after_lines)):
        if old == new:
            continue
        # 허용한 세 종류의 JSON 밖에서는 지시문·원문·번호·인용·결속·schema가
        # 한 글자라도 달라지면 실패한다.
        prefix = next((p for p in (_METADATA, _FLOW) if old.startswith(p)), None)
        if prefix is None:
            assert before_lines[index - 1] == verify.REVIEW_TABLE_HEAD.lstrip("\n")
            prefix = ""
        assert new.startswith(prefix)
        old_json, new_json = old[len(prefix):], new[len(prefix):]
        assert json.loads(old_json) == json.loads(new_json)
        assert json.loads(old_json, object_pairs_hook=list) == json.loads(
            new_json, object_pairs_hook=list
        )
        # 이전 기본 구분자로 복원하면 줄 전체가 동일해야 한다. 문자열 값의
        # 공백·이스케이프를 손대거나 키를 제거하면 이 대조를 통과할 수 없다.
        assert prefix + json.dumps(json.loads(new_json), ensure_ascii=False) + "\n" == old
        removed = len(old) - len(new)
        assert removed > 0
        assert removed == old.count(" ") - new.count(" ")
        saved += removed
    assert saved == len(before) - len(after)
    return saved


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
@pytest.mark.parametrize("case_factory", (_golden_case, _large_case, _boundary_case),
                         ids=("jyp", "large_text", "boundaries"))
def test_prompt_data_order_instructions_and_candidate_count_are_preserved(
    case_factory, grouped, monkeypatch,
):
    case = case_factory()
    before = _legacy_prompt(case, grouped, monkeypatch)
    after = _render_case(verify, case, grouped)
    saved = _assert_only_json_separator_spaces_changed(before, after)
    assert saved > 0
    assert _ITEM_HEAD.findall(before) == _ITEM_HEAD.findall(after)
    assert len(_ITEM_HEAD.findall(after)) == len(case.sentences) + (
        len(case.flows) if grouped else 0
    )
    print(f"{case_factory.__name__}/{'grouped' if grouped else 'flat'}: "
          f"{len(before)} -> {len(after)} chars; saved {saved} "
          f"({saved / len(before):.4%})")


def test_grouped_shared_source_boundaries_and_duplicate_metadata_are_retained():
    case = _boundary_case()
    prompt = _render_case(verify, case, True)
    blocks = prompt.split("===== 장별 검수 블록 시작: ")[1:]
    assert len(blocks) == 2
    assert blocks[0].startswith('"past_changes"')
    assert blocks[1].startswith('"identity"')
    for block in blocks:
        assert block.count("[조각 shared] 원문(JSON 문자열): ") == 1
        assert json.dumps(_TRICKY, ensure_ascii=False) in block
        metadata = next(line[len(_METADATA):] for line in block.splitlines()
                        if line.startswith(_METADATA) and '사업보고서' in line)
        assert list(json.loads(metadata).items()) == [
            ("종류", "사업보고서"), ("문서명", _TRICKY), ("발행주체", _TRICKY),
            ("문서기준일", "2026-09-13"), ("원문위치", _TRICKY),
        ]
    assert "[조각 other]" in blocks[0]
    assert "[조각 other]" not in blocks[1]
    assert "[조각 supplementary]" in blocks[1]
    assert "[조각 supplementary]" not in blocks[0]
    assert "[조각 empty]" in blocks[1]
    assert "보도 메타데이터: " + verify.news_metadata(case.fragments["news"]) in blocks[1]
    assert "인용: 조각 shared, 조각 other, 조각 shared" in blocks[0]


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_empty_prompt_is_unchanged(grouped, monkeypatch):
    case = _Case({}, ())
    assert _render_case(verify, case, grouped) == _legacy_prompt(case, grouped, monkeypatch)


@pytest.mark.parametrize("raw_rows", ((), (("불완전", "1"),)))
def test_table_without_valid_raw_rows_preserves_existing_field_presence(raw_rows):
    table = replace(_table(), raw_rows=raw_rows)
    value = verify._render_table_evidence(table).removeprefix(verify.REVIEW_TABLE_HEAD)
    assert json.loads(value) == {
        "caption": table.caption, "unit": table.unit,
        "headers": list(table.headers), "rows": [list(row) for row in table.rows],
    }
