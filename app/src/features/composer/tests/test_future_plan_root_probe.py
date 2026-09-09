"""ROOT 가 구현 도중 직접 돌린 반례를 회귀 시험으로 고정한다.

당시 구현은 네 반례를 «전부» 통과시켰다(기대값은 모두 «근거 거부»). 여기서 다시
통과하면 같은 구멍이 되살아난 것이다 — 이 파일은 그 재발을 막는다.

★ 반례의 내용은 이 파일 안에 «그대로» 적어 두었다. 저장소에 없는 산출물 파일의
  존재 여부에 기대지 않으므로, 어느 트리에서 돌려도 건너뛰지 않고 실제로 실행된다.
"""

from src.features.composer.future_plan_constants import (
    FUTURE_ACTIVITY_KEY,
    FUTURE_KEY,
    FUTURE_MODE_KEY,
    FUTURE_MODE_PLAN,
    FUTURE_QUOTE_KEY,
    FUTURE_SOURCE_KEY,
    FUTURE_TARGET_KEY,
    FUTURE_OUTLOOK_HARDENED,
    FUTURE_PLAN_DENIED_IN_SOURCE,
    FUTURE_SECOND_CLAIM_UNPROVEN,
    FUTURE_SUBJECT_MISMATCH,
)
from src.features.composer.future_plan_guard import future_plan_problem


def _evidence(source="f1", target="", activity="", quote="", mode=FUTURE_MODE_PLAN):
    """미래근거 항목 하나. 인자는 영문, 계약 키는 상수."""

    return {FUTURE_KEY: [{
        FUTURE_SOURCE_KEY: source,
        FUTURE_TARGET_KEY: target,
        FUTURE_ACTIVITY_KEY: activity,
        FUTURE_QUOTE_KEY: quote,
        FUTURE_MODE_KEY: mode,
    }]}


# ══════════════════════════════════════════════════════════
# ① other_company_single_subject
# ══════════════════════════════════════════════════════════
def test_other_company_single_subject():
    """★ 주제가 «하나»뿐이어도 그 주어가 이 줄과 무관한 회사면 남의 계획이다.

    이전 구현은 「주제가 둘 이상일 때만」 검사해서 이 문장을 통과시켰다.
    """

    source = "경쟁사는 해외 생산 거점을 증설할 계획입니다."
    assert future_plan_problem(
        ("해외 생산 거점 증설", "", ""),
        {"f1": source},
        _evidence(target="해외 생산 거점", activity="증설", quote=source),
    ) == FUTURE_SUBJECT_MISMATCH


def test_an_elided_or_generic_subject_still_passes():
    """반대쪽도 고정한다 — 이 검사가 정상 공시문을 지우면 안 된다."""

    for source in (
        "해외 생산 거점을 증설할 계획입니다.",
        "당사는 해외 생산 거점을 증설할 계획입니다.",
    ):
        assert future_plan_problem(
            ("해외 생산 거점 증설", "", ""),
            {"f1": source},
            _evidence(target="해외 생산 거점", activity="증설", quote=source),
        ) == "", source


# ══════════════════════════════════════════════════════════
# ② truncated_plan_denial
# ══════════════════════════════════════════════════════════
def test_truncated_plan_denial():
    """★ 표지 뒤의 부정을 잘라 낸 구절로는 계획을 세우지 못한다.

    원문은 「…증설할 계획은 없습니다」인데 구절을 「…증설할 계획」에서 끊었다.
    이전 구현은 구절만 읽어서 「할 계획」을 미래 표지로 받아들였다.
    """

    source = "당사는 해외 생산 거점을 증설할 계획은 없습니다."
    assert future_plan_problem(
        ("해외 생산 거점 증설", "", ""),
        {"f1": source},
        _evidence(
            target="해외 생산 거점",
            activity="증설",
            quote="당사는 해외 생산 거점을 증설할 계획",
        ),
    ) == FUTURE_PLAN_DENIED_IN_SOURCE


def test_quoting_the_full_sentence_yields_the_same_verdict():
    source = "당사는 해외 생산 거점을 증설할 계획은 없습니다."
    assert future_plan_problem(
        ("해외 생산 거점 증설", "", ""),
        {"f1": source},
        _evidence(target="해외 생산 거점", activity="증설", quote=source),
    ) == FUTURE_PLAN_DENIED_IN_SOURCE


def test_a_negated_plan_is_distinguished_from_an_absent_plan_and_kept():
    """★ 「하지 않을 계획」(표지 앞 부정)은 계획이 «있는» 것이다 — 지우지 않는다.
    「계획은 없다」(표지 뒤 부정)만 계획의 부재다."""

    source = "당사는 해외 생산 거점을 증설하지 않을 계획입니다."
    assert future_plan_problem(
        ("해외 생산 거점 증설 중단", "", ""),
        {"f1": source},
        _evidence(target="해외 생산 거점", activity="증설", quote=source),
    ) == ""


# ══════════════════════════════════════════════════════════
# ③ second_unproven_plan
# ══════════════════════════════════════════════════════════
def test_second_unproven_plan():
    """★ 한 조각만 증명하고 나머지 주장을 얹지 못한다.

    칸이 「A 확대 및 B 출시」인데 근거는 A 만 증명한다. 이전 구현은 칸 안의
    다른 주장을 세지 않아 그대로 통과시켰다.
    """

    source = "당사는 해외 매장을 확대할 계획입니다."
    assert future_plan_problem(
        ("해외 매장 확대 및 신규 브랜드 출시", "", ""),
        {"f1": source},
        _evidence(target="해외 매장", activity="확대", quote=source),
    ) == FUTURE_SECOND_CLAIM_UNPROVEN


def test_every_segment_proven_by_the_same_citation_is_kept():
    """반대쪽 고정 — 조각마다 근거를 대면 그대로 남는다. 인용은 여전히 하나다."""

    source = "당사는 해외 매장을 확대하고 신규 브랜드를 출시할 계획입니다."
    assert future_plan_problem(
        ("해외 매장 확대 및 신규 브랜드 출시", "", ""),
        {"f1": source},
        {FUTURE_KEY: [
            {"근거": "f1", "대상": "해외 매장", "활동": "확대",
             "원문": source, "양태": "계획"},
            {"근거": "f1", "대상": "신규 브랜드", "활동": "출시",
             "원문": source, "양태": "계획"},
        ]},
    ) == ""


def test_each_segment_citing_a_different_own_source_is_normal():
    """★ ROOT 정정 — 「항목의 근거가 모두 같아야 한다」는 요구가 아니었다.

    조각이 둘이고 각 조각을 «각자의 자기 인용»이 뒷받침하면 그 줄은 보존한다.
    """

    sources = {
        "f1": "당사는 해외 매장을 확대할 계획입니다.",
        "f2": "당사는 신규 브랜드를 출시할 계획입니다.",
    }
    assert future_plan_problem(
        ("해외 매장 확대 및 신규 브랜드 출시", "", ""),
        sources,
        {FUTURE_KEY: [
            {"근거": "f1", "대상": "해외 매장", "활동": "확대",
             "원문": sources["f1"], "양태": "계획"},
            {"근거": "f2", "대상": "신규 브랜드", "활동": "출시",
             "원문": sources["f2"], "양태": "계획"},
        ]},
    ) == ""


# ══════════════════════════════════════════════════════════
# ⑥ root-named-subject-probe.json — 고유명사 단일 주체
# ══════════════════════════════════════════════════════════
NAMED_SUBJECT_SOURCE = "나래기업은 해외사업을 확대할 계획입니다."


def _named_evidence():
    return {FUTURE_KEY: [{"근거": "f1", "대상": "해외사업", "활동": "확대",
                          "원문": NAMED_SUBJECT_SOURCE, "양태": "계획"}]}


def test_named_subject_row_naming_another_company_is_rejected():
    """★ ROOT 반례 — 원문 주어는 「나래기업」인데 행은 「가람기업」의 계획처럼 적혔다.

    주제가 «하나»뿐이면 검사를 건너뛰던 규칙 때문에 이것이 그대로 통과했다
    (`root-named-subject-probe.json`, passed=false). 이제 이름난 주어는 주제가
    하나여도 이 줄에 결속돼 있어야 한다.
    """

    assert future_plan_problem(
        ("가람기업 해외사업 확대", "", ""),
        {"f1": NAMED_SUBJECT_SOURCE},
        _named_evidence(),
    ) == FUTURE_SUBJECT_MISMATCH


def test_named_subject_row_naming_the_same_company_is_kept():
    """반대쪽 고정 — 그 이름이 행에 있으면 남의 계획이 아니다."""

    assert future_plan_problem(
        ("나래기업 해외사업 확대", "", ""),
        {"f1": NAMED_SUBJECT_SOURCE},
        _named_evidence(),
    ) == ""


def test_a_thing_subject_is_not_treated_as_someone_elses_plan():
    """★ 「…서비스는 … 기대하고 있습니다」의 주어는 «남»이 아니라 회사의 사물이다.

    이 escape 가 없으면 실측 SM 광고 원문의 정상 문장까지 지워진다.
    """

    source = ("당사의 차별화된 마케팅 커뮤니케이션 서비스는 새로운 시장 창출까지 "
              "가능할 것으로 기대하고 있습니다.")
    assert future_plan_problem(
        ("새로운 시장 창출 기대", "", ""),
        {"f1": source},
        {FUTURE_KEY: [{"근거": "f1", "대상": "새로운 시장", "활동": "창출",
                       "원문": source, "양태": "전망"}]},
    ) == ""


# ══════════════════════════════════════════════════════════
# ④ unrelated_outlook_qualifier
# ══════════════════════════════════════════════════════════
#: 원문은 «해외사업 확대»의 가능성·기대만 밝힌다. 확정 계획이 아니다.
OUTLOOK_SOURCE = "당사는 해외사업 확대가 가능할 것으로 기대하고 있습니다."


def test_unrelated_outlook_qualifier():
    """★ 전망 한정은 «그 활동»에 붙어 있어야 한다.

    ROOT 후속 설명이 정한 «더 분명한 대조 후보»를 쓴다 — 「해외사업 확대 확정;
    국내사업 검토 전망」. 다른 활동(국내사업)에 붙은 전망 표현으로 해외사업의
    «확정» 주장을 승인할 수 없다. 이전 구현은 칸 어디든 「전망」이 있으면 통과시켰다.
    """

    assert future_plan_problem(
        ("해외사업 확대 확정; 국내사업 검토 전망", "", ""),
        {"f1": OUTLOOK_SOURCE},
        _evidence(target="해외사업", activity="확대", quote=OUTLOOK_SOURCE, mode="전망"),
    ) in (FUTURE_OUTLOOK_HARDENED, FUTURE_SECOND_CLAIM_UNPROVEN)


def test_an_outlook_hedge_in_the_same_segment_is_kept():
    assert future_plan_problem(
        ("해외사업 확대 기대", "", ""),
        {"f1": OUTLOOK_SOURCE},
        _evidence(target="해외사업", activity="확대", quote=OUTLOOK_SOURCE, mode="전망"),
    ) == ""
