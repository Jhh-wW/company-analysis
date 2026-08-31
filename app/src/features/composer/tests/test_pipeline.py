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
     만들기 어렵고(2026-08-25 실측), 이 시험이 지켜야 할 것은 «잡히면
     막지 않는다»는 배선이지 dup_detect 판정 정확도(그건 test_dup_detect.py
     몫)가 아니기 때문이다.
"""

from __future__ import annotations

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
from src.features.pipeline.port import Grade
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


def _raw_fragments() -> dict[int, dict[str, str]]:
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비 전문기업이다."},
        2: {
            "종류": "홈페이지",
            "원문": "고객 존중을 핵심 가치로 삼는다.",
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

    fragments = tuple(
        CollectedFragment(
            fragment_id=str(number),
            kind=str(raw["종류"]),
            text=" ".join((str(raw["원문"]), *evidence_texts)).strip(),
            source_url=str(raw["출처"]),
            document_title=str(raw["문서명"]),
            document_identity=document_identity_from_parts(url=str(raw["출처"])),
        )
        for number, raw in _strict_fragments().items()
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
                fragments=fragments,
            )
            for section_id in SECTION_IDS
        ),
    )


def _section_json(mark: str) -> str:
    """장 하나 응답 — «확인» 1문장(조각 1) + «해석» 1문장(조각 2)."""
    return json.dumps(
        {
            "문장들": [
                {
                    "글": f"{mark} 장의 확인 사실 서술이다.",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                },
                {
                    "글": f"{mark} 장의 해석 서술이다.",
                    "인용": ["2"],
                    "등급": GRADE_INTERPRETED,
                },
            ]
        },
        ensure_ascii=False,
    )


_SUMMARY_TEXTS = ["요약 첫 문장이다.", "요약 둘째 문장이다.", "요약 셋째 문장이다."]


def _summary_json() -> str:
    return json.dumps(
        {
            "문장들": [
                {"글": text, "인용": ["1"], "등급": GRADE_CONFIRMED}
                for text in _SUMMARY_TEXTS
            ]
        },
        ensure_ascii=False,
    )


class _FakeWriter:
    """작성 프롬프트만 받아 장·요약 JSON을 돌려주는 가짜 작가."""

    def __init__(self, section_response=None):
        self.prompts: list[str] = []
        self.section_calls = 0
        self._section_response = section_response

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "핵심 요약" in prompt:
            return _summary_json()
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
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        corp_type="상장사",
        as_of_date="2026-08-24",
    )

    assert isinstance(output, V2RunOutput)
    report = output.report
    # v2 스키마 + 9장 전부 (장 삭제 없음, v3 정본 순서)
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert [section.cell for section in report.sections] == list(SECTION_IDS)
    assert all(section.prose_lines for section in report.sections)
    # 핵심 요약 3~5문장 — validate_v2를 통과했다는 뜻이다 (예외 없음)
    assert len(report.summary_items) == 3
    assert report.corp_type == "상장사"
    assert report.grade is Grade.PARTIAL
    # v2에는 아직 원자 claim 장부가 없다. shadow assessor는 이를 가짜 fact로
    # 통과시키지 않고 공개 차단/미완성으로 정직하게 측정한다.
    assert report.fact_records == []
    assert output.quality_observation.mode == "generation-shadow"
    assert output.quality_observation.safety_decision == "공개 차단"
    assert output.quality_observation.publication_grade == "미완성"
    assert output.quality_observation.release_allowed is False
    assert report.quality_contract_version == output.quality_observation.contract_version
    assert report.safety_decision == "공개 차단"
    assert report.publication_policy == "legacy-shadow-exception-v1"
    # ★ 2026-08-29 — 이 줄의 문구를 바꿨다. 옛 문구는 「«새 안전 검사»에서 …
    #   «새 구조로» 검증하는 작업은 아직 끝나지 않았습니다」로, 우리가 검증
    #   방식을 바꾸는 중이라는 «우리 사정»이었고 바로 위 제목 줄과 겹쳤다.
    #   지금은 제목이 말하지 않는 것 — «독자가 무엇을 하면 되는지» — 를 담는다.
    #   ⚠️ 지키는 것은 «문구»가 아니라 «고지가 사라지지 않았는가»다.
    assert any(
        "아직 하나씩 확인하지 못했습니다" in reason
        for reason in report.shortfall_reasons
    ), "★ 「아직 다 확인하지 못했다」는 고지가 사라졌다"
    assert any(
        "원문을 함께 확인해 주세요" in reason
        for reason in report.shortfall_reasons
    ), "★ 독자가 무엇을 하면 되는지가 사라졌다"
    assert any(
        "fact_id와 결속되지 않은 공개 내용" in problem
        for problem in output.quality_observation.safety_problems
    )
    # 부록은 인용된 조각(1·2)만, 번호는 조각 번호 그대로
    assert sorted(source.number for source in report.citations) == [1, 2]


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

    # 작가: 장 9회 + 요약 1회. 판정 프롬프트는 한 번도 받지 않는다.
    assert len(writer.prompts) == 10
    assert not any("판정" in prompt for prompt in writer.prompts)
    # 검수: 본문 1회 + 요약 1회. 작성 프롬프트는 한 번도 받지 않는다.
    assert len(reviewer.prompts) == 2
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
    if expects_quality_codes:
        # 엄격 경로만 숫자 하한 코드를 함께 싣는다(task 022).
        assert "too_few_substantive_claims" in caught.value.problem_codes
    else:
        # FULL은 회복 정책이 먼저 닫는다. 품질 코드를 지어내지 않고,
        # 사유는 사람 원문 없이 닫힌 report_recovery 코드 하나다.
        assert caught.value.problem_codes == ()
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
                            "인용": [str((section_index * 5 + index) % 8 + 1)],
                            "등급": GRADE_CONFIRMED,
                            "주장슬롯": slots[index],
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
    assert len(output.report.fact_records) == 45
    assert len(output.report.summary_items) == 5
    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1
    assert not any("핵심 요약" in prompt for prompt in writer.prompts)


def test_인라인_대괄호_인용_흉내는_출고검증을_막지_않는다():
    """작가가 «글» 안에 [2]처럼 대괄호 인용을 흉내내도(critical 결함) 파싱
    단계에서 걷어내, 가짜 인용-부록 불일치로 GATE_STOPPED에 빠지지 않는다."""

    def writer(prompt: str) -> str:
        if "핵심 요약" in prompt:
            return _summary_json()
        mark = _SECTION_MARKS[writer.calls % len(_SECTION_MARKS)]
        writer.calls += 1
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": f"{mark} 장은 자료 [2]에서 밝힌 대로 성장했다.",
                        "인용": ["1"],
                        "등급": GRADE_CONFIRMED,
                    }
                ]
            },
            ensure_ascii=False,
        )

    writer.calls = 0
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
    )  # V2ValidationError 없이 끝나야 한다

    report = output.report
    for section in report.sections:
        for text, _cite in section.prose_lines:
            assert "[2]" not in text  # 흉내낸 번호가 텍스트에 남지 않는다
    # 실제로 인용된 조각(1)만 부록에 실린다 — 흉내낸 [2]로 가짜 인용이 붙지 않는다
    assert sorted(source.number for source in report.citations) == [1]


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

    # 초안: 9장 × 2문장 + 요약 3문장 = 21. 전부 통과했으므로 생존도 21.
    assert output.composed_sentences == 21
    assert output.verified_sentences == 21


# ══════════════════════════════════════════════════════════
# ③ fail-closed — 빈 본문은 출고 검증에서 막힌다
# ══════════════════════════════════════════════════════════


def test_요약이_호출상한이면_본문을_버리지_않고_보고서를_낸다():
    """★ 2026-08-29 실측 — 요약 호출 하나가 완성된 9개 장을 통째로 버렸다.

    요약은 «이미 검증된» 본문 확인 문장으로 채울 길이 있고 그 길은 AI 를
    한 번도 부르지 않는다. 그러니 본문을 버릴 이유가 없다.
    """

    class _요약에서_한도(_FakeWriter):
        def __call__(self, prompt: str) -> str:
            if "핵심 요약" in prompt:
                raise AskFatalError(RuntimeError("한도"), call_limit=True)
            return super().__call__(prompt)

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=_요약에서_한도(),
        reviewer_ask=_FakeReviewer(),
    )

    report = output.report
    assert [section.cell for section in report.sections] == list(SECTION_IDS)
    assert all(section.prose_lines for section in report.sections), (
        "★ 본문이 사라지면 안 된다"
    )
    assert report.summary_items, "★ 요약은 본문 확인 문장으로 채워져야 한다"


def test_요약이_돈문제면_여전히_요청_전체가_멈춘다():
    """★ 안전선 — 예산 소진을 「요약만 대체」로 숨기지 않는다."""

    class _요약에서_예산소진(_FakeWriter):
        def __call__(self, prompt: str) -> str:
            if "핵심 요약" in prompt:
                raise AskFatalError(RuntimeError("예산"))
            return super().__call__(prompt)

    with pytest.raises(AskFatalError):
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=_요약에서_예산소진(),
            reviewer_ask=_FakeReviewer(),
        )


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
    fragments = {
        9: {
            "종류": "재무",
            "원문": "주요계정(DART API): 매출액 1,242,800,000,000",
        }
    }

    class Writer:
        def __init__(self) -> None:
            self.section_calls = 0

        def __call__(self, prompt: str) -> str:
            if "핵심 요약" in prompt:
                return json.dumps(
                    {
                        "문장들": [
                            {
                                "글": "연평균 성장률은 25% 이상이다.",
                                "인용": ["9"],
                                "등급": GRADE_INTERPRETED,
                            },
                            {
                                "글": "공식 자료에서 사업 변화가 확인된다.",
                                "인용": ["9"],
                                "등급": GRADE_CONFIRMED,
                            },
                            {
                                "글": "변화의 배경은 추가 확인이 필요하다.",
                                "인용": ["9"],
                                "등급": GRADE_CONFIRMED,
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            mark = _SECTION_MARKS[self.section_calls]
            is_past = self.section_calls == 3
            self.section_calls += 1
            sentences = [
                {
                    "글": f"{mark} 장에서 확인한 회사 사실이다.",
                    "인용": ["9"],
                    "등급": GRADE_CONFIRMED,
                },
                {
                    "글": (
                        "2년 누적 24.28%를 연평균 25% 이상으로 해석할 수 있다."
                        if is_past
                        else f"{mark} 장의 자료가 보여 주는 의미다."
                    ),
                    "인용": ["9"],
                    "등급": GRADE_INTERPRETED,
                },
            ]
            return json.dumps({"문장들": sentences}, ensure_ascii=False)

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
    assert len(output.report.fact_records) == 2
    assert all(fact.formula == "rate" for fact in output.report.fact_records)
    assert output.report.grade is Grade.PARTIAL
    assert any(
        "숫자·날짜 문장" in reason
        for reason in output.report.shortfall_reasons
    )


def test_한문장_장이_있으면_COMPLETE가_아니라_PARTIAL과_이유가_나온다():
    class OneSentenceWriter:
        def __init__(self) -> None:
            self.section_calls = 0

        def __call__(self, prompt: str) -> str:
            if "핵심 요약" in prompt:
                return _summary_json()
            mark = _SECTION_MARKS[self.section_calls]
            self.section_calls += 1
            return json.dumps(
                {
                    "문장들": [
                        {
                            "글": f"{mark} 장에서 확인한 회사의 고유 사실이다.",
                            "인용": ["1"],
                            "등급": GRADE_CONFIRMED,
                        }
                    ]
                },
                ensure_ascii=False,
            )

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
    assert any(
        "확인된 문장이 1개뿐이라 내용이 얇습니다" in reason
        for reason in output.report.shortfall_reasons
    )
