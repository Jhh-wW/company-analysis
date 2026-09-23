"""감사보고서 «감사인 표준 문구»가 사업 칸을 받지 못하는지 — 근원(수집 채점) 차단.

★ 2026-09-23 5차 실측: 감사보고서 「감사인의 책임」 단락(주어는 감사인 「우리」)이
  「위험」·「대응」 두 낱말로 5장 과제·대응 칸을 받았고, 작가가 감사인의 감사 행위를
  회사의 대응으로 옮겨 적었다.
자료는 감사기준서 예시 문형을 새로 풀어 쓴 익명 글이다(실제 회사 원문을 옮기지 않았다).
"""

from __future__ import annotations

import pytest

from features.evidence_collection import auditor_boilerplate, relevance
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult, RawFilingRow
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher

_NOW = "2026-09-23T00:00:00+09:00"

#: 감사인 책임 단락 — 문장마다 감사인이 주어인 감사 행위다.
AUDITOR_RESPONSIBILITY = (
    "- 부정이나 오류로 인한 재무제표의 중요왜곡표시위험을 식별하고 평가하며, 그 위험에 "
    "대응하는 감사절차를 설계하고 수행합니다. 그리고 감사의견의 근거로 충분하고 적합한 "
    "감사증거를 입수합니다. 부정은 공모나 내부통제 무력화가 개입될 수 있어 부정으로 인한 "
    "중요한 왜곡표시를 발견하지 못할 위험이 오류로 인한 위험보다 큽니다.- 상황에 맞는 "
    "감사절차를 설계하기 위하여 감사와 관련된 내부통제를 이해합니다. 이는 내부통제의 "
    "효과성에 대한 의견을 표명하기 위한 것이 아닙니다."
)
#: 감사의견·감사의견근거 단락.
AUDIT_OPINION = (
    "감사의견 우리는 주식회사 가나다의 재무제표를 감사하였습니다. 우리의 의견으로는 별첨된 "
    "재무제표는 회사의 재무상태와 재무성과를 중요성의 관점에서 공정하게 표시하고 있습니다. "
    "감사의견근거 우리는 대한민국의 회계감사기준에 따라 감사를 수행하였습니다."
)
#: 감사보고서 안의 경영진·지배기구 책임 단락.
MANAGEMENT_RESPONSIBILITY = (
    "재무제표에 대한 경영진과 지배기구의 책임 경영진은 이 재무제표를 작성하고 공정하게 "
    "표시할 책임이 있습니다. 경영진은 계속기업 관련 사항을 공시할 책임이 있으며, 회계의 "
    "계속기업전제의 사용에 대해서도 책임이 있습니다. 지배기구는 회사의 재무보고절차의 "
    "감시에 대한 책임이 있습니다."
)
#: 회사가 주어인 내부회계관리제도 운영 서술 — 짧은 감사 낱말이 있어도 회사 서술이다.
COMPANY_ICFR = (
    "회사는 내부회계관리제도를 운영하고 있으며, 내부회계관리자가 매년 운영실태를 평가해 "
    "이사회에 보고합니다. 평가 결과 회사의 내부회계관리제도는 중요성의 관점에서 효과적으로 "
    "운영되고 있으며, 재무제표의 중요한 왜곡표시를 예방하는 합리적인 확신을 제공합니다."
)
#: 핵심감사사항 — 회사 위험 서술 + 감사인 절차 문장.
KAM_MIXED = (
    "회사의 매출은 다수 고객과 맺은 구독 계약에서 발생하며, 경영진의 성과 목표 때문에 "
    "수익이 과대계상될 위험이 있습니다. 해당 사항에 대응하기 위하여 우리가 수행한 주요 "
    "감사절차는 다음과 같습니다."
)
#: 계속기업 불확실성 강조 문단 — 회사 사정(금액)이 핵심이다.
GOING_CONCERN = (
    "회사는 당기 중 영업손실 12,345백만원이 발생하였고 유동부채가 유동자산을 5,678백만원 "
    "초과하고 있습니다. 이러한 상황은 계속기업으로서의 존속능력에 유의적 의문을 제기할 만한 "
    "중요한 불확실성이 존재함을 나타냅니다."
)


@pytest.mark.parametrize("text", [AUDITOR_RESPONSIBILITY, AUDIT_OPINION, MANAGEMENT_RESPONSIBILITY],
                         ids=["감사인책임", "감사의견", "경영진책임"])
def test_감사인_표준_문구뿐인_문단은_사업_칸을_받지_않는다(text: str) -> None:
    split = auditor_boilerplate.split_auditor_clauses(text)
    assert split.only_auditor and split.business_clause_count == 0
    assert relevance.score_fragment_slots_with_signal(text, "") == ((), True)
    assert relevance.score_fragment_text(text) is None


@pytest.mark.parametrize("text", [COMPANY_ICFR, GOING_CONCERN,
                                  "우리의 목적은 모든 사람이 인공지능을 쉽게 쓰게 하는 것입니다. "
                                  "우리의 책임은 고객 데이터를 안전하게 지키는 것입니다.",
                                  "감사위원회는 내부감사기준에 따라 연 4회 내부감사를 실시하고 결과를 "
                                  "이사회에 보고합니다."],
                         ids=["회사_내부회계관리제도", "계속기업_회사사정", "홈페이지_우리", "내부감사기준"])
def test_회사가_주어인_서술은_감사인_문구로_세지_않는다(text: str) -> None:
    split = auditor_boilerplate.split_auditor_clauses(text)
    assert split.auditor_clause_count == 0
    assert not auditor_boilerplate.is_auditor_boilerplate(text)


def test_핵심감사사항은_회사_위험만_채점하고_감사인_대응_칸은_주지_않는다() -> None:
    split = auditor_boilerplate.split_auditor_clauses(KAM_MIXED)
    assert (split.auditor_clause_count, split.business_clause_count) == (1, 1)
    slots = {score.slot_id for score in relevance.score_fragment_slots(KAM_MIXED)}
    assert "current_challenges:issue" in slots
    assert "current_challenges:response" not in slots


def test_대조군_분리를_끄면_감사인_책임_단락이_5장_과제_대응_칸을_받는다(monkeypatch) -> None:
    """이 파일의 차단 시험이 «가드 덕분에» 초록인지 확인하는 대조군(실측 결함 재현)."""

    monkeypatch.setattr(
        auditor_boilerplate, "split_auditor_clauses",
        lambda text, **_: auditor_boilerplate.AuditorClauseSplit(text, 0, 1),
    )
    slots = {score.slot_id for score in relevance.score_fragment_slots(AUDITOR_RESPONSIBILITY)}
    assert {"current_challenges:issue", "current_challenges:response"} <= slots


@pytest.mark.parametrize("clause", [
    "회사는 재무제표 왜곡표시로 과징금 1,200백만원을 부과받았습니다",
    "회사는 감리 결과 회계처리기준 위반으로 재무제표 감사증거 보관 의무 제재를 받았습니다",
])
def test_회사에_일어난_금액_제재_사건은_감사인_문구가_아니다(clause: str) -> None:
    assert not auditor_boilerplate.is_auditor_clause(clause)


def _audit_report_fetcher(text: str) -> FakeFetcher:
    row = RawFilingRow("20260401000001", "감사보고서", "20260401")
    return FakeFetcher(
        list_responses_by_pblntf_ty={"F": FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={
            row.rcept_no: DocumentFetchResult(
                state="OK", text=text, elapsed_ms=10, bytes_downloaded=len(text.encode("utf-8")),
            ),
        },
    )


_AUDIT_REPORT = "\n\n".join((
    "회사의 개요\n주식회사 가나다는 2019년에 설립된 산업용 센서 제조 법인이며, 본점 소재지는 "
    "경기도 수원시입니다.",
    AUDITOR_RESPONSIBILITY,
    KAM_MIXED,
)) + "\n"


def test_수집_결과에_감사인_책임_단락이_분류_조각으로도_무분류_조각으로도_남지_않는다() -> None:
    """무분류로 보내면 AI 재판정이 사업 칸을 다시 붙일 수 있다 — 둘 다 없어야 한다."""

    harvest = collect_dart_evidence(_audit_report_fetcher(_AUDIT_REPORT), "00164788", now=_NOW)

    for fragment in (*harvest.fragments, *harvest.unclassified_fragments):
        assert "감사증거" not in fragment.text
    kam = [fragment for fragment in harvest.fragments if "과대계상될 위험" in fragment.text]
    assert len(kam) == 1
    assert "current_challenges:issue" in kam[0].covered_slot_ids
    assert "current_challenges:response" not in kam[0].covered_slot_ids
    assert any(slot.startswith("identity:") for fragment in harvest.fragments
               for slot in fragment.covered_slot_ids)


# ══ 2026-09-23 독립 검토 반영 — 구조 표지 관문(F1)과 내부 감사절차 면제(F4) ══════════
# 채점 자리는 문서 종류를 모른다. 「감사증거」「감사기준」 같은 짧은 표지는 감사 소프트웨어·
# 감사 서비스 회사의 사업 문장에도 나오므로, 문단이나 절 제목에 감사보고서 구조 표지가
# 있을 때만 감사인 절을 가린다.

#: 감사 업종 회사의 사업 문단(익명) — 구조 표지가 없다.
AUDIT_INDUSTRY_BUSINESS = (
    "당사의 AI 플랫폼은 감사증거 수집과 검토를 자동화합니다. 당사는 회계감사기준에 맞춘 "
    "조서 서식과 재무제표감사 일정 관리 기능을 회계법인 고객에게 제공합니다."
)
#: 머리말 없이 잘린 감사인 책임 문단 — 구조 표지가 없다.
AUDITOR_CHUNK_WITHOUT_STRUCTURE = (
    "- 부정이나 오류로 인한 재무제표의 중요왜곡표시위험을 식별하고 평가하며, 그 위험에 "
    "대응하는 감사절차를 설계하고 수행합니다. 그리고 감사의견의 근거로 충분하고 적합한 "
    "감사증거를 입수합니다."
)
#: 5차 실측 조각 12와 같은 모양 — 머리말은 없고 「감사보고서일까지 입수」만 구조 표지다
#: (R2로 「감사보고서일」 단독 표지를 감사인 문형 셋으로 좁힌 뒤에도 걸리는 꼴).
AUDITOR_CHUNK_WITH_REPORT_DATE = AUDITOR_CHUNK_WITHOUT_STRUCTURE + (
    " 우리의 결론은 감사보고서일까지 입수한 감사증거에 기초하나, 미래 사건이나 상황에 따라 "
    "회사가 계속기업으로서 존속하지 못할 수 있습니다."
)


def _scores_without_split(monkeypatch, text: str, heading: str = ""):
    with monkeypatch.context() as patch:
        patch.setattr(
            auditor_boilerplate, "split_auditor_clauses",
            lambda value, **_: auditor_boilerplate.AuditorClauseSplit(value, 0, 1),
        )
        return relevance.score_fragment_slots_with_signal(text, heading)


def test_구조_표지가_없는_감사_업종_사업_문단은_변경_전과_같이_채점된다(monkeypatch) -> None:
    split = auditor_boilerplate.split_auditor_clauses(AUDIT_INDUSTRY_BUSINESS)
    assert split.auditor_clause_count == 0
    assert not auditor_boilerplate.is_auditor_boilerplate(AUDIT_INDUSTRY_BUSINESS)
    assert (relevance.score_fragment_slots_with_signal(AUDIT_INDUSTRY_BUSINESS, "")
            == _scores_without_split(monkeypatch, AUDIT_INDUSTRY_BUSINESS))


@pytest.mark.parametrize(("text", "heading"), [
    (AUDITOR_CHUNK_WITHOUT_STRUCTURE, "재무제표감사에 대한 감사인의 책임"),
    (AUDITOR_CHUNK_WITH_REPORT_DATE, ""),
], ids=["제목에_구조표지", "감사보고서일_구조표지"])
def test_구조_표지가_제목이나_문단에_있으면_머리말_없는_감사인_문단도_가린다(text: str, heading: str) -> None:
    assert auditor_boilerplate.split_auditor_clauses(text, structure_context=heading).only_auditor
    assert relevance.score_fragment_slots_with_signal(text, heading) == ((), True)


def test_구조_표지가_어디에도_없으면_머리말_없는_감사인_문단은_가리지_않는다() -> None:
    """알려진 한계 — 엔진은 종류를 모르므로 이 문단은 근거 선별의 문서 종류 관문이 막는다."""

    split = auditor_boilerplate.split_auditor_clauses(AUDITOR_CHUNK_WITHOUT_STRUCTURE)
    assert split.auditor_clause_count == 0


@pytest.mark.parametrize(("clause", "expected"), [
    ("회사는 내부 감사절차를 설계해 운영한다", False),
    ("회사는 자체 감사절차를 설계해 점검한다", False),
    ("회사는 협력사 품질 감사절차를 설계해 연 2회 점검한다", False),
    ("상황에 맞는 감사절차를 설계하기 위하여 감사와 관련된 내부통제를 이해합니다", True),
], ids=["내부", "자체", "품질", "감사인"])
def test_내부_자체_품질_감사절차_설계는_감사인_절이_아니다(clause: str, expected: bool) -> None:
    assert auditor_boilerplate.is_auditor_clause(clause) is expected


# ══ 2026-09-23 B 수정 재검토 반영 — «감사보고서일 현재» 회사 주석(R2)·핵심감사사항 머리(R6)·
#    「전문가적 의구심」(R7) ══

#: 소송 주석(익명) — 「감사보고서일 현재」는 회사 주석의 흔한 기준일 문구다(감사인 문형이 아니다).
LITIGATION_NOTE = (
    "보고기간종료일 현재 회사가 피고로 계류중인 소송사건은 2건이며, 감사보고서일 현재 "
    "그 결과를 합리적으로 예측할 수 없습니다."
)
#: 감사보고서 유효 기간 문단(감사기준서 예시 문형을 새로 풀어 씀) — 감사인만 쓰는 문형이다.
AUDITOR_VALIDITY_NOTICE = (
    "이 감사보고서는 감사보고서일 현재로 유효한 것입니다. 따라서 감사보고서일 후 이 보고서를 "
    "열람하는 시점 사이에 재무제표에 중요한 영향을 미칠 수 있는 사건이 발생할 수 있습니다."
)
#: 사업보고서에 옮겨 실린 핵심감사사항 머리 문단(익명 재서술).
KAM_PREAMBLE = (
    "핵심감사사항은 우리의 전문가적 판단에 따라 당기 재무제표감사에서 가장 유의적인 사항들입니다. "
    "해당 사항들은 재무제표 전체에 대한 감사의 관점에서 우리의 의견형성 시 다루어졌으며, "
    "우리는 이런 사항에 대하여 별도의 의견을 제공하지는 않습니다."
)
#: 「전문가적 회의주의」의 실제 번역 변형을 쓴 감사인 문단(익명 재서술).
AUDITOR_SKEPTICISM_VARIANT = (
    "감사기준에 따른 감사의 일부로서 우리는 감사의 전 과정에 걸쳐 전문가적 판단을 수행하고 "
    "전문가적 의구심을 유지하고 있습니다."
)


def test_감사보고서일_현재를_기준일로_쓴_회사_주석은_채점에서_빠지지_않는다(monkeypatch) -> None:
    """R2 — 「감사보고서일」 단독 표지는 이 주석 문단을 통째로 채점에서 버렸다."""

    split = auditor_boilerplate.split_auditor_clauses(LITIGATION_NOTE)
    assert split.auditor_clause_count == 0
    assert (relevance.score_fragment_slots_with_signal(LITIGATION_NOTE, "")
            == _scores_without_split(monkeypatch, LITIGATION_NOTE))


@pytest.mark.parametrize("text", [AUDITOR_VALIDITY_NOTICE, KAM_PREAMBLE, AUDITOR_SKEPTICISM_VARIANT],
                         ids=["유효기간_문단", "핵심감사사항_머리", "전문가적_의구심"])
def test_감사인만_쓰는_문형은_머리말_없이도_문단째_가린다(text: str) -> None:
    """R2·R6·R7 — 좁힌 «감사보고서일» 문형과 새 구조 표지로 관문이 열린다."""

    assert auditor_boilerplate.split_auditor_clauses(text).only_auditor
    assert relevance.score_fragment_slots_with_signal(text, "") == ((), True)
