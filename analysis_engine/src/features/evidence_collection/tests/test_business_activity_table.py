"""영업표 추가 후보가 원문·기존 조각·장별 예산을 보존한다."""

from dataclasses import replace
import hashlib

import pytest

from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection import collect as collect_module
from features.evidence_collection.filing_select import RawFilingRow
from features.evidence_collection.segment import FragmentCandidate, usable_ranges_from_candidates
from features.evidence_collection.tests.test_collect import _fetcher, _NOW


TEXT = ("당기와 전기 중 당사가 시공한 주요 도급공사입니다.\n\n"
        "(3) 당기말 현재 종류별 실적입니다. ① 당기\n\n"
        "구분 | 도급금액 | 누적공사수익 ; 정밀설비공사 현장 | 100 | 60")


@pytest.mark.parametrize("actor", ("(주)가온기업", "(주)다른기업"))
def test_자기법인만_회사정의로_추가하고_원계약조각도_유지한다(actor, monkeypatch):
    text = "(주)가온기업\nII. 사업의 내용\n[설비사업부문]\n영업개황 " + actor + "\n\n" + TEXT
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315", "00126380")
    fetcher = _fetcher("A", row, text)
    fetcher.document_responses_by_rcept_no[row.rcept_no] = replace(
        fetcher.document_responses_by_rcept_no[row.rcept_no], corp_code="00126380", document_actor="(주)가온기업")
    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)
    extra = [f for f in harvest.fragments if "direct_pattern:current_company_activity_table" in f.reason_codes]
    assert bool(extra) == (actor == "(주)가온기업")
    for fragment in extra:
        assert fragment.covered_slot_ids == ("identity:business_definition",)
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
    assert len(fetcher.document_calls) == 1
    with monkeypatch.context() as scoped:
        scoped.setattr(collect_module, "activity_table_ranges", lambda *_args: ())
        before = collect_dart_evidence(fetcher, "00126380", now=_NOW)
    # 일반 분할기는 짧은 직전 표제를 표 앞에 함께 붙일 수 있다.
    # 문자열의 시작을 가정하지 않고 변경 전 생산 조각 전체를 그대로 대조한다.
    assert {(f.text, f.location, f.text_sha256) for f in before.fragments} <= {
        (f.text, f.location, f.text_sha256) for f in harvest.fragments}


@pytest.mark.parametrize("ranges,expected", (
    (((0, 4), (4, 8)), ((0, 8),)),
    (((0, 4), (0, 4)), ((0, 4),)),
    (((0, 8), (2, 4)), ((0, 8),)),
    (((8, 10), (2, 6), (4, 9)), ((2, 10),)),
    (((0, 4), (5, 8)), ((0, 4), (5, 8))),
))
def test_사용범위만_인접_중복_중첩_역순_정확합집합(ranges, expected):
    source = "0123456789"
    candidates = [FragmentCandidate(a, b, source[a:b], "") for a, b in ranges]
    original = tuple((f.start, f.end, f.text) for f in candidates)
    result = usable_ranges_from_candidates(candidates)
    assert tuple((r.start, r.end) for r in result) == expected
    assert tuple((f.start, f.end, f.text) for f in candidates) == original
    original_positions = {i for a, b in ranges for i in range(a, b)}
    assert {i for r in result for i in range(r.start, r.end)} == original_positions


def test_기존identity_보관몫이_차면_검색표가_표시근거를_밀어내지_않는다(monkeypatch):
    declarations = "\n\n".join(
        f"당사의 사업은 정밀부품{i} 사업과 교육 사업으로 구성되어 있습니다." for i in range(20))
    text = declarations + "\n\n" + TEXT
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315", "00126380")
    fetcher = _fetcher("A", row, text)
    fetcher.document_responses_by_rcept_no[row.rcept_no] = replace(
        fetcher.document_responses_by_rcept_no[row.rcept_no], corp_code="00126380", document_actor="(주)가온기업")
    after = collect_dart_evidence(fetcher, "00126380", now=_NOW)
    with monkeypatch.context() as scoped:
        scoped.setattr(collect_module, "activity_table_ranges", lambda *_args: ())
        before = collect_dart_evidence(fetcher, "00126380", now=_NOW)
    assert {(f.text, f.location, f.text_sha256) for f in before.fragments} <= {
        (f.text, f.location, f.text_sha256) for f in after.fragments}
