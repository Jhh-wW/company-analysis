"""공개 표시 꼬리와 안내문이 저장 보고서의 결속·본문 수를 왜곡하지 않는다."""

import json

import pytest

from tools.review_public_output import inspect, plain
from tools.report_metrics import measure
from tools.report_review_text import LEGACY_MOVED_NOTICE, SECTION_NOTICES, is_section_notice
from src.features.composer.dedupe_notice_constants import (
    NOTICE_MOVED_MATERIALS_SUFFIX_TEMPLATE, NOTICE_MOVED_TARGETS_TEMPLATE,
    NOTICE_ONLY_MATERIALS_TEMPLATE,
)


_MOVED_TARGETS = "4장 «주요 변화와 실적»·7장 «사업 운영과 파트너 구조»"
_MOVED_NOTICE = NOTICE_MOVED_TARGETS_TEMPLATE.format(targets=_MOVED_TARGETS)


def report(lines, owned=True):
    return {"company": "시험회사", "generated_at": "2026-09-23", "sections": [
        {"cell": "current_challenges", "title": "과제", "fact_ids": ["fact"] if owned else [],
         "prose_lines": [[text, ""] for text in lines], "empty_reason": ""}],
        "fact_records": [{"fact_id": "fact", "claim": "회사는 서비스 운영에 집중한다.", "verification_status": "verified"}]}


def test_verified_interpretation_display_matches_its_owned_claim():
    result = inspect(report(["회사는 서비스 운영에 집중한다. [37] — 해석"]))
    assert result["sections"][0]["lines"][0]["bound_to_fact"] is True


def test_true_unbound_interpretation_and_internal_words_are_not_repaired():
    assert inspect(report(["회사는 서비스 운영에 집중한다. — 해석"], owned=False))["sections"][0]["lines"][0]["bound_to_fact"] is False
    assert plain("회사는 — 해석 지침을 마련했다.") == "회사는 — 해석 지침을 마련했다."


@pytest.mark.parametrize("notice", sorted(SECTION_NOTICES))
def test_known_notice_variants_require_whole_line_match(notice):
    assert is_section_notice(notice)
    assert is_section_notice("  " + notice.replace(" ", "  ") + "  ")
    assert not is_section_notice(notice + " 회사는 새 계약을 공시했다.")


def test_metrics_excludes_both_notices_but_preserves_unbound_real_prose(tmp_path):
    lines = ["회사는 서비스 운영에 집중한다. [37] — 해석",
             LEGACY_MOVED_NOTICE + " 아래 표는 이 장에 그대로 남아 있습니다.",
             "확인된 자료가 부족해 이 장은 비어 있습니다.",
             "회사는 유형자산을 정액법으로 상각한다."]
    report_path, diagnostics_path = tmp_path / "report.json", tmp_path / "diagnostics.json"
    report_path.write_text(json.dumps(report(lines), ensure_ascii=False), encoding="utf-8")
    diagnostics_path.write_text("[]", encoding="utf-8")
    result = measure(report_path, diagnostics_path)
    assert result["본문줄합계"] == 2
    assert result["장별"][0]["안내포함줄"] == 4


@pytest.mark.parametrize("materials", ("표", "도식", "보도 목록", "표·도식", "표·보도 목록", "도식·보도 목록", "표·도식·보도 목록"))
def test_새_안내문의_자료종류와_이동대상_정본을_전체문장으로_인식한다(materials):
    notices = (
        _MOVED_NOTICE,
        _MOVED_NOTICE + NOTICE_MOVED_MATERIALS_SUFFIX_TEMPLATE.format(materials=materials),
        NOTICE_ONLY_MATERIALS_TEMPLATE.format(materials=materials),
    )
    for notice in notices:
        assert is_section_notice(notice)
        assert inspect(report([notice]))["sections"][0]["lines"][0]["notice"] is True
        assert not is_section_notice(notice + " 회사는 새 계약을 공시했다.")


@pytest.mark.parametrize("text", (
    "이 장에 담겼던 내용은 회사의 사업 전략을 설명한다.",
    "확인된 문장이 부족해 이 장에는 자료를 더 모아야 한다.",
    NOTICE_MOVED_TARGETS_TEMPLATE.format(targets="4장 «다른 제목»"),
    NOTICE_MOVED_TARGETS_TEMPLATE.format(targets="7장 «사업 운영과 파트너 구조»·4장 «주요 변화와 실적»"),
    NOTICE_ONLY_MATERIALS_TEMPLATE.format(materials="도식·도식"),
    NOTICE_ONLY_MATERIALS_TEMPLATE.format(materials="직원 현황"),
))
def test_새_안내_접두만_같은_본문과_알수없는_대상을_숨기지_않는다(text):
    assert not is_section_notice(text)


def test_새_안내문이_옛본문필드에_있어도_본문수에_넣지_않는다(tmp_path):
    notice = NOTICE_ONLY_MATERIALS_TEMPLATE.format(materials="도식·보도 목록")
    lines = [_MOVED_NOTICE, notice, "회사는 서비스 운영에 집중한다."]
    saved = report(lines)
    saved["sections"][0]["guidance_lines"] = [_MOVED_NOTICE, notice]
    report_path, diagnostics_path = tmp_path / "report.json", tmp_path / "diagnostics.json"
    report_path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    diagnostics_path.write_text("[]", encoding="utf-8")
    result = measure(report_path, diagnostics_path)
    assert result["본문줄합계"] == 1
    assert result["장별"][0]["안내포함줄"] == 3
    assert result["장별"][0]["별도안내줄"] == 2
