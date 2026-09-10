# -*- coding: utf-8 -*-
"""조사·어미만 다른 정상 문장은 유지하고 조직단위 혼동을 막는다."""

from src.features.composer.prose_own_source import (
    own_source_support_terms,
    prose_own_source_problem,
)


# ── 회귀 재현: 우리은행 918(실제 2026-09-10 새벽 실행) [14][240] ──────────
#: 원문은 기업회계기준서 금융부채 분류 설명 — 이 문장과 무관하다.
WOORI_14 = (
    "위험관리나 투자전략에 따라 금융상품집합(금융부채 또는 금융부채의 조합으로 "
    "구성된 집합)의 일부를 구성하고, 공정가치기준으로 관리하고 그 성과를 "
    "평가하며, 그 정보를 내부적으로 제공하는 경우 최초 인식시점에 당기손익-"
    "공정가치측정금융부채로 지정할 수 있습니다."
)
#: 원문은 개인금융 영업부문의 증권·신탁 수수료 인식 설명 — 「여신관련수입수수료」·
#: 「외국환취급수수료」·「기업금융」은 한 번도 나오지 않는다.
WOORI_240 = '⑦ 증권업무수입수수료증권업무수입수수료는 주로 수익증권판매에 대한 수수료로 구성되어 있으며, 고객에게 수익증권을 판매하는 시점에 수익을 인식하고 있습니다. 증권업무수입수수료는 주로 개인금융 영업부문에서 발생합니다. ⑧ 신탁업무운용수익신탁업무운용수익에는 수탁 받은 자산에 대한 운용 및 관리 서비스에 대한 대가로 수취하는 수수료로 구성되어 있습니다. 운용 및 관리 서비스는 기간에 걸쳐 이행하는 수행의무로 서비스가 제공되는 기간 동안 수익을 인식하고 있습니다. 신탁업무운용수익 중 수익보수와 같이 미래 기간의 수탁 자산의 가치 및 기준수익률 등에 의해 영향을 받는 변동대가는 추정치의 제약이 해소되는 시점에 수익을 인식하고 있습니다. 신탁업무운용수익은 주로 개인금융 영업부문에서 발생합니다.⑨ 기타기타에는 주로 송금관련 수수료와 그 밖에 연결회사가 고객에게 제공하는 각종 서비스에 대한 수수료가 포함되어 있습니다. 이러한 수수료는 고객의 요청에 따라 거래가발생하고, 서비스가 제공되는 시점에 수수료의 수취와 동시에 수익을 인식하고 있습니다. 기타수수료는 영업부문 전반에 걸쳐 발생합니다.'
#: 자기 인용 [14][240] 이 실제로 달렸던 후보 — 기업금융 영업부문 여신관련
#: 수입수수료·외국환취급수수료 주장. 사전검수(§2)가 이미 «실제 오류»로 확정.
WOORI_CLAIM = (
    "기업금융 영업부문은 법인 및 개인사업자를 대상으로 운전자금·시설자금 대출, "
    "무역금융, 외환거래 등을 취급하며, 여신관련수입수수료와 외국환취급수수료를 "
    "통해 수익을 창출한다."
)


def test_org_unit_head_noun_alone_is_not_counted_as_a_new_support_term():
    """「영업부문」만 같고 수식어(부문 이름)가 다르면 근거어로 세지 않는다.

    이 시험이 막는 회귀: 조사 벗김 도입 직후 「영업부문은(기업금융)」과
    「영업부문에서(개인금융)」가 같은 스템으로 묶여 근거어 2개(「영업부문」+
    「수익을」)로 최소 문턱을 넘고, 실제로는 무관한 [14][240] 인용이 통과했다.
    """

    terms = own_source_support_terms(WOORI_CLAIM, [WOORI_14, WOORI_240])
    assert "영업부문" not in terms, terms


def test_org_unit_regression_sentence_is_blocked_again():
    """위 회귀 문장은 다시 «근거 부족»으로 막혀야 한다(사전검수 §2 판정 유지)."""

    reason = prose_own_source_problem(WOORI_CLAIM, {"14": WOORI_14, "240": WOORI_240})
    assert reason == "prose_own_source_unsupported", reason


# ── 지켜야 할 보존: 조사·어미만 다른 정상 의역(가람이/가람은) ─────────────
JOSA_SOURCE = "가람은 음반을 제작합니다."
JOSA_CLAIM = "가람이 음반을 제작한다."


def test_particle_only_paraphrase_still_survives():
    """이번 좁힘이 조사 벗김 자체를 죽이지 않았는지 — 가람이/가람은은 그대로 산다."""

    terms = own_source_support_terms(JOSA_CLAIM, [JOSA_SOURCE])
    assert {"가람", "음반을", "제작"} <= set(terms), terms
    assert prose_own_source_problem(JOSA_CLAIM, {"1": JOSA_SOURCE}) == ""


# ── 지켜야 할 차단: 일반 주어(회사는→회사, 실측 SM F3) ───────────────────
GENERIC_SUBJECT_CLAIM = "회사는 국내 광고시장의 침체에 직면해 있다."
GENERIC_SUBJECT_SOURCE = "당해 회사가 영위하는 국내 사업의 개요는 다음과 같습니다."


def test_generic_subject_stem_is_still_excluded():
    """조직단위 좁힘이 기존 일반주어(GENERIC_SUPPORT_STEMS) 차단을 건드리지 않는다."""

    terms = own_source_support_terms(GENERIC_SUBJECT_CLAIM, [GENERIC_SUBJECT_SOURCE])
    assert "회사" not in terms, terms


# ── 좁음 확인: 글자 그대로 겹치는 조직단위 복합어는 그대로 센다 ───────────
def test_a_literally_quoted_org_unit_phrase_is_not_suppressed():
    """수식어까지 «글자 그대로» 같은 조직단위 복합어는 이 좁힘의 대상이 아니다.

    ★ 이 변경은 «새로 벗겨서 얻은» 낱말에만 적용된다(설계 원칙). 원문에 이미
      «기업금융 영업부문»이 그대로 있으면 첫 단계(글자 그대로 포함)에서 이미
      잡히므로 조직단위 좁힘이 끼어들 일이 없다 — 그래서 이 문장은 여전히
      살아야 한다(정상 의역을 새로 막지 않는다는 계약).
    """

    claim = "기업금융 영업부문은 무역금융을 취급한다."
    source = "기업금융 영업부문은 법인 고객을 대상으로 무역금융을 취급합니다."
    terms = own_source_support_terms(claim, [source])
    # 글자 그대로(조사 포함) 겹친 낱말은 스템으로 바뀌지 않고 그대로 세어진다 —
    # 이 좁힘이 손대는 «새로 벗겨서 얻은» 경로가 아니기 때문이다.
    assert "영업부문은" in terms, terms
    assert prose_own_source_problem(claim, {"1": source}) == ""


