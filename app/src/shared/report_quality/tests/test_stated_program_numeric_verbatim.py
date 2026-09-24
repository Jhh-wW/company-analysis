"""자기 선언 프로그램의 FULL 숫자 예외. 모든 문장·출처는 익명 합성 자료다."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from src.shared.report_quality.assessment import assess_safety
from src.shared.report_quality.comparison_claims import (
    COMPARISON_LIMITATION_SLOT,
    STATED_DIFFERENTIATOR_SLOT,
    stated_differentiator_claim,
    stated_differentiator_limitation_claim,
)
from src.shared.report_quality.constants import (
    INTERPRETATION_CLAIM_TYPE,
    LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    STATED_DIFFERENTIATOR_CLAIM_TYPE,
    STRICT_QUALITY_CONTRACT_VERSION,
)
from src.shared.report_quality.contract import contract_for_generation
from src.shared.report_quality.dto import (
    ClaimFact, ReportCandidate, ReportSectionCandidate, SourceDocument,
)
from src.shared.report_quality.safety_problem_kinds import safety_problem_kind


_COMPANY = "합성법인"
_SOURCE_TEXT = "당사는 2021년 국내 최초로 12개 시험 공정을 독자 개발했다."
_CLAIM = f"{_COMPANY}는 2021년 국내 최초로 12개 시험 공정을 독자 개발했다."
_SECTION = "competitive_position"
_NUMERIC_PROBLEMS = {"numeric_labels_missing", "numeric_binding_missing"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidate(*, claim: str = _CLAIM, source_text: str = _SOURCE_TEXT) -> ReportCandidate:
    source = SourceDocument(
        source_id="source-1",
        document_identity="document:example.test:official-1",
        exact_evidence_hashes=(_sha(source_text),),
        publisher=_COMPANY,
        source_kind="dart_business_report",
        published_on="2026-03-31",
    )
    base = ClaimFact(
        fact_id="stated-1",
        section_owner=_SECTION,
        source_id=source.source_id,
        source_identity=source.document_identity,
        verification_state="verified",
        claim_slot=STATED_DIFFERENTIATOR_SLOT,
        evidence_binding_valid=True,
        claim=claim,
        supporting_source_ids=(source.source_id,),
        supporting_source_identities=(source.document_identity,),
        supporting_evidence_hashes=(_sha(source_text),),
        claim_type=STATED_DIFFERENTIATOR_CLAIM_TYPE,
        legal_entity=_COMPANY,
        state_evidence=source_text,
    )
    limitation = replace(
        base,
        fact_id="limitation-1",
        claim_slot=COMPARISON_LIMITATION_SLOT,
        claim=stated_differentiator_limitation_claim(_COMPANY, claim),
    )
    return ReportCandidate(
        (ReportSectionCandidate(_SECTION, (base.fact_id, limitation.fact_id)),),
        (base, limitation),
        (source,),
    )


def _problems(candidate: ReportCandidate, version: str = STRICT_QUALITY_CONTRACT_VERSION):
    return assess_safety(candidate, contract_for_generation(version)).problems


def _numeric_kinds(candidate: ReportCandidate, fact_id: str) -> set[str]:
    return {
        safety_problem_kind(problem)
        for problem in _problems(candidate)
        if problem.startswith(fact_id)
    } & _NUMERIC_PROBLEMS


def test_공식_자기선언과_정확한_재인용은_FULL에서_함께_통과한다():
    assert _problems(_candidate()) == ()


@pytest.mark.parametrize("kind", ("filing", "dart_business_report", "official_web_page"))
def test_공식_등록부에_결속된_프로그램의_숫자를_인정한다(kind: str):
    candidate = _candidate()
    candidate = replace(candidate, sources=(replace(candidate.sources[0], source_kind=kind),))
    assert _problems(candidate) == ()


def test_재인용_정본은_기존_생산_문구와_글자가_같다():
    assert stated_differentiator_limitation_claim("합성법인", "원본 주장") == (
        "합성법인가 공식 자료에서 밝힌 표현의 범위만 옮겼으며 "
        "'원본 주장'에 대한 타사 비교 판정은 포함하지 않습니다."
    )


@pytest.mark.parametrize(
    ("raw", "aliases", "expected"),
    [
        (_SOURCE_TEXT, (), _CLAIM),
        (_CLAIM, (), _CLAIM),
        ("우리 회사는 2021년 공정을 개발했다.", (), "합성법인는 2021년 공정을 개발했다."),
        ("별칭은 2021년 공정을 개발했다.", ("별칭",), "합성법인은 2021년 공정을 개발했다."),
        ("1. 당사는 ２０２１년\n공정을 개발했다.", (), "합성법인는 2021년 공정을 개발했다."),
    ],
)
def test_원본_생산_정본의_대명사_별칭_장식_규칙은_그대로다(raw, aliases, expected):
    assert stated_differentiator_claim(raw, _COMPANY, aliases) == expected


@pytest.mark.parametrize(
    "claim",
    [
        _CLAIM.replace("개발했다", "폐쇄했다"),
        _CLAIM.replace("개발했다", "개발하지 않았다"),
        _CLAIM.replace("개발했다", "개발할 예정이다"),
        _CLAIM + " 따라서 시장 지배력을 확보했다.",
        _CLAIM + " ",
    ],
    ids=("동사", "부정", "시점", "추가해석", "공백변조"),
)
def test_숫자가_같아도_원본_주장_변조는_재인용과_함께_차단한다(claim: str):
    candidate = _candidate(claim=claim)
    for fact in candidate.facts:
        assert _numeric_kinds(candidate, fact.fact_id) == _NUMERIC_PROBLEMS


def test_안전_평가는_운반되지_않은_별칭을_원문에서_추측하지_않는다():
    raw = _SOURCE_TEXT.replace("당사", "별칭")
    claim = stated_differentiator_claim(raw, _COMPANY, ("별칭",))
    candidate = _candidate(claim=claim, source_text=raw)
    assert _numeric_kinds(candidate, "stated-1") == _NUMERIC_PROBLEMS
    assert _numeric_kinds(candidate, "limitation-1") == _NUMERIC_PROBLEMS


@pytest.mark.parametrize(
    "changes",
    [
        {"source_kind": "other"},
        {"source_kind": "news"},
        {"source_kind": "official_page_like"},
        {"counts_toward_document_floor": False},
        {"publisher": "다른 합성법인"},
        {"publisher": ""},
        {"published_on": ""},
        {"published_on": "2026-02-30"},
        {"published_on": "20260331"},
        {"exact_evidence_hashes": (_sha("다른 원문"),)},
        {"document_identity": "document:example.test:other"},
    ],
    ids=("일반출처", "뉴스", "임의공식종류", "공식수미포함", "다른발행자", "빈발행자",
         "빈날짜", "없는날짜", "비정본날짜", "미등록원문", "다른문서"),
)
def test_출처_자격이나_원문_등록이_다르면_숫자_예외가_없다(changes):
    candidate = _candidate()
    candidate = replace(candidate, sources=(replace(candidate.sources[0], **changes),))
    for fact in candidate.facts:
        assert _numeric_kinds(candidate, fact.fact_id) == _NUMERIC_PROBLEMS


@pytest.mark.parametrize(
    "changes",
    [
        {"verification_state": "unverified"},
        {"evidence_binding_valid": False},
        {"claim_type": INTERPRETATION_CLAIM_TYPE},
        {"claim_slot": "competitive_position:self_context"},
        {"section_owner": "identity"},
        {"legal_entity": "다른 합성법인"},
        {"source_id": "absent-source"},
        {"source_identity": "document:example.test:other"},
        {"supporting_source_ids": ()},
        {"supporting_source_ids": ("different-source",)},
        {"supporting_source_ids": ("source-1", "source-1")},
        {"supporting_source_identities": ()},
        {"supporting_source_identities": ("document:example.test:other",)},
        {"supporting_evidence_hashes": ()},
        {"supporting_evidence_hashes": (_sha("다른 원문"),)},
        {"state_evidence": _SOURCE_TEXT + " "},
        {"state_evidence": _SOURCE_TEXT.replace("12개", "13개")},
        {"comparison_judgment": "competitive_advantage"},
    ],
    ids=("미검증", "결속무효", "해석", "다른슬롯", "다른장", "다른법인", "출처없음",
         "신원불일치", "인용없음", "인용불일치", "복수인용", "인용신원없음", "인용신원불일치",
         "지문없음", "지문불일치", "원문공백변조", "원문값변조", "우열판정"),
)
def test_프로그램_결속이_하나라도_어긋나면_원본과_재인용이_막힌다(changes):
    candidate = _candidate()
    base, limitation = candidate.facts
    candidate = replace(candidate, facts=(replace(base, **changes), limitation))
    assert _numeric_kinds(candidate, base.fact_id) == _NUMERIC_PROBLEMS
    assert _numeric_kinds(candidate, limitation.fact_id) == _NUMERIC_PROBLEMS


@pytest.mark.parametrize(
    "changes",
    [
        {"raw_value": "12"}, {"display_value": "12개"}, {"calculation": "6+6"},
        {"rounding_rule": "half_up"}, {"numeric_checks": ("numeric-binding-v1:x",)},
        {"metric": "공정 수"}, {"period_start": "2021-01-01"},
        {"period_end": "2021-12-31"}, {"sign": "positive"}, {"unit": "개"},
        {"unit_dimension": "count"}, {"formula": "sum"},
    ],
)
def test_구조화_수치_입력이_있으면_원문_숫자_예외로_우회하지_못한다(changes):
    candidate = _candidate()
    base, limitation = candidate.facts
    candidate = replace(candidate, facts=(replace(base, **changes), limitation))
    expected = {"numeric_labels_missing"} if "numeric_checks" in changes else _NUMERIC_PROBLEMS
    assert _numeric_kinds(candidate, base.fact_id) == expected
    if "numeric_checks" in changes:
        # 깨진 versioned 결속은 기존 검산기로 보내므로 «없음» 대신 상세 오류다.
        assert "claim_detail" in {
            safety_problem_kind(problem) for problem in _problems(candidate)
            if problem.startswith(base.fact_id)
        }
    assert _numeric_kinds(candidate, limitation.fact_id) == _NUMERIC_PROBLEMS


@pytest.mark.parametrize(
    "claim",
    [
        _CLAIM.replace("12개", "13개"),
        _CLAIM.replace("2021년", "2022년"),
        _CLAIM.replace("12개", "-12개"),
        _CLAIM.replace("12개", "12명"),
        _CLAIM.replace("12개", "24개"),
    ],
    ids=("값", "연도", "부호", "단위", "계산값"),
)
def test_원문에_없는_숫자는_원본도_재인용도_막는다(claim: str):
    candidate = _candidate(claim=claim)
    for fact in candidate.facts:
        assert _numeric_kinds(candidate, fact.fact_id) == _NUMERIC_PROBLEMS


def test_같은_출처의_형제_원문에만_숫자가_있어도_빌리지_않는다():
    candidate = _candidate(source_text="당사는 시험 공정을 독자 개발했다.")
    source = candidate.sources[0]
    candidate = replace(candidate, sources=(replace(
        source, exact_evidence_hashes=(*source.exact_evidence_hashes, _sha(_SOURCE_TEXT)),
    ),))
    assert _numeric_kinds(candidate, "stated-1") == _NUMERIC_PROBLEMS


@pytest.mark.parametrize("keep_hidden_base", (False, True))
def test_원본이_최종_공개_집합에_없으면_재인용을_인정하지_않는다(keep_hidden_base: bool):
    candidate = _candidate()
    base, limitation = candidate.facts
    candidate = replace(
        candidate,
        facts=(base, limitation) if keep_hidden_base else (limitation,),
        sections=(ReportSectionCandidate(_SECTION, (limitation.fact_id,)),),
    )
    assert _numeric_kinds(candidate, limitation.fact_id) == _NUMERIC_PROBLEMS


@pytest.mark.parametrize("suffix", (" 추가적인 설명이다.", " "))
def test_재인용_문구는_숫자가_같아도_정본_템플릿이어야_한다(suffix: str):
    candidate = _candidate()
    base, limitation = candidate.facts
    candidate = replace(candidate, facts=(base, replace(limitation, claim=limitation.claim + suffix)))
    assert _numeric_kinds(candidate, base.fact_id) == set()
    assert _numeric_kinds(candidate, limitation.fact_id) == _NUMERIC_PROBLEMS


def test_숫자가_같아도_다른_주장을_인용한_한계_문장은_막는다():
    candidate = _candidate()
    base, limitation = candidate.facts
    other_claim = _CLAIM.replace("독자 개발했다", "공급했다")
    candidate = replace(candidate, facts=(base, replace(
        limitation, claim=stated_differentiator_limitation_claim(_COMPANY, other_claim),
    )))
    assert _numeric_kinds(candidate, limitation.fact_id) == _NUMERIC_PROBLEMS


@pytest.mark.parametrize(
    "source_text",
    (
        "경쟁사는 2021년 국내 최초로 12개 시험 공정을 독자 개발했다.",
        "당사는 2021년 12개 시험 공정을 폐쇄했다.",
    ),
    ids=("타사주어", "선언표지없음"),
)
def test_회사_주어와_선언_표지가_없는_원문은_숫자를_빌리지_못한다(source_text: str):
    claim = stated_differentiator_claim(source_text, _COMPANY)
    candidate = _candidate(claim=claim, source_text=source_text)
    for fact in candidate.facts:
        assert _numeric_kinds(candidate, fact.fact_id) == _NUMERIC_PROBLEMS


def test_같은_원문이라도_다른_출처의_원본과_짝짓지_않는다():
    candidate = _candidate()
    base, limitation = candidate.facts
    second = replace(candidate.sources[0], source_id="source-2")
    base = replace(base, source_id=second.source_id, supporting_source_ids=(second.source_id,))
    candidate = replace(candidate, facts=(base, limitation), sources=(*candidate.sources, second))
    assert _numeric_kinds(candidate, base.fact_id) == set()
    assert _numeric_kinds(candidate, limitation.fact_id) == _NUMERIC_PROBLEMS


@pytest.mark.parametrize("version", (QUALITY_CONTRACT_VERSION, LEGACY_STRICT_QUALITY_CONTRACT_VERSION))
def test_FULL_v3_밖에서는_기존_수치_결속_요구를_유지한다(version: str):
    candidate = _candidate()
    problems = _problems(candidate, version)
    assert [safety_problem_kind(problem) for problem in problems] == [
        "numeric_labels_missing", "numeric_binding_missing",
        "numeric_labels_missing", "numeric_binding_missing",
    ]


def test_숫자_없는_프로그램은_기존대로_처리한다():
    candidate = _candidate(
        claim=f"{_COMPANY}는 국내 최초로 시험 공정을 독자 개발했다.",
        source_text="당사는 국내 최초로 시험 공정을 독자 개발했다.",
    )
    assert _problems(candidate) == ()
