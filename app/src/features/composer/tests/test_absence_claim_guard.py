# -*- coding: utf-8 -*-
"""자료를 가리키며 «없다»고 단언한 문장을 막고, 회사의 «해당사항 없음»은 지킨다.

★ 왜 이 가드가 생겼나 (실측) — 실행 결과의 8장에 이런 문장 두 개가 실렸다:
    · 「공식 자료에서 회사의 인재상, 핵심가치 선언, 조직문화를 명시적으로 밝힌
       내용을 «찾을 수 없다»」
    · 「공식 자료에서 직원 현황(인원, 평균 근속연수), 복리후생·보상·교육 제도,
       조직개편과 부서 구성, 노사 관계에 관한 구체적 정보를 «찾을 수 없다»」
  둘 다 인용이 0개인 «해석» 문장이다. 작성부는 «공식 자료 전체»를 본 적이
  없고 수집 조각만 본다. 게다가 그 실행은 홈페이지 수집이 오류였고 공식 IR
  문서 시도가 0건이었다 — 「공식 자료」의 일부는 애초에 수집되지도 않았다.
  그리고 그 자료는 원문에 «실제로 있었다»(직원 등 현황·평균근속연수·조직개편).

★ 각 시험은 «막아야 하는 것»과 «살려야 하는 것»을 짝으로 둔다 — 한쪽만 있으면
  가드가 전부 막아도 녹색이 되기 때문이다.
"""

import pytest

from src.features.composer.absence_claim_constants import ABSENCE_CLAIM_UNSUPPORTED
from src.features.composer.absence_claim_guard import absence_claim_problem

#: 실행 결과의 8장 두 문장을 «글자 그대로» 옮긴 것.
N2_문장들 = (
    "공식 자료에서 회사의 인재상, 핵심가치 선언, 조직문화를 명시적으로 밝힌 "
    "내용을 찾을 수 없다.",
    "공식 자료에서 직원 현황(인원, 평균 근속연수), 복리후생·보상·교육 제도, "
    "조직개편과 부서 구성, 노사 관계에 관한 구체적 정보를 찾을 수 없다.",
)


# ── 막아야 하는 것 ───────────────────────────────────────────────────
@pytest.mark.parametrize("문장", N2_문장들, ids=("인재상", "직원현황"))
def test_공식자료_부재_단언은_제외된다(문장):
    assert absence_claim_problem(문장) == ABSENCE_CLAIM_UNSUPPORTED


@pytest.mark.parametrize("문장", [
    "사업보고서에는 조직개편에 관한 기재가 되어 있지 않다.",
    "공시자료에서 노사 관계에 관한 내용은 확인되지 않는다.",
    "회사 홈페이지에서 인재상을 확인할 수 없다.",
    "제공된 자료에는 평균 근속연수가 포함되어 있지 않다.",
], ids=("사업보고서", "공시자료", "홈페이지", "제공된자료"))
def test_지시어와_술어가_달라도_같은_거짓말은_같이_막힌다(문장):
    """어휘 하나에 기대지 않는다 — 지시어 갈래와 술어 갈래를 각각 고정한다."""

    assert absence_claim_problem(문장) == ABSENCE_CLAIM_UNSUPPORTED


def test_이_가드는_장을_보지_않는다():
    """장 무관 규칙이다 — 함수가 장을 인자로 받지 않는다는 사실로 못 박는다."""

    import inspect

    assert list(inspect.signature(absence_claim_problem).parameters) == ["text"]


# ── 살려야 하는 것 ───────────────────────────────────────────────────
def test_회사가_해당사항_없다고_밝힌_인용_문장은_보존된다():
    """음성 대조 — 회사가 스스로 «밝힌» 공시 문장은 정상 인용이다.

    원문 실측: 「2) 신규 사업 및 중단사업 당사 해당사항 없습니다」. 이 문장의
    주어는 자료가 아니라 회사다 — 지시어를 «공식 자료/공시/원문» 쪽으로
    좁혀 두 진술을 가른다.
    """

    for 문장 in (
        "2) 신규 사업 및 중단사업 당사 해당사항 없습니다.",
        "당사는 보고기간 중 중단사업이 없습니다.",
        "회사는 해당 기간에 배당을 실시하지 않았다.",
    ):
        assert absence_claim_problem(문장) == ""


def test_확인범위_안내문은_이_검사를_지나가지_않는다():
    """음성 대조 — 부재는 «안내문»이 말한다. 안내문은 문장 경로에 없다.

    ★ 두 가지를 함께 단정한다 — ① 저장소가 이미 쓰는 안내문 글귀 자체가 이
      가드에 걸리지 않는다 ② 안내문은 ComposedSection.notice 로 실려 문장
      목록에 들어가지 않으므로 애초에 이 검사를 받지 않는다.
    """

    from src.features.composer.port import ComposedSection

    안내문 = (
        "확인 범위 밖의 항목은 이 보고서에 담지 않았습니다. "
        "자료가 없다는 뜻은 아닙니다."
    )
    assert absence_claim_problem(안내문) == ""

    section = ComposedSection(section_id="culture", sentences=(), notice=안내문)
    assert section.sentences == ()
    assert 안내문 not in [sentence.text for sentence in section.sentences]


def test_정도_표현은_부재_단언이_아니다():
    """「제한적이다」·「어렵다」는 부재 단언이 아니라 기존 의미 검수의 몫이다."""

    for 문장 in (
        "공식 자료만으로는 조직문화를 판단하기 어렵다.",
        "공시 자료에 담긴 인사 제도 설명은 제한적이다.",
    ):
        assert absence_claim_problem(문장) == ""


def test_같은_절이_아니면_걸리지_않는다():
    """앞 절의 지시어와 뒤 절의 부재 술어가 우연히 만나서는 안 된다."""

    문장 = "공식 자료를 확인했다. 회사는 배당을 실시하지 않았다."
    assert absence_claim_problem(문장) == ""


def test_빈_문자열과_공백은_판정_대상이_아니다():
    for 문장 in ("", "   ", "\n\n"):
        assert absence_claim_problem(문장) == ""


# ══════════════════════════════════════════════════════════
# 표현만 바꾼 같은 거짓말 (독립 검토가 찾은 미탐 4건)
#
# ★ 이 가드는 낱말 목록이라 목록에 없는 새 표현은 여전히 통과한다. 아래는
#   실측으로 뚫린 네 갈래를 어간 기준으로 담은 것이고, 이 시험은 그 네 갈래가
#   되돌아오지 않게 못 박는다. 근본 해법(자료 쪽 주어 + 부정 극성을 문법으로
#   판정)은 이 커밋 범위 밖이다 — 상수 파일 머리말에 그 한계를 적어 두었다.
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("문장", [
    "공식 자료에는 인재상에 대한 설명이 담겨 있지 않다.",
    "수집된 근거 조각에는 조직문화 관련 내용이 나오지 않는다.",
    "공식 자료상 확인 가능한 인재상 관련 기술은 부재하다.",
    "공식 자료 어디에도 평균 근속연수는 등장하지 않는다.",
], ids=("담겨있지않다", "나오지않는다", "부재하다", "등장하지않는다"))
def test_표현만_바꾼_같은_거짓말도_막힌다(문장):
    assert absence_claim_problem(문장) == ABSENCE_CLAIM_UNSUPPORTED


@pytest.mark.parametrize("문장", [
    "사업보고서에 명시되지 않았다.",
    "공시자료에서 나타나지 않는다.",
    "공식 자료에 관련 기재가 없다.",
    "제공된 자료에는 그런 내용이 없었다.",
], ids=("명시되지않", "나타나지않", "기재가없다", "없었다"))
def test_코디네이터가_지정한_술어_갈래도_막힌다(문장):
    assert absence_claim_problem(문장) == ABSENCE_CLAIM_UNSUPPORTED


@pytest.mark.parametrize("문장", [
    # 회사 주어 — 자료를 가리키지 않는다.
    "2) 신규 사업 및 중단사업 당사 해당사항 없습니다.",
    "당사는 해당 사항이 없습니다.",
    "당사는 노동조합이 결성되어 있지 않습니다.",
    # 확인 범위 안내문 — 부재를 말하는 «정직한» 통로다.
    "확인 범위 밖의 항목은 이 보고서에 담지 않았습니다. 자료가 없다는 뜻은 아닙니다.",
    # 자료를 근거로 «전달»하는 문장.
    "사업보고서에 따르면 당사는 배당을 실시하지 않았다.",
    "회사는 구체적 금액을 밝히지 않은 채 계약을 체결했다고 공시했다.",
    # 정도 표현 — 부재 단언이 아니다.
    "공식 자료만으로는 조직문화를 판단하기 어렵다.",
    "공식 자료에서 관련 내용을 찾아보기 어려웠다.",
    # ★ 자료 지시어«와» 「없다」를 «함께» 가진 정상 문장 — 여기가 넓히는 쪽
    #   수정에서 실제로 무너진 자리다. 예전 음성 대조 목록에는 이 모양이
    #   하나도 없어서, 「없다」를 대상에 결속하지 않은 채 넓혔을 때 정상 문장
    #   10건 중 9건이 제외로 뒤집힌 것을 아무도 잡지 못했다.
    #   전부 회사가 «밝힌» 사실을 자료를 근거로 전달하는 문장이다.
    "사업보고서에 따르면 당사는 노동조합이 없다.",
    "감사보고서에 따르면 계속기업 관련 중요한 불확실성은 없다.",
    "분기보고서 기준 최대주주 변동이 없다.",
    "보도자료에 가격 인상 계획은 없다고 밝혔다.",
    "홈페이지에 따르면 해외 지점이 없다.",
    "원문에는 회사가 진행 중인 중요 소송이 없다고 적혀 있다.",
    # 확인 범위 안내문의 «변형» — 원문 그대로가 아니어도 지켜야 한다.
    "공식 자료가 없다는 뜻은 아닙니다.",
], ids=(
    "해당사항없음", "해당사항없음2", "노동조합", "안내문",
    "공시전달", "밝히지않은채", "판단하기어렵다", "찾아보기어려웠다",
    "사업보고서_노동조합없다", "감사보고서_불확실성없다",
    "분기보고서_변동없다", "보도자료_계획없다",
    "홈페이지_지점없다", "원문_소송없다", "안내문변형",
))
def test_어휘를_넓혀도_살려야_할_문장은_그대로_산다(문장):
    """★ 넓히는 쪽 수정에서 가장 먼저 깨지는 자리라 전부 한 시험에 모은다."""

    assert absence_claim_problem(문장) == ""


# ══════════════════════════════════════════════════════════
# 문장을 «뺀 장»에 남기는 확인 범위 안내문 — 실제 진입점 배선
#
# ★ 왜 필요한가 (4차 유료 실행 실측) — 이 가드는 참·거짓을 가리지 않고 막는다.
#   3차 실행 8장에 실렸던 「공식 자료에서 회사의 인재상, 핵심가치, 조직문화,
#   일하는 방식에 관한 명시적 선언을 찾을 수 없다」는 네 낱말 모두 원문 출현
#   0회라 «참»이었는데 4차에서 그대로 지워졌고, 그 장의 확인 범위 안내문은
#   0개였다. 거짓말은 막았지만 정보가 통째로 사라졌다.
# ★ 시험은 «운영 진입점»을 단정한다 — 가드 함수만 부르면 배선 결함을 못 잡는다.
# ══════════════════════════════════════════════════════════

from src.features.composer.absence_claim_constants import (  # noqa: E402
    ABSENCE_SCOPE_GUIDANCE_NOTICE,
)
from src.features.composer.absence_claim_guard import (  # noqa: E402
    with_absence_scope_guidance,
)


def _조각들() -> dict[int, dict[str, str]]:
    return {
        1: {
            "종류": "사업내용",
            "원문": (
                "당사는 임직원 교육훈련 제도를 운영하고 있으며 "
                "인사위원회가 승진 기준을 심의합니다."
            ),
        },
    }


def _문화보고서(문장들):
    from src.features.composer.port import ComposedReport, ComposedSection

    return ComposedReport(
        sections=(
            ComposedSection(section_id="culture", sentences=문장들),
        )
    )


def _문장(글: str, 인용=("1",)):
    from src.features.composer.constants import GRADE_INTERPRETED
    from src.features.composer.port import ComposedSentence

    return ComposedSentence(text=글, citations=인용, grade=GRADE_INTERPRETED)


class _언제나참:
    """검수 AI 대역 — 모든 후보를 «참»으로 판정한다.

    ★ 묶음(packet 엄격) 경로는 판정마다 «장»과 «근거»까지 요구한다. 그 두 칸을
      빼면 후보가 «다른 이유»로 빠져 시험이 조용히 무의미해진다 — 그래서
      프롬프트에서 번호를 읽어 두 칸을 채운다.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        import json
        import re

        self.prompts.append(prompt)
        numbers = [int(n) for n in re.findall(r"^\[(\d+)\] \(", prompt, re.MULTILINE)]
        if not numbers:
            numbers = list(range(1, 10))
        return json.dumps(
            {"판정": [{"번호": n, "결과": "참", "장": "culture", "근거": ["1"]}
                     for n in numbers]},
            ensure_ascii=False,
        )


def _허용(grouped: bool):
    """엄격(packet) 경로에서만 장별 허용 근거를 넘긴다 — legacy는 None."""

    return {"culture": frozenset({"1"})} if grouped else None


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "packet엄격"))
def test_verify_report는_부재_단언을_뺀_장에_확인범위_안내문을_남긴다(grouped):
    """★ 배선 시험 — 문장은 빠지고, 그 자리에 안내문 한 줄이 생긴다.

    두 검수 경로를 «같은 시험»이 함께 단정한다. 한쪽만 걸면 그 경로로 나간
    보고서에서만 안내문이 사라진다.
    """

    from src.features.composer.verify import verify_report

    보고서 = _문화보고서((
        _문장(N2_문장들[0], 인용=()),
    ))
    결과 = verify_report(
        보고서, _조각들(), None, _언제나참(),
        allowed_fragment_ids_by_section=_허용(grouped),
    )

    장 = 결과.sections[0]
    assert 장.sentences == ()  # 부재 단언 문장은 공개되지 않는다
    assert ABSENCE_SCOPE_GUIDANCE_NOTICE in 장.notice
    assert 장.notice.count(ABSENCE_SCOPE_GUIDANCE_NOTICE) == 1


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "packet엄격"))
def test_verify_report는_부재_단언이_없으면_안내문을_만들지_않는다(grouped):
    """음성 대조 — 안 걸린 장에 이 안내문이 붙으면 그건 거짓 설명이다."""

    from src.features.composer.verify import verify_report

    보고서 = _문화보고서((
        _문장("회사는 임직원 교육훈련 제도를 운영한다."),
    ))
    결과 = verify_report(
        보고서, _조각들(), None, _언제나참(),
        allowed_fragment_ids_by_section=_허용(grouped),
    )

    장 = 결과.sections[0]
    assert 장.sentences  # 정상 문장은 그대로 공개된다
    assert ABSENCE_SCOPE_GUIDANCE_NOTICE not in 장.notice


def test_한_장에서_여러_문장이_걸려도_안내문은_한_줄이다():
    """중복 금지 — 문장 수만큼 안내문이 늘어나면 안 된다."""

    from src.features.composer.verify import verify_report

    보고서 = _문화보고서((
        _문장(N2_문장들[0], 인용=()),
        _문장(N2_문장들[1], 인용=()),
    ))
    결과 = verify_report(보고서, _조각들(), None, _언제나참())

    assert 결과.sections[0].notice.count(ABSENCE_SCOPE_GUIDANCE_NOTICE) == 1


def test_check_diagrams도_부재_단언을_뺀_장에_같은_안내문을_남긴다():
    """★ 도식 경로 배선 — 같은 거짓말이 칸으로 옮겨 적혀도 같은 처분이다."""

    from src.features.composer.diagram_check import check_diagrams
    from src.features.composer.port import (
        CollectedFragment, ComposedReport, ComposedSection, FlowRow,
    )

    행 = FlowRow(
        cells=("인재상", "핵심가치", "공식 자료에서 관련 내용을 찾을 수 없다"),
        citations=("1",),
    )
    보고서 = ComposedReport(
        sections=(
            ComposedSection(section_id="culture", sentences=(), flow_rows=(행,)),
        )
    )
    조각 = (CollectedFragment(
        fragment_id="1", kind="사업내용",
        text="당사는 임직원 교육훈련 제도를 운영하고 있습니다.",
    ),)

    결과, 사유들 = check_diagrams(보고서, 조각, _언제나참())

    장 = 결과.sections[0]
    assert 장.flow_rows == ()  # 부재 단언 행은 공개되지 않는다
    assert ABSENCE_SCOPE_GUIDANCE_NOTICE in 장.notice
    assert any(ABSENCE_CLAIM_UNSUPPORTED in 사유 for 사유 in 사유들)


def test_기존_안내문이_있으면_지우지_않고_뒤에_붙인다():
    """장이 통째로 비면 기존 안내문과 «둘 다» 있어야 한다 — 한쪽을 덮지 않는다."""

    from src.features.composer.verify import NOTICE_ALL_SENTENCES_REJECTED

    합친것 = with_absence_scope_guidance(NOTICE_ALL_SENTENCES_REJECTED)
    assert NOTICE_ALL_SENTENCES_REJECTED in 합친것
    assert ABSENCE_SCOPE_GUIDANCE_NOTICE in 합친것
    # 두 번 불러도 늘어나지 않는다.
    assert with_absence_scope_guidance(합친것) == 합친것


def test_안내문은_지운_문장의_주제어를_담지_않는다():
    """지어냄 방지 — 그 주제가 실제로 없는지는 작성부가 모른다."""

    for 주제어 in ("인재상", "핵심가치", "조직문화", "일하는 방식", "복리후생"):
        assert 주제어 not in ABSENCE_SCOPE_GUIDANCE_NOTICE


def test_안내문_자체는_이_가드에_걸리지_않는다():
    """음성 대조 — 안내문이 부재 단언으로 판정되면 유일한 통로가 막힌다."""

    assert absence_claim_problem(ABSENCE_SCOPE_GUIDANCE_NOTICE) == ""
