# -*- coding: utf-8 -*-
"""«확인» 산문의 자기 원문 근거 검사 — 본문·요약·진단·빈 장 안내가 같은 판정을 본다.

출발점은 실제 실행에서 남은 세 자리다(문장·원문은 시험 안에 그대로 적어 둔다;
바깥 artifact 파일에 기대지 않는다).
  · F1 business_model — 「…라이선싱 수익이 회사의 주요 수익원이다」
        자기 원문은 «언제 수익으로 인식하는가»만 말한다.
  · F3 current_challenges / F4 culture — 자기 원문이 다른 이야기를 한다.

각 시험은 «막아야 하는 것»과 «살려야 하는 것»을 짝으로 둔다.

⚠️ 이 검사가 판정하지 않는 것 — 시험으로도 못 박는다
   원문이 크기를 조금이라도 말하면(1%·70%·다른 사업의 70%·다른 대상의 명시적
   「주요 수익원」) 이 검사는 «물러난다». 순위의 참·거짓을 증명하지 않는다.
"""

from __future__ import annotations

import json
import re

import pytest

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.prose_own_source import (
    own_source_support_terms,
    prose_own_source_problem,
)
from src.features.composer.prose_own_source_constants import (
    PROSE_OWN_SOURCE_REASON_CODES,
    PROSE_OWN_SOURCE_UNSUPPORTED,
    PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY,
)
from src.features.composer.verify import NOTICE_ALL_SENTENCES_REJECTED, verify_report
from src.shared.report_quality.review_diagnostic_constants import REVIEW_REASONS

SECTION_ID = "business_model"
CLAIM_SLOT = "business_model:sales_channel"

F1_CLAIM = (
    "음원 라이선스 제공, 아티스트 출연료·저작권사용료·디지털컨텐츠에 대한 판매기준 "
    "또는 사용기준 로열티 수익 등 지적재산권 기반의 라이선싱 수익이 회사의 주요 수익원이다."
)
#: 자기 원문은 «인식 시점»만 말한다. 「대부분」이 한 번 나오지만 수익 크기가 아니다.
F1_RECOGNITION_ONLY_SOURCE = (
    "연결회사에서 제작한 지적재산의 라이선스를 제공하는 대가로 약속된 판매기준 로열티나 "
    "사용기준 로열티는 기업회계기준서 제1115호에 따르면 나중의 사건이 일어날 때 수익으로 "
    "인식합니다. 라이선스에서 생기는 나머지 효익의 대부분을 획득할 수 있음을 의미합니다."
)
#: 규칙 ①(근거어)은 넉넉히 통과하고 규칙 ②만 갈리도록 «실제에 가깝게» 적는다.
SHORT_CLAIM = "음원 라이선싱 수익이 회사의 주요 수익원이다."
NORMAL_CLAIM = "회사는 온라인 판매 채널을 운영한다."
NORMAL_SOURCE = "가나다회사는 직접 온라인 판매 채널을 운영한다."
UNRELATED_CLAIM = "회사는 국내 광고시장의 침체와 전통광고 시장의 역성장 추세에 직면해 있다."
UNRELATED_SOURCE = "광고업은 차별화된 크리에이티브 역량과 효율적인 매체 기획이 경쟁요소입니다."


def cited(*texts):
    return {f"frag-{index}": text for index, text in enumerate(texts, start=1)}


# ══════════════════════════════════════════════════════════
# 사유 코드 전송 계약
# ══════════════════════════════════════════════════════════
def test_every_new_reason_code_is_an_allowed_review_reason():
    """새 사유는 기존 전송 구조의 «허용 사유»에 등록돼 있어야 한다."""

    for code in PROSE_OWN_SOURCE_REASON_CODES:
        assert code in REVIEW_REASONS


# ══════════════════════════════════════════════════════════
# 규칙 ② — 인식 설명만으로 앞자리를 단정한 경우
# ══════════════════════════════════════════════════════════
def test_the_real_primacy_claim_from_recognition_only_source_is_rejected():
    assert prose_own_source_problem(
        F1_CLAIM, cited(F1_RECOGNITION_ONLY_SOURCE)
    ) == PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY


@pytest.mark.parametrize(
    ("label", "source_text"),
    [
        ("one-percent", "음원 라이선싱 수익은 전체 매출의 1%를 차지한다."),
        ("another-business-70",
         "광고 수익은 전체 매출의 70%를 차지한다. 음원 라이선싱 수익은 별도로 인식한다."),
        ("own-70", "음원 라이선싱 수익은 전체 매출의 70%를 차지한다."),
        ("own-explicit-primacy", "음원 라이선싱 수익은 당사의 주요 수익원입니다."),
    ],
)
def test_this_check_steps_back_when_the_source_states_any_magnitude(label, source_text):
    """정직한 한계 — 크기를 말한 원문에는 판정하지 않는다.

    ★ 이것은 「1%도 주요로 인정한다」는 뜻이 아니다. 「이 검사가 그 참·거짓을
      가리지 않는다」는 뜻이다. 대상 결속을 이 문자열 계층에서 정직하게
      증명할 수 없어서 범위를 좁힌 결과다.
    """

    assert prose_own_source_problem(SHORT_CLAIM, cited(source_text)) == "", label


def test_a_claim_without_the_primacy_shape_is_untouched():
    """「주요 통화」·「핵심 성공요소」는 수익원 순위 단정이 아니다."""

    for claim, source_text in (
        ("회사는 미국달러화, 일본 엔화 등 주요 통화 위험을 모니터링한다.",
         "연결회사는 환율 변동을 모니터링하고 있습니다. 주요 통화 위험을 관리합니다."),
        ("검증된 제작 시스템이 핵심 성공요소로 작용한다.",
         "차별화된 제작 시스템과 IP 경쟁력이 핵심 성공요소로 작용합니다."),
    ):
        assert prose_own_source_problem(claim, cited(source_text)) == ""


def test_a_source_that_is_not_even_a_recognition_explanation_is_untouched():
    assert prose_own_source_problem(
        SHORT_CLAIM, cited("연결회사는 음원 라이선싱 수익 사업을 영위하고 있습니다.")
    ) == ""


# ══════════════════════════════════════════════════════════
# 규칙 ① — 자기 원문 지지가 계약 최소치 미만
# ══════════════════════════════════════════════════════════
def test_a_confirmed_sentence_its_own_source_does_not_support_is_rejected():
    assert prose_own_source_problem(
        UNRELATED_CLAIM, cited(UNRELATED_SOURCE)
    ) == PROSE_OWN_SOURCE_UNSUPPORTED


def test_a_confirmed_sentence_its_own_source_supports_is_kept():
    assert prose_own_source_problem(NORMAL_CLAIM, cited(NORMAL_SOURCE)) == ""


def test_a_reasonable_paraphrase_is_kept():
    """의역이라도 자기 원문 지지가 충분하면 남는다(「배출」→「육성·관리」 모양)."""

    assert prose_own_source_problem(
        "회사는 독창적 제작 시스템을 통해 세계적인 아티스트를 육성하고 관리해왔다.",
        cited("당사는 독창적 제작 시스템을 통해 세계적인 아티스트를 배출해왔습니다."),
    ) == ""


def test_an_unrecoverable_source_is_not_counted_as_zero_support():
    """복원 못 한 원문을 0단어로 세어 거짓 차단하지 않는다."""

    assert prose_own_source_problem(UNRELATED_CLAIM, cited(UNRELATED_SOURCE, "")) == ""
    assert prose_own_source_problem(UNRELATED_CLAIM, {}) == ""


def test_support_terms_are_computed_in_one_place():
    """결속과 공개가 같은 근거어 계산을 본다."""

    from src.features.composer.prose_facts import ProseEvidence, _support_terms

    evidence = [ProseEvidence("f1", None, NORMAL_SOURCE)]
    assert _support_terms(NORMAL_CLAIM, evidence) == own_source_support_terms(
        NORMAL_CLAIM, [NORMAL_SOURCE]
    )


# ══════════════════════════════════════════════════════════
# 실제 진입점 — 본문·요약·진단·빈 장 안내
# ══════════════════════════════════════════════════════════
def _answer_all_true(prompt: str) -> str:
    """프롬프트에 실린 항목을 장·인용 그대로 되돌려 «전부 참»으로 답한다."""

    items = []
    for match in re.finditer(
        r"\[(\d+)\] \(장: ([^,]+), 종류: [^,]+, 인용: ([^)]*)\)", prompt
    ):
        number, section_id, cites = match.groups()
        items.append({
            "번호": int(number),
            "결과": "참",
            "장": section_id.strip(),
            "근거": [item.strip()
                    for item in re.findall(r"조각\s*(\S+?)(?:,|$)", cites)],
        })
    return json.dumps({"판정": items}, ensure_ascii=False)


def _sentence(text, citation, grade=GRADE_CONFIRMED):
    return ComposedSentence(
        text=text, citations=(citation,), grade=grade,
        planned_claim_slot=CLAIM_SLOT, verification_state="verified",
    )


def _verified(sections, summary, fragments):
    diagnostics: list[dict] = []
    result = verify_report(
        ComposedReport(sections=sections, summary=summary),
        fragments, None, _answer_all_true,
        allowed_fragment_ids_by_section={
            SECTION_ID: frozenset(fragment.fragment_id for fragment in fragments)
        },
        diagnostics=diagnostics,
    )
    return result, diagnostics


@pytest.mark.parametrize(
    ("bad_claim", "bad_source"),
    [
        (F1_CLAIM, F1_RECOGNITION_ONLY_SOURCE),
        (UNRELATED_CLAIM, UNRELATED_SOURCE),
    ],
    ids=("primacy-from-recognition", "own-source-unsupported"),
)
def test_the_same_sentence_is_removed_from_body_and_summary_together(
    bad_claim, bad_source
):
    """★ 필수 — 본문과 요약에 «같이» 실린 문장이 둘 다 빠지고 정상은 남는다.

    부록에서 사라진 것만으로는 요약 제거의 증명이 되지 않는다. 그래서 요약
    문장 목록을 직접 본다.
    """

    bad, good = _sentence(bad_claim, "1"), _sentence(NORMAL_CLAIM, "2")
    result, diagnostics = _verified(
        (ComposedSection(SECTION_ID, (bad, good)),),
        (bad, good),
        (CollectedFragment("1", "공시", bad_source),
         CollectedFragment("2", "공시", NORMAL_SOURCE)),
    )
    body = [sentence.text for sentence in result.sections[0].sentences]
    summary = [sentence.text for sentence in result.summary]
    assert bad_claim not in body
    assert bad_claim not in summary
    assert NORMAL_CLAIM in body
    assert NORMAL_CLAIM in summary
    reasons = {item.get("reason_code") for item in diagnostics}
    assert reasons & set(PROSE_OWN_SOURCE_REASON_CODES)
    # 삭제 사유가 본문·요약 «양쪽» 진단으로 전달돼야 한다.
    assert {"본문", "요약"} <= {item.get("kind") for item in diagnostics}


def test_a_section_emptied_by_this_reason_gets_the_contract_notice():
    """빈 장 안내가 기존 계약 문구 그대로 붙는다."""

    bad = _sentence(F1_CLAIM, "1")
    result, diagnostics = _verified(
        (ComposedSection(SECTION_ID, (bad,)),),
        (),
        (CollectedFragment("1", "공시", F1_RECOGNITION_ONLY_SOURCE),),
    )
    assert result.sections[0].sentences == ()
    assert result.sections[0].notice == NOTICE_ALL_SENTENCES_REJECTED
    assert {item.get("reason_code") for item in diagnostics} == {
        PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY
    }


def test_a_normal_report_keeps_body_summary_and_no_notice():
    """대조군 — 정상 문장만 있으면 아무것도 빠지지 않는다."""

    good = _sentence(NORMAL_CLAIM, "1")
    result, diagnostics = _verified(
        (ComposedSection(SECTION_ID, (good,)),),
        (good,),
        (CollectedFragment("1", "공시", NORMAL_SOURCE),),
    )
    assert [s.text for s in result.sections[0].sentences] == [NORMAL_CLAIM]
    assert [s.text for s in result.summary] == [NORMAL_CLAIM]
    assert result.sections[0].notice == ""
    assert not {item.get("reason_code") for item in diagnostics} & set(
        PROSE_OWN_SOURCE_REASON_CODES
    )


def test_an_interpreted_sentence_is_not_touched_by_these_rules():
    interpreted = _sentence(UNRELATED_CLAIM, "1", grade=GRADE_INTERPRETED)
    result, _diagnostics = _verified(
        (ComposedSection(SECTION_ID, (interpreted,)),),
        (),
        (CollectedFragment("1", "공시", UNRELATED_SOURCE),),
    )
    assert [s.text for s in result.sections[0].sentences] == [UNRELATED_CLAIM]
