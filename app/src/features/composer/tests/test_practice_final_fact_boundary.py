"""검수 상태를 직접 받은 사실 생성 입구의 교육 예시 경계를 독립 검증한다."""

import hashlib

import pytest

from src.features.composer.port import ComposedSentence
from src.features.composer.prose_facts import ProseEvidence, evaluate_verified_prose_fact
from src.features.provenance.sources import Source, SourceKind
from src.shared.report_evidence.practice_context import build_practice_context


SLOT = "past_changes:completed_execution"


def _evidence(number, text, *, practice=False, news=False):
    marker = "프롬프트 예시: 다음 기준으로 요청합니다."
    ranges = (marker, text) if practice else (text,)
    digest = hashlib.sha256("\n".join(ranges).encode()).hexdigest()
    location = f"https://example.org/guide · 목록 {len(ranges)}번째 항목"
    context = build_practice_context(
        ranges=ranges, document_id="independent-guide", document_sha256=digest,
        fragment_index=len(ranges)-1, fragment_location=location,
    ) if practice else ""
    if practice:
        assert context
    source = Source(
        number=number, kind=SourceKind.NEWS if news else SourceKind.OTHER,
        label="독립 검증 원문", source_id=f"independent-source-{number}",
        title="교육 안내" if practice else "회사 실행 사례", publisher="새봄소프트",
        url="https://example.org/guide", location=location,
        document_content_sha256=digest,
        exact_evidence_hashes=[hashlib.sha256(text.encode()).hexdigest()],
    )
    return ProseEvidence(str(number), source, text, practice_context_json=context)


def _build(claim, evidence, *, grade="확인", section="past_changes", slot=SLOT):
    sentence = ComposedSentence(
        text=claim, citations=tuple(item.fragment_id for item in evidence),
        grade=grade, planned_claim_slot=slot, verification_state="verified",
    )
    return evaluate_verified_prose_fact(
        sentence, section_id=section, company_name="새봄소프트",
        as_of_date="2026-10-10", evidence=evidence,
    )


@pytest.mark.parametrize("grade", ["확인", "해석"])
def test_검수완료_상태도_교육_예시를_실제_완료_사실로_봉인하지_않는다(grade):
    text = "처리 후보마다 검토 근거를 기록하고 오류 분류표를 정리합니다."
    result = _build("처리 후보마다 검토 근거를 기록하고 오류 분류표를 정리했다.",
                    (_evidence(1, text, practice=True),), grade=grade)
    assert result.fact is None
    assert result.reason_code == "source_practice_unbound"


def test_무관한_실제_회사_근거로_예시_완료_사실을_세탁하지_않는다():
    example = "온보딩 대안의 오류 화면과 로딩 화면을 검수합니다."
    actual = "당사는 기업용 보안 서비스를 운영했습니다."
    result = _build("온보딩 대안의 오류 화면과 로딩 화면을 검수했다.",
                    (_evidence(1, example, practice=True), _evidence(2, actual)))
    assert result.fact is None
    assert result.reason_code == "source_practice_unbound"


@pytest.mark.parametrize("text,news", [
    ("회사는 직원 교육 과정에 오류 분류표 검수 원칙을 도입했습니다.", False),
    ("회사는 기업 고객과 보안 서비스 PoC를 진행했다고 밝혔습니다.", True),
    ("당사 직원은 검토 근거를 기록하고 오류 분류표를 정리합니다.", False),
])
def test_실제_도입_기사_PoC와_직원_업무_원칙은_정상_사실로_보존한다(text, news):
    result = _build(text, (_evidence(1, text, news=news),))
    assert result.fact is not None, result.reason_code


def test_예시와_실제_실행_근거가_함께_있어도_실제_절로_지원되는_사실은_보존한다():
    example = "새 온보딩 화면의 검수 항목을 정리합니다."
    actual = "당사는 기업용 보안 서비스를 운영했습니다."
    result = _build(actual, (_evidence(1, example, practice=True), _evidence(2, actual)))
    assert result.fact is not None, result.reason_code


def test_다른_슬롯의_교육_내용_소개를_회사_실행으로_오인해_폐기하지_않는다():
    text = "교육 자료는 온보딩 화면의 검수 항목을 소개한다."
    result = _build(text, (_evidence(1, text, practice=True),),
                    section="portfolio", slot="portfolio:product_role")
    assert result.fact is not None, result.reason_code
