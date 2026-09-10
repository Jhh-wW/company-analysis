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
