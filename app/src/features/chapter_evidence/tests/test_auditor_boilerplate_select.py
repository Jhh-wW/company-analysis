"""근거 선별 — 감사인 표준 문구뿐인 조각은 사업 칸의 근거로 담지 않는다.

★ 2026-09-23 5차 실측: 감사보고서 「감사인의 책임」 단락이 5장 과제·대응 칸을 받아
  작가가 감사인의 감사 행위를 회사의 대응으로 옮겨 적었다. 수집 엔진이 근원에서
  막지만, AI 재판정·저장된 수집 결과·다른 수집기 조각도 이 선별을 지난다.
자료는 감사기준서 예시 문형을 새로 풀어 쓴 익명 글이다.
"""

from __future__ import annotations

import hashlib

import pytest

from src.features.chapter_evidence import select as select_module
from src.features.chapter_evidence.auditor_boilerplate import is_auditor_boilerplate
from src.features.chapter_evidence.constants import AUDITOR_BOILERPLATE_FRAGMENT_IGNORED
from src.features.chapter_evidence.select import select_section_fragments
from src.shared.report_evidence.constants import SourceRequirement, SourceTier
from src.shared.report_evidence.models import (
    CollectedEvidenceDocument,
    DocumentTextRange,
    EvidenceFragment,
)

SECTION = "current_challenges"
SLOTS = ("current_challenges:issue", "current_challenges:response")
AUDITOR_RESPONSIBILITY = (
    "- 부정이나 오류로 인한 재무제표의 중요왜곡표시위험을 식별하고 평가하며, 그 위험에 "
    "대응하는 감사절차를 설계하고 수행합니다. 그리고 감사의견의 근거로 충분하고 적합한 "
    "감사증거를 입수합니다.- 상황에 맞는 감사절차를 설계하기 위하여 감사와 관련된 "
    "내부통제를 이해합니다."
)
COMPANY_CHALLENGE = (
    "원재료 가격 상승이 회사의 당면 과제이며, 회사는 공급처를 다변화하는 대책으로 "
    "대응하고 있습니다."
)
KAM_MIXED = (
    "회사의 매출은 다수 고객과 맺은 구독 계약에서 발생하며, 경영진의 성과 목표 때문에 "
    "수익이 과대계상될 위험이 있습니다. 해당 사항에 대응하기 위하여 우리가 수행한 주요 "
    "감사절차는 다음과 같습니다."
)
COMPANY_ICFR = (
    "회사는 내부회계관리제도를 운영하고 있으며, 평가 결과 중요성의 관점에서 효과적으로 "
    "운영되고 있어 재무제표의 중요한 왜곡표시를 예방하는 합리적인 확신을 제공합니다."
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _document(*texts: str, source_kind: str = "dart_audit_report") -> CollectedEvidenceDocument:
    return CollectedEvidenceDocument(
        company_id="corp-1",
        document_id="doc-1",
        canonical_url="https://example.com/x",
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind=source_kind,
        publisher="예시회사",
        title="감사보고서",
        published_on="2026-04-14",
        collected_at="2026-09-23T00:00:00+00:00",
        content_sha256="a" * 64,
        exact_evidence_hashes=tuple(_sha(text) for text in texts),
        identity_binding="binding",
        usable_ranges=(DocumentTextRange(0, 2000),),
        collector_version="collector-v1",
        parser_version="parser-v1",
        requirement=SourceRequirement.REQUIRED,
    )


def _fragment(fragment_id: str, text: str, *, company_id: str = "corp-1") -> EvidenceFragment:
    return EvidenceFragment(
        company_id=company_id,
        fragment_id=fragment_id,
        document_id="doc-1",
        location=f"{len(fragment_id)}-{len(text)}",
        text_sha256=_sha(text),
        text=text,
        section_id=SECTION,
        slot_id=SLOTS[0],
        score_millis=900,
        reason_codes=("keyword_hit:current_challenges:issue",),
        covered_slot_ids=SLOTS,
    )


def _select(*fragments: EvidenceFragment, source_kind: str = "dart_audit_report"):
    return select_section_fragments(
        section_id=SECTION,
        company_id="corp-1",
        documents=(_document(*(fragment.text for fragment in fragments), source_kind=source_kind),),
        fragments=fragments,
    )


def test_감사인_책임_단락은_5장_칸을_받아_와도_선별에서_빠지고_사유가_남는다() -> None:
    selection = _select(_fragment("f-audit", AUDITOR_RESPONSIBILITY), _fragment("f-co", COMPANY_CHALLENGE))

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f-co"]
    assert f"{AUDITOR_BOILERPLATE_FRAGMENT_IGNORED}:1" in selection.reason_codes


@pytest.mark.parametrize("text", [KAM_MIXED, COMPANY_ICFR, COMPANY_CHALLENGE],
                         ids=["핵심감사사항_회사위험", "회사_내부회계관리제도", "회사_과제"])
def test_회사_서술이_섞인_조각은_그대로_담긴다(text: str) -> None:
    selection = _select(_fragment("f1", text))

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f1"]
    assert not any(code.startswith(AUDITOR_BOILERPLATE_FRAGMENT_IGNORED) for code in selection.reason_codes)


def test_결속_방어가_먼저라_다른_회사_조각은_결속_오류로_센다() -> None:
    selection = _select(_fragment("f-audit", AUDITOR_RESPONSIBILITY, company_id="corp-other"))

    assert selection.fragments == ()
    assert "fragment_company_mismatch:1" in selection.reason_codes
    assert not any(code.startswith(AUDITOR_BOILERPLATE_FRAGMENT_IGNORED) for code in selection.reason_codes)


def test_대조군_판정을_끄면_감사인_책임_단락이_5장_근거로_담긴다(monkeypatch) -> None:
    """이 파일의 차단 시험이 «판정 덕분에» 초록인지 확인하는 대조군."""

    monkeypatch.setattr(select_module, "is_auditor_boilerplate", lambda text, **_: False)
    selection = _select(_fragment("f-audit", AUDITOR_RESPONSIBILITY))

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f-audit"]


def test_판정은_제목_절을_세지_않고_회사_절이_하나라도_있으면_거짓이다() -> None:
    assert is_auditor_boilerplate("감사의견근거\n" + AUDITOR_RESPONSIBILITY, audit_report=True)
    assert not is_auditor_boilerplate(AUDITOR_RESPONSIBILITY + " " + COMPANY_CHALLENGE, audit_report=True)
    assert not is_auditor_boilerplate("")


# ══ 2026-09-23 독립 검토 반영 — 문서 종류 관문(F1) ═══════════════════════════════
# 감사보고서 조각만 판정한다. DART 정기보고서는 구조 표지가 있을 때만, 홈페이지·IR·뉴스는
# 판정하지 않는다 — 「감사증거」「재무제표감사」 낱말이 든 감사 업종 회사의 사업 문장 보호.

#: 감사 업종 회사의 사업 문장(익명) — 짧은 감사 표지만 있고 구조 표지는 없다.
AUDIT_INDUSTRY_BUSINESS = (
    "당사의 AI 플랫폼은 감사증거 수집과 검토를 자동화합니다. 당사는 재무제표감사 일정 "
    "관리 기능을 회계법인 고객에게 제공합니다."
)
#: 구조 표지(「감사인의 책임」)가 붙은 감사인 책임 단락.
AUDITOR_RESPONSIBILITY_WITH_HEADING = "재무제표감사에 대한 감사인의 책임\n" + AUDITOR_RESPONSIBILITY


def test_사업보고서의_감사_업종_사업_조각은_구조_표지가_없어_담긴다() -> None:
    selection = _select(_fragment("f-biz", AUDIT_INDUSTRY_BUSINESS), source_kind="dart_business_report")

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f-biz"]
    assert not any(code.startswith(AUDITOR_BOILERPLATE_FRAGMENT_IGNORED) for code in selection.reason_codes)


def test_사업보고서라도_구조_표지가_있는_감사인_단락은_뺀다() -> None:
    selection = _select(_fragment("f-audit", AUDITOR_RESPONSIBILITY_WITH_HEADING),
                        _fragment("f-co", COMPANY_CHALLENGE), source_kind="dart_business_report")

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f-co"]
    assert f"{AUDITOR_BOILERPLATE_FRAGMENT_IGNORED}:1" in selection.reason_codes


@pytest.mark.parametrize(("source_kind", "expected"), [
    ("dart_audit_report", True),
    ("dart_consolidated_audit_report", True),
    ("dart_business_report", None),
    ("dart_semiannual_report", None),
    ("dart_quarterly_report", None),
    ("", None),
    ("official_web_page", False),
    ("official_ir_pdf", False),
    ("news", False),
])
def test_문서_종류가_감사인_문구_판정_범위를_정한다(source_kind: str, expected: bool | None) -> None:
    assert select_module._auditor_judgment_scope(source_kind) is expected


def test_감사보고서가_아닌_글은_감사인_문구가_있어도_판정하지_않는다() -> None:
    assert is_auditor_boilerplate(AUDITOR_RESPONSIBILITY, audit_report=True)
    assert not is_auditor_boilerplate(AUDITOR_RESPONSIBILITY, audit_report=False)
    # 종류를 모르면 구조 표지가 있어야 판정한다.
    assert not is_auditor_boilerplate(AUDITOR_RESPONSIBILITY)
    assert is_auditor_boilerplate(AUDITOR_RESPONSIBILITY_WITH_HEADING)
    assert not is_auditor_boilerplate(AUDIT_INDUSTRY_BUSINESS)


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


@pytest.mark.parametrize("source_kind", ["dart_audit_report", "dart_business_report"])
def test_감사보고서일_현재를_기준일로_쓴_회사_주석은_담긴다(source_kind: str) -> None:
    """R2 — 「감사보고서일」 단독 표지는 감사보고서 안 소송 주석을 감사인 문구로 읽어 뺐다."""

    selection = _select(_fragment("f-note", LITIGATION_NOTE), source_kind=source_kind)

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f-note"]
    assert not any(code.startswith(AUDITOR_BOILERPLATE_FRAGMENT_IGNORED) for code in selection.reason_codes)


def test_사업보고서에_옮겨_실린_핵심감사사항_머리_문단은_뺀다() -> None:
    """R6 — 「우리의 의견형성」이 구조 표지라 정기보고서에서도 판정된다."""

    selection = _select(_fragment("f-kam", KAM_PREAMBLE), _fragment("f-co", COMPANY_CHALLENGE),
                        source_kind="dart_business_report")

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f-co"]
    assert f"{AUDITOR_BOILERPLATE_FRAGMENT_IGNORED}:1" in selection.reason_codes


@pytest.mark.parametrize("text", [AUDITOR_VALIDITY_NOTICE, AUDITOR_SKEPTICISM_VARIANT],
                         ids=["감사보고서일_현재로_유효_후", "전문가적_의구심"])
def test_감사인만_쓰는_문형은_종류를_몰라도_판정한다(text: str) -> None:
    """R2·R7 — 좁힌 «감사보고서일» 문형과 「전문가적 의구심」은 구조 표지다."""

    assert is_auditor_boilerplate(text)
    assert is_auditor_boilerplate(text, audit_report=True)
    assert not is_auditor_boilerplate(text, audit_report=False)
