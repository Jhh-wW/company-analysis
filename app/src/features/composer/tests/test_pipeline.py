"""v2 오케스트레이션(run_v2)을 못 박는다 (엔진 v2 소단계 3-4b).

★ 여기서 지키는 것:
  ① 정상 흐름 — 가짜 작가·검수만으로 compose→verify→summary→render→validate가
     한 번에 이어져 v2 스키마의 pipeline Report가 나온다 (AI·네트워크 0회).
  ② 역할 분리 — 작가 ask는 작성 프롬프트만, 검수 ask는 판정 프롬프트만 받는다
     (Generator/Evaluator 분리).
  ③ fail-closed — 본문이 통째로 비어 요약을 만들 수 없으면 V2ValidationError로
     끝난다 (조용한 통과 없음).
  ④ 관측 지표 — 초안·생존 문장 수가 실제 개수와 일치한다.
  ⑤ 중복 검출 경고 — dup_detect가 뭔가 잡아도 run_v2는 그대로 끝난다(예외
     없음). 잡히면 WARNING 로그로만 남는다. `find_numeric_duplicates`를
     실제 보고서로 다시 만들지 않고 monkeypatch로 대체한다 — compose→
     verify→dedupe 각 단계가 저마다 문장을 걸러내 실제 중복을 살아남게
     만들기 어렵고(실측), 이 시험이 지켜야 할 것은 «잡히면
     막지 않는다»는 배선이지 dup_detect 판정 정확도(그건 test_dup_detect.py
     몫)가 아니기 때문이다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

import pytest

from src.features.composer import pipeline as pipeline_module
from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    SECTION_IDS,
)
from src.features.composer.dup_detect import (
    CONFIDENCE_CONFIRMED,
    DuplicateFinding,
    NumericOccurrence,
)
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    SectionEvidencePacket,
    SectionEvidencePacketSet,
)
from src.features.composer.pipeline import V2RunOutput, run_v2
from src.features.composer.port import FilingMeta, PerformanceTable
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.composer.validate import V2ValidationError
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.pipeline.port import Grade, Report
from src.features.provenance.sources import Source, SourceKind
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    classify_v2_validation_final_gate_reason,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.source_identity import document_identity_from_parts

_LOGGER_NAME = "src.features.composer.pipeline"

#: 장 순서를 표시할 숫자 없는 한국어 표지 — 숫자를 넣으면 수치 검증(3-2)이
#: 근거에 없는 숫자로 보고 강등하므로 일부러 뺀다.
_SECTION_MARKS = "가나다라마바사아자"

#: 검수 프롬프트에서 대조 문장 번호를 읽는 모양 (verify._build_review_prompt)
_REVIEW_NUMBER_RE = re.compile(
    r"\[(\d+)\] \(등급: [^,\n]+, 인용:"
)

# 장별로 다른 원자 사실을 실제 합성 원문에 싣는다. 일반어 해석 filler로
# 요약 하한을 채우면 미결속 문장을 공개하는 회귀를 가릴 수 있다.
_SECTION_CLAIMS = {
    "identity": (
        ("identity:corporate_identity", "가나다전자는 반도체 검사 장비 전문기업이다."),
        ("identity:official_location", "본사는 수원에 있다."),
    ),
    "business_model": (
        ("business_model:revenue_model", "검사 장비 판매에서 수익이 발생한다."),
        ("business_model:customer_type", "주요 고객은 반도체 제조사다."),
    ),
    "portfolio": (
        ("portfolio:product_role", "웨이퍼 검사기는 생산라인의 결함을 점검하는 장비다."),
        ("portfolio:customer_fit", "광학 측정기는 고객 공정의 치수 편차를 측정한다."),
    ),
    "past_changes": (
        ("past_changes:completed_execution", "회사는 장비 조립 공장 증설을 완료했다."),
        ("past_changes:cumulative_change", "납품 후 유지보수 사업을 시작했다."),
    ),
    "current_challenges": (
        ("current_challenges:issue", "검사 부품 조달 기간 지연이 현안이다."),
        ("current_challenges:response", "회사는 대체 공급사를 확보하고 있다."),
    ),
    "future_strategy": (
        ("future_strategy:stated_plan", "회사는 차세대 검사 장비 개발을 추진할 계획이다."),
        ("future_strategy:plan_condition", "회사는 고객사 성능 인증을 조건으로 신규 장비를 출시할 계획이다."),
    ),
    "operations_partners": (
        ("operations_partners:value_chain", "부품 조달 뒤 사내 공장에서 검사 장비를 조립한다."),
        ("operations_partners:distribution_relation", "완성 장비는 반도체 제조사의 생산라인에 납품한다."),
    ),
    "culture": (
        ("culture:work_principle", "고객 존중을 핵심 가치로 삼는다."),
        ("culture:decision_process", "현장 문제는 담당 부서가 함께 검토한다."),
    ),
    "competitive_position": (
        ("competitive_position:stated_differentiator", "가나다전자는 정밀 광학 검사 기술을 차별점으로 제시한다."),
        ("competitive_position:self_context", "회사는 고객 공정에 맞춘 검사 소프트웨어 제공을 경쟁력으로 설명한다."),
    ),
}


def _raw_fragments() -> dict[int, dict[str, str]]:
    return {
        1: {
            "종류": "사업내용",
            "원문": " ".join(claims[0][1] for claims in _SECTION_CLAIMS.values()),
            "출처": "https://www.ganada.example/business",
            "문서일": "2026-08-01",
        },
        2: {
            "종류": "홈페이지",
            "원문": " ".join(claims[1][1] for claims in _SECTION_CLAIMS.values()),
            "출처": "https://www.ganada.example/about",
            "문서일": "2026-08-01",
        },
    }


def _strict_fragments() -> dict[int, dict[str, str]]:
    document_marks = (
        "가람",
        "나래",
        "다솜",
        "라온",
        "마루",
        "바다",
        "사랑",
        "아람",
    )
    return {
        number: {
            "종류": "공식 홈페이지",
            "원문": (
                "가나다전자는 공식 자료에서 회사 사업 고객 제품 전략 운영 문화 "
                f"경쟁 과제 대응 협력 실적을 설명한다. 문서 표지는 {document_marks[number - 1]}이다."
            ),
            "출처": f"https://www.ganada.example/document/{number}",
            "문서명": f"공식 자료 {number}",
        }
        for number in range(1, 9)
    }


def _strict_packet_set(
    *, evidence_texts: tuple[str, ...] = ()
) -> SectionEvidencePacketSet:
    """옛 FULL 시험 입력을 현재 typed 아홉 장 계약으로 고정한다."""

    all_claim_slots = tuple(
        slot_id
        for section_id in SECTION_IDS
        for slot_id in CLAIM_SLOTS_BY_SECTION[section_id]
    )
    fragments: list[CollectedFragment] = []
    for number, raw in _strict_fragments().items():
        # 이 fixture의 조각 하나가 곧 문서 전체다. 생산 수집기처럼 실제 원문
        # 바이트에서 문서 지문을 만들며 URL·번호를 hash 대용으로 쓰지 않는다.
        text = " ".join((str(raw["원문"]), *evidence_texts)).strip()
        fragments.append(
            CollectedFragment(
                fragment_id=str(number),
                kind=str(raw["종류"]),
                text=text,
                source_url=str(raw["출처"]),
                document_title=str(raw["문서명"]),
                document_identity=document_identity_from_parts(
                    url=str(raw["출처"])
                ),
                document_content_sha256=hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
                supported_claim_slots=all_claim_slots,
            )
        )
    generation = "a" * 64
    return SectionEvidencePacketSet(
        company_id="00123456",
        evidence_generation_sha256=generation,
        packets=tuple(
            SectionEvidencePacket(
                company_id="00123456",
                evidence_generation_sha256=generation,
                section_id=section_id,
                fragments=tuple(fragments),
            )
            for section_id in SECTION_IDS
        ),
    )


#: 8장(인재상·조직문화·일하는 방식)의 장 표시. 이 장만 원문 절 계약을 받는다.
_CULTURE_MARK = _SECTION_MARKS[SECTION_IDS.index("culture")]


def _section_json(mark: str) -> str:
    """해당 장의 원문 두 절과 명시된 주장 슬롯을 그대로 돌려준다."""
    section_id = SECTION_IDS[_SECTION_MARKS.index(mark)]
    return json.dumps(
        {
            "문장들": [
                {
                    "글": text,
                    "인용": [str(index)],
                    "등급": GRADE_CONFIRMED,
                    "주장슬롯": slot,
                }
                for index, (slot, text) in enumerate(_SECTION_CLAIMS[section_id], start=1)
            ]
        },
        ensure_ascii=False,
    )


#: 요약 «고르기» 프롬프트의 후보 줄 모양 — `logic.build_summary_selection_prompt`
#: 이 만드는 «번호. [장 이름] 문장» 한 줄.
_SUMMARY_CANDIDATE_RE = re.compile(r"^(\d+)\. \[([^\]]+)\] ", re.MULTILINE)

#: 한 번에 고르는 요약 문장 수 — 실제 AI가 따르는 지시(3~5) 중 최소치다.
_SUMMARY_PICKS = 3


def _summary_selection_json(prompt: str) -> str:
    """후보 목록을 읽어 «서로 다른 장에서 하나씩» 번호를 고른 응답을 만든다.

    ★ 왜 프롬프트를 읽나 — 요약이 「AI가 새로 쓴다」에서 「검증된 본문 문장
      중 고른다」로 바뀌었다(2026-09-11). 응답에 문장 글자를 담아도 그 글자는
      보고서에 실리지 않으므로, 가짜 작가도 «번호»를 골라야 실제 배선을
      지난다. 고정 번호를 박으면 후보 수가 달라질 때 조용히 범위 밖이 되어
      배선이 끊겨도 초록불이 된다.
    """

    첫번호_by_장: dict[str, int] = {}
    for number, title in _SUMMARY_CANDIDATE_RE.findall(prompt):
        첫번호_by_장.setdefault(title, int(number))
    고른번호 = list(첫번호_by_장.values())[:_SUMMARY_PICKS]
    return json.dumps(고른번호, ensure_ascii=False)


def section_id_in_prompt(prompt: str) -> str:
    """장 작성 프롬프트가 어느 장의 것인지 읽는다. 못 가리면 빈 문자열."""

    found = [
        section_id for section_id in SECTION_IDS if f"{section_id}:" in prompt
    ]
    return found[0] if len(found) == 1 else ""


def summary_candidate_filler(prompt: str) -> dict[str, object] | None:
    """이름 표 시험의 원문에 실제 있는 장별 절만 요약 재료로 쓴다.

    해당 절이 프롬프트에 없으면 보충하지 않는다. 근거 없는 장 이름 filler는
    공개 경계의 결함을 숨기므로 만들지 않는다.
    """
    section_id = section_id_in_prompt(prompt)
    claims = {
        "identity": ("가나다회사는 사업부문 하나를 운영한다.", "1", "identity:business_definition"),
        "business_model": ("회사는 고객에게 사업부문 운영 서비스를 제공한다.", "1", "business_model:value_exchange"),
        "culture": ("고객 존중을 핵심 가치로 삼는다.", "2", "culture:work_principle"),
    }
    if section_id not in claims:
        return None
    text, citation, slot = claims[section_id]
    if text not in prompt:
        return None
    return {"글": text, "인용": [citation], "등급": GRADE_CONFIRMED, "주장슬롯": slot}



class _FakeWriter:
    """작성 프롬프트만 받아 장 JSON·요약 «번호»를 돌려주는 가짜 작가."""

    def __init__(self, section_response=None):
        self.prompts: list[str] = []
        self.section_calls = 0
        self._section_response = section_response

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "핵심 요약" in prompt:
            return _summary_selection_json(prompt)
        # 장 프롬프트는 v3 정본 순서로 들어온다 (compose_sections 계약)
        mark = _SECTION_MARKS[self.section_calls % len(_SECTION_MARKS)]
        self.section_calls += 1
        if self._section_response is not None:
            return self._section_response
        return _section_json(mark)


class _FakeReviewer:
    """판정 프롬프트의 문장 번호 전부를 «참»으로 돌려주는 가짜 검수."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # 정상 계획 fixture에만 원문 절과 대상·활동을 정확히 묶는다.
        # 근거가 없는 다른 계획을 «참»만으로 살리지 않는다.
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        if items:
            verdicts = []
            for item in items:
                verdict = {"번호": item.number, "결과": "참"}
                if item.section:
                    verdict.update({"장": item.section, "근거": [
                        value.split()[-1] for value in item.citations
                    ]})
                for index, (target, activity) in enumerate((
                    ("차세대 검사 장비 개발", "추진"), ("신규 장비", "출시"),
                )):
                    quote = _SECTION_CLAIMS["future_strategy"][index][1]
                    if item.text == quote:
                        verdict["검증근거"] = {"미래근거": [{
                            "근거": str(index + 1), "대상": target,
                            "활동": activity, "원문": quote, "양태": "계획",
                        }]}
                verdicts.append(verdict)
            return json.dumps({"판정": verdicts}, ensure_ascii=False)
        grouped = re.findall(
            r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)",
            prompt,
        )
        if grouped:
            return json.dumps(
                {
                    "판정": [
                        {
                            "번호": int(number),
                            "장": section_id,
                            "근거": re.findall(r"조각 (\d+)", citations),
                            "결과": "참",
                        }
                        for number, section_id, _kind, citations in grouped
                    ]
                },
                ensure_ascii=False,
            )
        numbers = [int(value) for value in _REVIEW_NUMBER_RE.findall(prompt)]
        return json.dumps(
            {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
            ensure_ascii=False,
        )


# ══════════════════════════════════════════════════════════
# ①②④ 정상 흐름
# ══════════════════════════════════════════════════════════


def test_정상_흐름이면_검증된_v2_Report가_나온다():
    writer, reviewer = _FakeWriter(), _FakeReviewer()
    output = run_v2(
        "가나다전자", _raw_fragments(), None,
        writer_ask=writer, reviewer_ask=reviewer,
        corp_type="상장사", as_of_date="2026-08-24",
    )
    assert isinstance(output, V2RunOutput)
    report = output.report
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert [section.cell for section in report.sections] == list(SECTION_IDS)
    assert all(len(section.prose_lines) == 2 for section in report.sections)
    assert len(report.summary_items) == 5
    assert report.corp_type == "상장사"
    assert report.grade is Grade.PARTIAL
    # 합성 원문의 장별 절과 claim slot이 실제 FactRecord로 결속돼야 한다.
    assert len(report.fact_records) == 18
    assert all(fact.evidence_binding for fact in report.fact_records)
    assert output.quality_observation.mode == "generation-shadow"
    assert output.quality_observation.safety_decision == "공개 가능"
    assert output.quality_observation.publication_grade == "부분 완성"
    assert output.quality_observation.release_allowed is True
    assert report.quality_contract_version == output.quality_observation.contract_version
    assert report.safety_decision == "공개 가능"
    assert report.publication_policy == "structured-safety-v1"
    assert "too_few_substantive_claims" in output.quality_observation.quality_problem_codes
    assert "too_few_document_sources" in output.quality_observation.quality_problem_codes
    assert not output.quality_observation.safety_problems
    assert sorted(source.number for source in report.citations) == [1, 2]


def test_SHADOW의_관측_품질과_Report_품질이_같다():
    output = run_v2(
        "가나다전자", _raw_fragments(), None,
        writer_ask=_FakeWriter(), reviewer_ask=_FakeReviewer(),
        corp_type="상장사", as_of_date="2026-08-24",
    )
    assert output.report.grade is Grade.PARTIAL
    assert output.report.sections
    assert output.report.quality_observation == output.quality_observation
    assert output.report.safety_decision == output.quality_observation.safety_decision
    assert output.report.publication_policy == "structured-safety-v1"
    assert output.report.quality_contract_version == output.quality_observation.contract_version


@pytest.mark.parametrize("defect", ("missing_slot", "unsupported_claim"))
def test_참_판정만으로_미결속_문장을_본문이나_요약에_살리지_않는다(defect):
    blocked_claim = _SECTION_CLAIMS["identity"][0][1]
    if defect == "unsupported_claim":
        blocked_claim = "가나다전자는 달 표면의 광산을 독점 운영한다."

    class UnboundWriter(_FakeWriter):
        def __call__(self, prompt):
            payload = json.loads(super().__call__(prompt))
            if section_id_in_prompt(prompt) == "identity":
                row = payload["문장들"][0]
                row["글"] = blocked_claim
                if defect == "missing_slot":
                    row.pop("주장슬롯")
            return json.dumps(payload, ensure_ascii=False)

    writer = UnboundWriter()
    output = run_v2(
        "가나다전자", _raw_fragments(), None,
        writer_ask=writer, reviewer_ask=_FakeReviewer(),
    )
    public_text = " ".join(
        [text for section in output.report.sections for text, _ in section.prose_lines]
        + [item.text for item in output.report.summary_items]
    )
    assert blocked_claim not in public_text
    assert blocked_claim not in {fact.claim for fact in output.report.fact_records}
    assert len(writer.prompts) == 9
    assert output.report.fact_records
    fingerprint = hashlib.sha256(blocked_claim.encode("utf-8")).hexdigest()
    diagnostics = [entry for entry in output.review_diagnostics
                   if entry.get("candidate_sha256") == fingerprint]
    assert len(diagnostics) == 1
    assert diagnostics[0]["section_id"] == "identity"
    assert diagnostics[0]["kind"] == "본문"
    assert diagnostics[0]["candidate_fingerprint_version"] == "candidate-raw-utf8-v1"
    expected_reason = (
        "public_sentence_fact_unbound" if defect == "missing_slot"
        else "prose_own_source_unsupported"
    )
    assert diagnostics[0]["reason_code"] == expected_reason
    assert blocked_claim not in json.dumps(diagnostics, ensure_ascii=False)


@pytest.mark.parametrize("partial", (False, True))
def test_최종_결속선택에서_본문을_모두_잃은_장에도_독자_안내가_남는다(partial):
    from src.features.composer.evidence_availability import EvidenceAvailability

    class UnboundSectionWriter(_FakeWriter):
        def __call__(self, prompt):
            payload = json.loads(super().__call__(prompt))
            if section_id_in_prompt(prompt) == "identity":
                for sentence in payload["문장들"]:
                    sentence.pop("주장슬롯")
            return json.dumps(payload, ensure_ascii=False)

    writer = UnboundSectionWriter()
    output = run_v2(
        "가나다전자", _raw_fragments(), None,
        writer_ask=writer, reviewer_ask=_FakeReviewer(),
        evidence_availability=EvidenceAvailability("partial") if partial else None,
    )
    section = next(section for section in output.report.sections if section.cell == "identity")
    assert not section.prose_lines and not section.fact_ids
    assert section.guidance_lines
    assert any(entry.get("section_id") == "identity"
               and entry.get("reason_code") == "public_sentence_fact_unbound"
               for entry in output.review_diagnostics)
    # 이 legacy fixture의 문화 자료는 공식 문서 신원 조건이 없어 부분 작성에서 생략한다.
    expected_sections = set(SECTION_IDS) - ({"culture"} if partial else set())
    written_sections = [section_id_in_prompt(prompt) for prompt in writer.prompts]
    assert set(written_sections) == expected_sections
    assert len(written_sections) == len(expected_sections)
    assert output.report.fact_records and output.report.summary_items
    if partial:
        assert section.empty_reason


def _structured_financial_table() -> PerformanceTable:
    payload = {
        "status": "000",
        "list": [
            {
                "fs_div": "CFS",
                "sj_div": "IS",
                "account_id": "ifrs-full_Revenue",
                "account_nm": "매출액",
                "bsns_year": "2025",
                "reprt_code": "11011",
                "currency": "KRW",
                "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                "thstrm_amount": "1242800000000",
                "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
                "frmtrm_amount": "1100000000000",
                "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
                "bfefrmtrm_amount": "1000000000000",
            },
            {
                "fs_div": "CFS",
                "sj_div": "IS",
                "account_id": "dart_OperatingIncomeLoss",
                "account_nm": "영업이익",
                "bsns_year": "2025",
                "reprt_code": "11011",
                "currency": "KRW",
                "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                "thstrm_amount": "200000000000",
                "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
                "frmtrm_amount": "150000000000",
                "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
                "bfefrmtrm_amount": "100000000000",
            },
        ],
    }
    evidence = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return PerformanceTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적",
        headers=("사업연도", "매출액", "영업이익"),
        rows=(
            ("2025", "12,428", "2,000"),
            ("2024", "11,000", "1,500"),
            ("2023", "10,000", "1,000"),
        ),
        unit="억원",
        cite="조각 9·재무",
        raw_rows=(
            ("2025", "1,242,800,000,000", "200,000,000,000"),
            ("2024", "1,100,000,000,000", "150,000,000,000"),
            ("2023", "1,000,000,000,000", "100,000,000,000"),
        ),
        scale_divisor="100000000",
        scale_places=0,
        evidence_rows=(evidence,) * 3,
        entity_scope="consolidated",
        raw_unit="원",
        unit_dimension="currency",
    )


def test_작가와_검수는_서로_다른_프롬프트만_받는다():
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
    )

    # 작가: 장 작성 9회. 요약은 결속된 본문에서 결정적으로 고른다.
    assert len(writer.prompts) == 9
    assert not any("핵심 요약" in prompt for prompt in writer.prompts)
    assert not any("판정" in prompt for prompt in writer.prompts)
    # 검수: 본문 1회뿐이다. 요약 재검증은 없어졌다 (2026-09-11) — 요약이
    # 검증된 본문 문장을 글자 그대로 싣게 되면서 다시 검수할 «새 글자»가
    # 사라졌다. 예전의 2회 중 하나가 요약 검수였다.
    assert len(reviewer.prompts) == 1
    assert all("판정" in prompt for prompt in reviewer.prompts)
    assert not any("핵심 요약" in prompt for prompt in reviewer.prompts)


@pytest.mark.parametrize(
    ("release_mode", "expects_quality_codes"),
    [
        # FULL은 회복 정책이 사유를 소유한다 — 얇은 결과는 report_recovery 코드로
        # 닫히고, 품질 코드를 지어내지 않는다.
        (ReleaseMode.FULL, False),
        # 회복 정책이 없는 엄격 경로에서만 숫자 하한 코드가 실린다(task 022).
        (ReleaseMode.ENFORCE_NO_PARTIAL, True),
    ],
)
def test_엄격모드는_AI요약을_부르지_않고_얇은_보고서를_막는다(
    release_mode: ReleaseMode, expects_quality_codes: bool
):
    """얇은 후보는 9장을 채웠다는 이유만으로 나가지 않는다 — 사유 주인은 모드마다 다르다."""

    class StrictWriter(_FakeWriter):
        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            section_id = SECTION_IDS[self.section_calls]
            text = (
                "고객 존중과 회사의 법인 정체성을 공식 자료에서 확인했다.",
                "고객에게 가치를 전달하는 판매 경로를 공식 자료에서 확인했다.",
                "고객이 선택할 제품 묶음과 역할을 공식 자료에서 확인했다.",
                "회사가 과거에 실행한 변화 흐름을 공식 자료에서 확인했다.",
                "현재 해결해야 할 운영 과제와 대응을 공식 자료에서 확인했다.",
                "앞으로 추진한다고 밝힌 전략 조건을 공식 자료에서 확인했다.",
                "협력사와 유통사의 운영 연결 관계를 공식 자료에서 확인했다.",
                "조직의 의사결정과 문화 원칙을 공식 자료에서 확인했다.",
                "경쟁 비교 기준과 회사의 차별점을 공식 자료에서 확인했다.",
            )[self.section_calls]
            self.section_calls += 1
            return json.dumps(
                {
                    "문장들": [
                        {
                            "글": text,
                            "인용": ["2"],
                            "등급": GRADE_CONFIRMED,
                            "주장슬롯": CLAIM_SLOTS_BY_SECTION[section_id][0],
                        }
                    ]
                },
                ensure_ascii=False,
            )

    writer = StrictWriter()
    reviewer = _FakeReviewer()

    with pytest.raises(V2ValidationError) as caught:
        run_v2(
            "가나다전자",
            _strict_fragments(),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=release_mode,
            section_evidence_packets=_strict_packet_set(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert len(writer.prompts) == 9
    assert not any("핵심 요약" in prompt for prompt in writer.prompts)
    assert len(reviewer.prompts) == 1
    assert caught.value.problems
    # 실질 claim 9건 < 하한 40건. 여기서 «어느 게이트가 사유를 소유하는가»를
    # 모드별로 못 박는다 — 이 순서가 뒤집히면 사용자에게 다른 이유가 나간다.
    # 두 경로 모두 숫자 하한 코드를 싣는다. 싣는 «주인»만 다르다 —
    # 엄격 경로는 STRICT 게이트가, FULL은 회복 정책이 싣는다.
    assert "too_few_substantive_claims" in caught.value.problem_codes
    # 코드를 싣기만 하고 분류가 안 되면 사용자 화면은 그대로 «출고 전 자동
    # 검증 거절»이다. 실제로 «보고서 품질 최소 기준 미달»로 갈라지는지까지 본다.
    assert (
        classify_v2_validation_final_gate_reason(caught.value.problem_codes)
        == FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR
    )
    if not expects_quality_codes:
        # FULL은 사람 원문 없이 닫힌 report_recovery 코드로 사유를 남긴다.
        assert all(
            problem.startswith("report_recovery:")
            for problem in caught.value.problems
        )


def test_엄격모드는_충분한_검증사실만_완성으로_봉인한다():
    """엄격 경로가 항상 막히는 장식용 게이트가 아님을 실제 조립으로 증명한다."""

    topics = (
        "법인 정체성과 설립 목적 및 공식 사업 범위",
        "고객 유형별 수익 방식과 판매 채널 및 가치 교환",
        "제품 묶음별 역할과 고객 적합성 및 사업 연결",
        "과거 완료 실행과 실적 변화 및 확인할 한계",
        "현재 해결 과제와 대응 행동 및 남은 점검 항목",
        "향후 발표 전략과 실행 시점 및 필요한 선행 조건",
        "공급 생산 유통 협력 관계와 회사의 운영 역할",
        "리더십 업무 원칙 의사결정 방식과 검증 사례",
        "비교 대상 지표 기준 범위와 경쟁 판단의 한계",
    )
    endings = (
        "첫째 의미를 공식 자료에서 확인했다.",
        "둘째 대상을 공식 자료에서 확인했다.",
        "셋째 경로를 공식 자료에서 확인했다.",
        "넷째 범위를 공식 자료에서 확인했다.",
        "다섯째 근거를 공식 자료에서 확인했다.",
        "여섯째 한계를 공식 자료에서 확인했다.",
    )

    class CompleteWriter(_FakeWriter):
        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            section_index = self.section_calls
            section_id = SECTION_IDS[section_index]
            self.section_calls += 1
            slots = CLAIM_SLOTS_BY_SECTION[section_id]
            return json.dumps(
                {
                    "문장들": [
                        {
                            "글": f"가나다전자는 {topics[section_index]}의 {ending}",
                            "인용": [str((section_index * 6 + index) % 8 + 1)],
                            "등급": GRADE_CONFIRMED,
                            "주장슬롯": slots[index % len(slots)],
                        }
                        for index, ending in enumerate(endings)
                    ]
                },
                ensure_ascii=False,
            )

    writer = CompleteWriter()
    reviewer = _FakeReviewer()
    expected_sentences = tuple(
        f"가나다전자는 {topic}의 {ending}"
        for topic in topics
        for ending in endings
    )
    output = run_v2(
        "가나다전자",
        _strict_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_strict_packet_set(
            evidence_texts=expected_sentences
        ),
        company_id="00123456",
        build_identity_sha256="b" * 64,
    )

    assert output.report.grade is Grade.COMPLETE
    assert output.report.shortfall_reasons == []
    assert output.report.publication_policy == "structured-safety-v1"
    assert output.quality_observation.quality_grade == "완성"
    assert output.quality_observation.safety_decision == "공개 가능"
    assert output.quality_observation.release_allowed is True
    assert len(output.report.fact_records) == 54
    assert len(output.report.summary_items) == 5
    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1
    assert not any("핵심 요약" in prompt for prompt in writer.prompts)


def test_인라인_대괄호_인용_흉내는_출고검증을_막지_않는다():
    """본문 글의 가짜 번호는 제거하고 명시적 인용만 부록에 남긴다."""
    class InlineCitationWriter(_FakeWriter):
        def __call__(self, prompt):
            payload = json.loads(super().__call__(prompt))
            for row in payload["문장들"]:
                row["글"] = "[999] " + row["글"]
            return json.dumps(payload, ensure_ascii=False)

    output = run_v2(
        "가나다전자", _raw_fragments(), None,
        writer_ask=InlineCitationWriter(), reviewer_ask=_FakeReviewer(),
    )
    assert all("[999]" not in text for section in output.report.sections
               for text, _cite in section.prose_lines)
    assert len(output.report.fact_records) == 18
    assert sorted(source.number for source in output.report.citations) == [1, 2]


def test_초안과_생존_문장_수를_그대로_센다():
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
    )

    # 장별 서로 다른 사실 18개와 결정적 요약 5개를 같은 방식으로 센다.
    assert output.composed_sentences == 23
    assert output.verified_sentences == 23
    assert sum(len(section.prose_lines) for section in output.report.sections) == 18
    assert len(output.report.summary_items) == 5


# ══════════════════════════════════════════════════════════
# ③ fail-closed — 빈 본문은 출고 검증에서 막힌다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("call_limit", (False, True))
def test_요약은_추가_AI호출이나_예산을_쓰지_않는다(call_limit):
    class SummaryCallForbidden(_FakeWriter):
        def __init__(self):
            super().__init__()
            self.summary_calls = 0

        def __call__(self, prompt):
            if "핵심 요약" in prompt:
                self.summary_calls += 1
                raise AskFatalError(RuntimeError("추가 요약 호출 금지"), call_limit=call_limit)
            return super().__call__(prompt)

    writer = SummaryCallForbidden()
    output = run_v2(
        "가나다전자", _raw_fragments(), None,
        writer_ask=writer, reviewer_ask=_FakeReviewer(),
    )
    assert writer.summary_calls == 0
    assert len(writer.prompts) == 9
    assert all(section.prose_lines for section in output.report.sections)
    assert len(output.report.summary_items) == 5
    body_claims = {fact.claim for fact in output.report.fact_records}
    assert all(re.sub(r"\s*\[\d+\]", "", item.text).strip() in body_claims
               for item in output.report.summary_items)


def test_본문이_통째로_비면_V2ValidationError로_끝난다():
    # 작가가 모든 장에서 «쓸 문장이 없다»고 답한 경우 — 요약 재료가 없어
    # 요약 3문장을 만들 수 없고, 마지막 출고 검증이 fail-closed로 막는다.
    writer = _FakeWriter(section_response=json.dumps({"문장들": []}))
    reviewer = _FakeReviewer()

    with pytest.raises(V2ValidationError) as caught:
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
        )

    assert any("핵심 요약" in problem for problem in caught.value.problems)
    # 본문이 비면 요약·검수 헛호출도 없어야 한다 (작가 9회로 끝)
    assert len(writer.prompts) == 9
    assert reviewer.prompts == []
    # 이 raise는 STRICT 품질 게이트가 아니라 validate_v2의 구조 검사다
    # (release_mode 기본값 SHADOW) — 품질 코드를 지어내지 않는다(task 022).
    assert caught.value.problem_codes == ()


def test_요약_후보가_세_문장_미만이면_보고서_전체가_막히고_AI도_안_부른다():
    """★ 정책을 «운영 진입점»에서 못 박는다 (2026-09-11 독립 검토 P2-3).

    계약(`docs/출력물 기준/00_핵심_요약/README.md`)은 카드 3~5개 고정이고,
    실패 시 처리가 「근거가 충분한 결론이 3개 미만이면 요약을 억지로 채우지
    않는다」다. 그래서 후보가 3문장 미만이면 요약만 줄여 내보내지 않고
    보고서 «전체»가 출고 검증에서 막힌다.

    이 시험이 없으면 다음 사람이 「요약이 짧으면 그냥 내보내자」로 조용히
    뒤집을 수 있다. 단계 시험은 「2문장으로 그대로 돌아온다」까지만 재고,
    그 뒤 무슨 일이 일어나는지는 여기서만 보인다.

    ★ 함께 못 박는 것 — 어차피 막힐 실행에서 고르기 AI를 «부르지 않는다».
      불러도 결과를 바꿀 수 없어 그 실행의 유료 1회가 그냥 사라진다.
    """

    # 아홉 장이 «같은» 사실을 쓰면 소유 장 하나로 모여 본문에 1문장만 남는다.
    class OnlyIdentityWriter(_FakeWriter):
        def __call__(self, prompt):
            payload = json.loads(super().__call__(prompt))
            payload["문장들"] = (
                payload["문장들"][:1] if section_id_in_prompt(prompt) == "identity" else []
            )
            return json.dumps(payload, ensure_ascii=False)

    writer = OnlyIdentityWriter()
    reviewer = _FakeReviewer()

    with pytest.raises(V2ValidationError) as caught:
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
        )

    assert any("핵심 요약" in problem for problem in caught.value.problems), (
        caught.value.problems
    )
    # 요약 «고르기» 프롬프트는 한 번도 나가지 않았다 — 장 9회로 끝이다.
    assert len(writer.prompts) == 9
    assert not any("핵심 요약" in prompt for prompt in writer.prompts)


def test_후보가_두_장에만_있으면_많아도_막히고_AI도_안_부른다():
    """★ 가드는 «후보 수»가 아니라 «서로 다른 장 수»로 본다 (재검토 P3-1).

    장당 최대 1개가 코드 강제라, 요약이 채울 수 있는 문장 수의 상한은 후보가
    걸쳐 있는 장의 수다. 예전 가드는 후보 «개수»만 봐서, 후보 4개가 두 장에만
    있는 실행이 유료 1회를 쓰고도 2문장으로 끝나 어차피 막혔다(실측 재현).
    """

    class _두_장만_쓰는_작가(_FakeWriter):
        """앞 두 장에만 서로 다른 문장을 쓰고 나머지는 «쓸 문장이 없다»."""

        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if "핵심 요약" in prompt:
                return _summary_selection_json(prompt)
            section_id = section_id_in_prompt(prompt)
            if section_id not in SECTION_IDS[:2]:
                return json.dumps({"문장들": []}, ensure_ascii=False)
            return _section_json(_SECTION_MARKS[SECTION_IDS.index(section_id)])

    writer = _두_장만_쓰는_작가()

    with pytest.raises(V2ValidationError) as caught:
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=writer,
            reviewer_ask=_FakeReviewer(),
        )

    assert any("핵심 요약" in problem for problem in caught.value.problems), (
        caught.value.problems
    )
    assert not any("핵심 요약" in prompt for prompt in writer.prompts), (
        "두 장뿐인데 고르기 AI를 불렀다 — 그 호출은 결과를 바꾸지 못한다"
    )


# ══════════════════════════════════════════════════════════
# ⑤ 중복 검출 경고 — 잡혀도 출고는 막지 않는다
# ══════════════════════════════════════════════════════════


def _fake_confirmed_finding() -> DuplicateFinding:
    """실측과 같은 모양의 «확정»급 중복 하나 (값+단위+기간 일치)."""
    occurrence_a = NumericOccurrence(
        section_id="business_model",
        section_label="2장 사업모델",
        format="문장",
        value="900",
        unit="억원",
        period="2025",
        metric_hint="매출액",
        excerpt="2025년 매출액은 900억원이다.",
    )
    occurrence_b = NumericOccurrence(
        section_id="financials",
        section_label="4장 재무",
        format="표",
        value="900",
        unit="억원",
        period="2025",
        metric_hint="매출액",
        excerpt="주요 재무 · 매출액 2025=900",
    )
    return DuplicateFinding(
        confidence=CONFIDENCE_CONFIRMED,
        reason="서로 다른 장에 같은 수치가 반복됨",
        occurrences=(occurrence_a, occurrence_b),
    )


def test_중복이_있어도_출고가_막히지_않는다(monkeypatch: pytest.MonkeyPatch):
    """dup_detect가 확정급 중복을 잡아도 run_v2는 예외 없이 끝난다."""
    monkeypatch.setattr(
        pipeline_module,
        "find_numeric_duplicates",
        lambda rendered: (_fake_confirmed_finding(),),
    )
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
    )

    assert isinstance(output, V2RunOutput)  # 예외 없이 출고까지 끝났다


def test_중복_경고가_로그로_남는다(monkeypatch: pytest.MonkeyPatch, caplog):
    """dup_detect가 잡은 결과가 WARNING 로그 한 줄로 남는다 — 예외가 아니다."""
    monkeypatch.setattr(
        pipeline_module,
        "find_numeric_duplicates",
        lambda rendered: (_fake_confirmed_finding(),),
    )
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
        )

    assert "경고 전용" in caplog.text
    assert "확정 1건" in caplog.text
    assert "2장 사업모델" in caplog.text and "4장 재무" in caplog.text


def test_중복이_없으면_경고를_남기지_않는다(caplog):
    """정상 흐름(숫자 없는 가짜 본문)에서는 중복 경고가 아예 안 남는다."""
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
        )

    assert "중복 검출" not in caplog.text


def test_잘못된_연평균_AI문장은_최종_Report에서_빠지고_누적claim만_남는다():
    fragments = _raw_fragments()
    fragments[9] = {
        "종류": "재무", "원문": "주요계정(DART API): 매출액 1,242,800,000,000",
    }

    class Writer(_FakeWriter):
        def __call__(self, prompt: str) -> str:
            payload = json.loads(super().__call__(prompt))
            if section_id_in_prompt(prompt) == "past_changes":
                payload["문장들"].append({
                    "글": "2년 누적 24.28%를 연평균 25% 이상으로 해석할 수 있다.",
                    "인용": ["9"], "등급": GRADE_INTERPRETED,
                    "주장슬롯": "past_changes:historical_performance",
                })
            return json.dumps(payload, ensure_ascii=False)

    output = run_v2(
        "가나다전자",
        fragments,
        _structured_financial_table(),
        writer_ask=Writer(),
        reviewer_ask=_FakeReviewer(),
        grade=Grade.COMPLETE,
        as_of_date="2026-08-28",
        filing_meta=FilingMeta(
            document_id="20260828000123",
            title="사업보고서",
            disclosed_at="2026-03-20",
        ),
    )

    public_text = " ".join(
        [
            text
            for section in output.report.sections
            for text, _citation in section.prose_lines
        ]
        + [item.text for item in output.report.summary_items]
    )
    assert "연평균 25%" not in public_text
    assert "누적 증감률은 24.28%" in public_text
    rates = [fact for fact in output.report.fact_records if fact.formula == "rate"]
    assert len(rates) == 2
    assert all("연평균" not in fact.claim for fact in output.report.fact_records)
    assert output.report.grade is Grade.PARTIAL
    assert any(
        "숫자·날짜 문장" in reason
        for reason in output.report.shortfall_reasons
    )


def test_한문장_장이_있으면_COMPLETE가_아니라_PARTIAL과_이유가_나온다():
    class OneSentenceWriter(_FakeWriter):
        def __call__(self, prompt):
            payload = json.loads(super().__call__(prompt))
            payload["문장들"] = payload["문장들"][:1]
            return json.dumps(payload, ensure_ascii=False)


    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=OneSentenceWriter(),
        reviewer_ask=_FakeReviewer(),
        grade=Grade.COMPLETE,
    )

    assert output.report.grade is Grade.PARTIAL
    assert "identity" in output.quality_observation.underfilled_sections
    assert "low_public_sentence_coverage" in (
        output.quality_observation.quality_problem_codes
    )
    # 원문 결속된 사실이 한 문장뿐인 장은 실제로 1건으로 측정한다.
    identity = next(
        section for section in output.report.sections if section.cell == "identity"
    )
    assert len(identity.prose_lines) == 1
    assert any(
        "확인된 문장이 1개뿐이라 내용이 얇습니다" in reason
        for reason in output.report.shortfall_reasons
    )


def test_생성지표는_등록부전용_source가_아니라_실제_인용조각만_센다() -> None:
    fragments = (
        CollectedFragment(fragment_id="1", kind="공시", text="첫 번째 원문"),
        CollectedFragment(fragment_id="2", kind="공시", text="두 번째 원문"),
    )
    rendered = Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        citations=[
            Source(number=1, kind=SourceKind.FILING, label="첫 문서"),
            Source(number=2, kind=SourceKind.FILING, label="둘째 문서"),
            # 공식 웹 소유권 증명 Source는 최종 등록부에는 필요하지만 인용
            # 조각이 아니다. 이 한 건 때문에 fragments_cited가 분모보다
            # 커지던 비교 bridge 회귀를 직접 고정한다.
            Source(
                number=99,
                kind=SourceKind.FILING,
                label="법인 소유권 증명",
                provenance_role="attestation_only",
            ),
            # 프로그램 등록부에만 남은 미사용 citation Source도 조각이 없으면
            # 인용 수에 들어갈 수 없다.
            Source(number=100, kind=SourceKind.FILING, label="미사용 후보"),
        ],
    )

    assert pipeline_module._generation_fragment_counts(  # noqa: SLF001
        fragments,
        rendered,
    ) == (2, 2)


# ══════════════════════════════════════════════════════════
# 핵심 요약이 본문 «첫 문장»을 축자 복제하지 않는다 (운영 진입점 배선)
#
# ★ 실측 재현 조건 — 작가 한도에 닿아 요약 «초안이 0건»이면 보충 경로만 남는다.
#   그 경로가 장마다 «첫» 문장부터 집어서, 요약 3건이 본문 2장·3장·1장의 첫
#   문장과 축자 동일해졌다. 아래 두 시험은 시험 안에서 따로 만든 경로가 아니라
#   운영 진입점 `_legacy_summary_stage` 를 그대로 호출한다.
# ══════════════════════════════════════════════════════════

_요약_최소 = 3


def _요약용_문장(text: str):
    """verify_report를 이미 통과한 본문 «확인» 문장 — 인용 1개·검수 표식 있음.

    ★ 2026-09-23부터 요약 잣대(`is_release_ready_summary_sentence`)는 숫자
      유무와 무관하게 «검수 통과 표식 + 인용»을 요구한다(운영 PDF 27e9f03
      표지 04에 인용 0개·미검수 해석이 실린 실측). 그래서 이 재료는 항상
      verified다 — 예전의 `numeric_verified` 스위치는 숫자 문장만 표식을
      달아 주던 것이라 지금 잣대에서는 뜻이 없다.
    """
    from src.features.composer.port import ComposedSentence

    return ComposedSentence(
        text=text,
        citations=("1",),
        grade=GRADE_CONFIRMED,
        verification_state="verified",
    )


def _요약용_본문(*, identity_sentences):
    from src.features.composer.port import ComposedReport, ComposedSection

    sections = []
    for section_id in SECTION_IDS:
        if section_id == "identity":
            문장들 = identity_sentences
        else:
            문장들 = (
                _요약용_문장(f"{section_id} 첫 문장이다."),
                _요약용_문장(f"{section_id} 둘째 문장이다."),
            )
        sections.append(ComposedSection(section_id=section_id, sentences=문장들))
    return ComposedReport(sections=tuple(sections))


def _한도에_닿은_작가():
    def writer_ask(_prompt: str) -> str:
        raise AskFatalError(RuntimeError("호출 횟수 상한"), call_limit=True)

    return writer_ask


#: ★ 검수 호출자를 넘길 자리가 «구조적으로» 없다 (2026-09-11). 예전에는
#:   `_불려서는_안_되는_검수()`를 넘겨 「요약 검수는 안 불린다」를 지켰는데,
#:   이제 `_legacy_summary_stage`에 reviewer 매개변수 자체가 없어서 부를 방법이
#:   없다. 「한 번도 안 부른다」는 정의 모듈 spy로
#:   `test_legacy_summary_diagnostics.py`가 못 박는다.


def test_작성한도에_닿아도_요약이_본문_첫문장_서명을_만들지_않는다() -> None:
    from src.features.composer.pipeline import _legacy_summary_stage
    from src.features.composer.structured_claims import NumericSafetyFiltering

    verified = _요약용_본문(identity_sentences=(
        _요약용_문장("identity 첫 문장이다."),
        _요약용_문장("identity 둘째 문장이다."),
    ))
    첫문장_서명 = [
        section.sentences[0].text for section in verified.sections[:_요약_최소]
    ]

    final, draft_count, _filtering = _legacy_summary_stage(
        verified,
        writer_ask=_한도에_닿은_작가(),
        body_numeric_filtering=NumericSafetyFiltering(),
    )

    texts = [sentence.text for sentence in final.summary]
    assert draft_count == 0, "이 시험의 전제 — 작가가 요약 초안을 못 냈다"
    assert len(texts) == _요약_최소
    assert texts != 첫문장_서명, f"요약이 본문 첫 문장 서명 그대로다: {texts}"
    assert not (set(texts) & set(첫문장_서명))


def test_수치_안전_검사가_뺀_문장은_요약_보충으로_되돌아오지_않는다() -> None:
    """★ 인접 결함 — 뺀 문장을 보충이 그대로 되돌려 넣고 있었다.

    실측 단계 기록: 수치검사후수 2 → 최종수 3이고, 되돌아온 그 한 문장이 바로
    수치 검사가 뺀 문장이었다. 수치 검사는 그 뒤로 다시 돌지 않는다.

    ★ 의도가 강해진 근거 (2026-09-11) — 이제 이 잣대를 요약을 다 만든 «뒤»가
      아니라 «후보 단계»에서 먼저 건다. 그래서 지키는 것이 「뺀 문장이
      돌아오지 않는다」에서 「애초에 들어가지 않아 뺄 일이 없다」로 바뀌었다.
      전제 단정도 그에 맞춰 뒤집는다 — 요약에서 «뺀 개수»가 0이어야 한다.
      이 숫자가 1 이상으로 돌아오면 후보 거르기가 끊겼다는 뜻이다.
    """
    from src.features.composer.logic import summary_candidates
    from src.features.composer.pipeline import _legacy_summary_stage
    from src.features.composer.structured_claims import (
        NumericSafetyFiltering, has_public_numeric_token,
        is_release_ready_summary_sentence, safe_numeric_owners_by_fact_id,
    )

    수치_문장 = _요약용_문장(
        "설립일은 1997년 4월 25일이고 상장일은 2001년 11월 21일이다.",
    )
    assert has_public_numeric_token(수치_문장.text), "이 시험의 전제 — 공개 숫자 문장"
    # 장에 문장이 하나뿐이라, 거르기가 끊기면 보충이 «반드시» 이 문장을 집는다.
    verified = _요약용_본문(identity_sentences=(수치_문장,))

    # 전제 — 이 문장은 요약 잣대를 못 넘고, 그래서 후보 목록에 없다.
    소유장 = safe_numeric_owners_by_fact_id(verified.sections)
    assert not is_release_ready_summary_sentence(
        수치_문장, safe_owner_by_fact_id=소유장
    ), "이 시험의 전제 — 요약 잣대를 못 넘는 문장이다"
    후보 = summary_candidates(
        verified,
        accept=lambda sentence: is_release_ready_summary_sentence(
            sentence, safe_owner_by_fact_id=소유장
        ),
    )
    assert 수치_문장.text not in [c.sentence.text for c in 후보], (
        "요약 잣대를 못 넘는 문장이 후보로 나갔다"
    )

    final, _draft_count, filtering = _legacy_summary_stage(
        verified,
        writer_ask=_한도에_닿은_작가(),
        body_numeric_filtering=NumericSafetyFiltering(),
    )

    texts = [sentence.text for sentence in final.summary]
    assert filtering.removed_summary_count == 0, (
        "요약에서 뺀 문장이 있다 — 후보를 미리 거르지 못했다는 뜻이다"
    )
    assert 수치_문장.text not in texts, "뺀 문장이 보충으로 되돌아왔다"
    assert len(texts) == _요약_최소, "제외 때문에 요약이 짧아지면 안 된다"


def test_보충으로_채운_요약을_다시_검사해도_아무것도_빠지지_않는다() -> None:
    """★ 잔여 구멍 — «뺀 그 문장»만 막고 «같은 이유로 빠졌어야 할 다른 문장»은 통과.

    독립 검토가 운영 진입점에서 재현한 모양 그대로다: 아홉 장이 각각
    「깨끗한 문장 + 공개숫자·증명서 없는 문장」을 가지면, 예전에는 최종 요약
    3건이 «전부» 수치 안전 검사를 다시 걸면 빠지는 문장이 됐다.

    원인은 본문 잣대와 요약 잣대가 다르다는 것이다 — 본문은 「검수 통과 표식 +
    확인 등급 + 인용」만으로 남지만, 요약은 구조화 사실과 소유 장 일치를
    요구한다. 보충이 요약 잣대를 다시 받지 않았다.
    """
    from src.features.composer.pipeline import _legacy_summary_stage
    from src.features.composer.structured_claims import (
        NumericSafetyFiltering, enforce_public_numeric_safety,
        has_public_numeric_token,
    )

    def 본문():
        from src.features.composer.port import ComposedReport, ComposedSection

        sections = []
        for index, section_id in enumerate(SECTION_IDS):
            sections.append(ComposedSection(section_id=section_id, sentences=(
                _요약용_문장(f"{section_id} 첫 문장이다."),
                _요약용_문장(
                    f"{section_id} 매출은 {100 + index}억원이다.",
                ),
            )))
        return ComposedReport(sections=tuple(sections))

    verified = 본문()
    # 이 시험의 전제 — 둘째 문장은 본문 잣대는 통과하지만 요약 잣대는 못 넘는다.
    assert has_public_numeric_token(verified.sections[0].sentences[1].text)

    final, _draft, _filtering = _legacy_summary_stage(
        verified,
        writer_ask=_한도에_닿은_작가(),
        body_numeric_filtering=NumericSafetyFiltering(),
    )

    texts = [sentence.text for sentence in final.summary]
    assert len(texts) == _요약_최소

    재검사, _ = enforce_public_numeric_safety(final)
    남은것 = [sentence.text for sentence in 재검사.summary]
    assert 남은것 == texts, (
        f"최종 요약에 같은 검사를 다시 걸면 빠지는 문장이 있다: "
        f"{[t for t in texts if t not in 남은것]}"
    )


def test_보충이_요약_잣대를_놓치면_뒷문이_그_문장을_뺀다(monkeypatch) -> None:
    """★ fail-closed 뒷문에도 지켜 주는 시험을 둔다.

    보충 술어와 요약 잣대는 «같은 재료»를 쓰므로 정상적으로는 뒷문이 아무것도
    빼지 않는다. 그래서 두 잣대가 앞으로 갈라지는 상황을 «주입»해서, 그때
    조용히 새는 대신 여기서 빠지는지 확인한다. 진입점은 그대로
    `_legacy_summary_stage`이고, 고장 낸 것은 보충 함수 하나뿐이다.
    """
    from src.features.composer import pipeline as pipeline_under_test
    from src.features.composer.structured_claims import NumericSafetyFiltering

    새는_문장 = _요약용_문장(
        "identity 매출은 999억원이다.",
    )
    verified = _요약용_본문(identity_sentences=(
        _요약용_문장("identity 첫 문장이다."),
        새는_문장,
    ))

    def 잣대를_무시하는_보충(summary, report, **_kwargs):
        return tuple(summary) + (새는_문장,)

    monkeypatch.setattr(
        pipeline_under_test, "_supplement_safe_summary", 잣대를_무시하는_보충
    )

    final, _draft, filtering = pipeline_under_test._legacy_summary_stage(
        verified,
        writer_ask=_한도에_닿은_작가(),
        body_numeric_filtering=NumericSafetyFiltering(),
    )

    texts = [sentence.text for sentence in final.summary]
    assert 새는_문장.text not in texts, "요약 잣대를 못 넘는 문장이 뒷문을 지나갔다"
    assert filtering.removed_summary_count >= 1
