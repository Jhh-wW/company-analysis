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
    """장 하나 응답 — «확인» 1문장(조각 1) + «해석» 1문장(조각 2).

    ★ 8장 해석 문장만 조각 2의 낱말(고객 존중·핵심 가치)을 그대로 쓴다. 8장
      원문 절 계약이 «후보가 기댄 절»을 판정 재료로 쓰기 때문에, 인용한 원문과
      낱말이 하나도 겹치지 않는 문장은 어느 절에 기댔는지 가릴 수 없어 그 장에서
      빠진다. 실물 작가는 인용한 원문의 명사를 그대로 옮겨 적는다.
    ★ 아홉 장 «전부»를 그렇게 바꾸지 않는 이유도 실측이다 — 같은 긴 구절을 아홉
      장이 공유하면 장 간 중복 제거가 여덟 장을 지운다(생존 13 → 5). 이 장만
      바꾼다.
    """
    문화장 = mark == _CULTURE_MARK
    return json.dumps(
        {
            "문장들": [
                {
                    "글": f"{mark} 장: 가나다전자는 반도체 검사 장비 전문기업이다.",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                },
                {
                    "글": (
                        f"{mark} 장: 고객 존중을 핵심 가치로 삼는다는 설명이다."
                        if 문화장
                        else f"{mark} 장의 해석 서술이다."
                    ),
                    "인용": ["2"],
                    "등급": GRADE_INTERPRETED,
                },
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
    """장마다 «다른» 해석 문장 하나 — 요약 후보를 3개 이상 만드는 재료.

    ★ 왜 필요한가 (2026-09-11) — 여러 시험의 가짜 작가가 아홉 장에 «같은»
      확인 문장을 써 왔다. 같은 사실은 소유 장 하나로 모이므로 본문에 결국
      1문장만 남는데, 예전에는 그래도 요약이 3문장이었다 — AI가 본문에 없는
      문장을 «새로 썼기» 때문이다. 요약이 「검증된 본문 문장 중 고르기」로
      바뀐 뒤로는 후보 1개로 3문장을 만들 수 없고, 그러면 출고 검증
      (요약 3~5문장)이 그 시험의 주제와 무관한 이유로 실행을 막는다.
      그래서 «장마다 다른» 문장을 하나씩 더해 후보를 만든다.
    ★ 인용은 조각 1 그대로다 — 부록 번호 계약을 건드리지 않기 위해서다.

    Returns:
        장을 못 가리면 None (부르는 쪽이 아무것도 더하지 않는다).
    """

    section_id = section_id_in_prompt(prompt)
    if not section_id:
        return None
    return {
        "글": f"{section_id} 장의 해석 서술이다.",
        "인용": ["1"],
        "등급": GRADE_INTERPRETED,
    }


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
    # ★ 여기서 지키는 것은 «내부 사유가 그대로 저장되는가»다.
    #   2026-09-05 사용자 결정으로 이 문장들은 웹·PDF·노션 «독자 화면»에서
    #   빠졌지만, 생산은 그대로 유지한다 — 저장본·관리자 화면·진단이 읽는
    #   자료이기 때문이다. 독자 채널에 안 나오는지는
    #   `web/tests/test_three_channels_share_sealed_blocks.py`와
    #   `web/tests/test_three_forms_match.py`가 같은 금지어 목록
    #   (`web/tests/_reader_notice_ban.py`)으로 따로 지킨다.
    #   ⚠️ 여기서 지키는 것은 «문구»가 아니라 «사유 기록이 사라지지 않았는가»다.
    assert any(
        "아직 하나씩 확인하지 못했습니다" in reason
        for reason in report.shortfall_reasons
    ), "★ 「아직 다 확인하지 못했다」는 내부 사유 기록이 사라졌다"
    assert any(
        "원문을 함께 확인해 주세요" in reason
        for reason in report.shortfall_reasons
    ), "★ 무엇을 하면 되는지를 적은 내부 사유 기록이 사라졌다"
    assert any(
        "fact_id와 결속되지 않은 공개 내용" in problem
        for problem in output.quality_observation.safety_problems
    )
    # 부록은 인용된 조각(1·2)만, 번호는 조각 번호 그대로
    assert sorted(source.number for source in report.citations) == [1, 2]


def test_SHADOW에서_release_allowed_False여도_Outcome·차감·화면은_불변이다():
    """`report.quality_observation`을 채워도 실제 판정 결과는 그대로다.

    이 시험이 쓰는 fixture는 위 `test_정상_흐름이면_검증된_v2_Report가_나온다`와
    같은 입력으로 `release_allowed=False`를 낸다. `pipeline/real.py`의
    Outcome·차감(`charged`) 결정은 이 시험의 소유 밖이지만, 그 두 결정을
    감시하는 검사(`real.py:2021-2026`·`:2102-2109`)가 둘 다
    `release_mode in {ENFORCE_NO_PARTIAL, FULL}`로만 게이트돼 있어 SHADOW는
    애초에 `quality_observation` 유무를 보지 않는다(정적 확인, 이 변경의 소유 밖이라
    real.py에는 새 시험을 만들지 않았다). 이 시험은 composer 경계에서
    증명 가능한 것만 본다 — `quality_observation`이 채워져도 v2 정본 판정
    (grade·safety_decision·publication_policy·본문)은 이 필드가 비어 있던
    예전 동작과 완전히 같은 값이라는 것.
    """
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

    # 이 fixture는 품질 하한 미달로 release_allowed=False를 낸다 — 그런데도
    # REPORT 자체는 여전히 나온다(예외 없음, 게이트로 안 막힘).
    assert output.quality_observation.release_allowed is False
    assert output.report.grade is Grade.PARTIAL
    assert output.report.sections  # 본문이 비지 않았다

    # 이 변경이 새로 채우는 필드: report.quality_observation은 새로 판정한 값이
    # 아니라 V2RunOutput이 이미 계산해 둔 것과 «완전히 같은» 값이다.
    assert output.report.quality_observation == output.quality_observation
    assert output.report.quality_observation.release_allowed is False

    # release_allowed=False가 판정 자체를 바꾸지 않는다는 증거: 다른 판정
    # 필드는 quality_observation이 채워지기 전과 여전히 같은 값이다.
    assert output.report.safety_decision == "공개 차단"
    assert output.report.publication_policy == "legacy-shadow-exception-v1"
    assert (
        output.report.quality_contract_version
        == output.quality_observation.contract_version
    )


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

    # 작가: 장 9회 + 요약 고르기 1회. 판정 프롬프트는 한 번도 받지 않는다.
    assert len(writer.prompts) == 10
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
    """작가가 «글» 안에 [2]처럼 대괄호 인용을 흉내내도(critical 결함) 파싱
    단계에서 걷어내, 가짜 인용-부록 불일치로 GATE_STOPPED에 빠지지 않는다."""

    def writer(prompt: str) -> str:
        if "핵심 요약" in prompt:
            return _summary_selection_json(prompt)
        mark = _SECTION_MARKS[writer.calls % len(_SECTION_MARKS)]
        writer.calls += 1
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": f"{mark} 장: 가나다전자는 반도체 [2] 검사 장비 전문기업이다.",
                        "인용": ["1"],
                        "등급": GRADE_CONFIRMED,
                    },
                    # ★ 장마다 다른 «해석» 한 문장을 더 둔다 (2026-09-11). 위
                    #   확인 문장은 장마다 같은 사실이라 소유 장 하나로 모여
                    #   본문에 1문장만 남는다. 요약이 「검증된 본문 문장 중에서
                    #   고르기」로 바뀐 뒤로는 후보가 1개면 3문장을 못 채워
                    #   출고 검증에서 막힌다 — 이 시험의 주제(대괄호 흉내 제거)
                    #   와 무관한 이유로 죽지 않게 재료만 늘린다.
                    #   인용은 조각 1 그대로라 부록 번호 계약은 바뀌지 않는다.
                    {
                        "글": f"{mark} 장의 해석 서술이다.",
                        "인용": ["1"],
                        "등급": GRADE_INTERPRETED,
                    },
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

    # 초안: 9장 × 2문장 + 요약 3문장 = 21.
    # 같은 전문기업 사실을 반복한 확인 문장은 소유 장 하나로 모이므로
    # 확인 본문 1 + 해석 본문 9 + 요약 3 = 13문장이 남는다.
    assert output.composed_sentences == 21
    assert output.verified_sentences == 13
    assert sum(len(section.prose_lines) for section in output.report.sections) == 10
    assert len(output.report.summary_items) == 3


# ══════════════════════════════════════════════════════════
# ③ fail-closed — 빈 본문은 출고 검증에서 막힌다
# ══════════════════════════════════════════════════════════


def test_요약이_호출상한이면_본문을_버리지_않고_보고서를_낸다():
    """★ 실측 — 요약 호출 하나가 완성된 9개 장을 통째로 버렸다.

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
    한문장 = json.dumps(
        {
            "문장들": [
                {
                    "글": "가나다전자는 반도체 검사 장비 전문기업이다.",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                }
            ]
        },
        ensure_ascii=False,
    )
    writer = _FakeWriter(section_response=한문장)
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
            # ⚠️ 문장에 숫자를 넣지 않는다 — 구조화 결속 없는 숫자 문장은
            #   수치 안전 검사가 본문에서 빼 버려, 이 시험이 재려는 「두 장에
            #   후보 4개」가 아니라 「후보 0개」가 된다(실측으로 확인).
            return json.dumps(
                {
                    "문장들": [
                        {
                            "글": f"{section_id} 장의 {차례} 해석 서술이다.",
                            "인용": ["1"],
                            "등급": GRADE_INTERPRETED,
                        }
                        for 차례 in ("첫", "둘째")
                    ]
                },
                ensure_ascii=False,
            )

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
                return _summary_selection_json(prompt)
            mark = _SECTION_MARKS[self.section_calls]
            # ★ 첫 장만 «확인» 문장을 쓰고 나머지는 장마다 다른 «해석» 한
            #   문장을 쓴다 (2026-09-11). 예전에는 아홉 장이 모두 같은 확인
            #   문장이라 소유 장 하나로 모여 본문에 1문장만 남았고, 그래도
            #   요약은 AI가 3문장을 «지어내» 채웠다. 요약이 「검증된 본문
            #   문장 중에서 고르기」로 바뀐 뒤로는 후보 1개로 3문장을 만들 수
            #   없다 — 지어내지 않는다는 것이 이 설계의 요점이다. 장마다
            #   «한 문장»이라는 이 시험의 주제는 그대로다.
            첫_장 = self.section_calls == 0
            self.section_calls += 1
            문장 = (
                {
                    "글": f"{mark} 장: 가나다전자는 반도체 검사 장비 전문기업이다.",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                }
                if 첫_장
                else {
                    "글": f"{mark} 장의 해석 서술이다.",
                    "인용": ["1"],
                    "등급": GRADE_INTERPRETED,
                }
            )
            return json.dumps({"문장들": [문장]}, ensure_ascii=False)

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
    # 작가는 한 문장을 만들었지만 이 SHADOW fixture에는 원자 FactRecord와
    # 원문 결속이 없다. 이를 «확인된 1문장»으로 세는 것이 기존 결함이므로,
    # 공개 문장 하한은 0건으로 정직하게 표시한다. 임계값을 낮춘 변경이 아니다.
    identity = next(
        section for section in output.report.sections if section.cell == "identity"
    )
    assert len(identity.prose_lines) == 1
    assert any(
        "확인된 문장이 0개뿐이라 내용이 얇습니다" in reason
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


def _요약용_문장(text: str, *, numeric_verified: bool = False):
    from src.features.composer.port import ComposedSentence

    return ComposedSentence(
        text=text,
        citations=("1",),
        grade=GRADE_CONFIRMED,
        verification_state="verified" if numeric_verified else "unverified",
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
        numeric_verified=True,
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
                    numeric_verified=True,
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
        "identity 매출은 999억원이다.", numeric_verified=True
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
