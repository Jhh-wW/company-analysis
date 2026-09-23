"""공식 선택 후보 칸의 운반 경계와 관계법인 회계범위 각주의 legacy 운반.

★ 왜 필요한가(2026-09-23 4차 실측) — 감사보고서 「회사의 개요」 조각으로 작가가
  본점 소재지를 identity:official_location으로 정확히 썼지만, 공식 문서 종류의
  지원 칸 상한이 필수 칸뿐이라 typed 운반이 그 칸을 실을 수 없었다. 이 시험은
  «전문 공시만» 선택 칸을 싣고, 다른 종류·필수 커버리지는 그대로 닫혀 있는지
  고정한다. 같은 법인의 제한 각주 legacy 조각은 7장 packet으로만 간다.

★ AI·네트워크 0회.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.features.pipeline import real
from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_SECTION_IDS_KEY,
    RAW_EVIDENCE_SLOT_IDS_KEY,
    build_section_evidence_packet_set,
    typed_fragments_from_raw,
)
from src.features.pipeline.tests.test_evidence_transport import (
    _CORP_ID,
    _FILING_META,
    _GENERATION,
    _all_legacy_frags,
    _typed_raw_for_formal_kind,
)
from src.shared.report_evidence.constants import (
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE,
    LEGACY_KIND_RELATED_PARTY,
)
from src.shared.report_evidence.source_kind_policy import (
    attempt_slots_for_formal_source_kind,
    document_slots_for_formal_source_kind,
)

_LOCATION_SLOT = "identity:official_location"


def _identity_raw(source_kind: str, slots: tuple[str, ...]) -> dict[str, object]:
    raw = _typed_raw_for_formal_kind(source_kind)
    raw[RAW_EVIDENCE_SECTION_IDS_KEY] = ("identity",)
    raw[RAW_EVIDENCE_SLOT_IDS_KEY] = slots
    raw["원문"] = "회사는 서울특별시 중구 예시로 10에 본점을 두고 소프트웨어를 주요 사업으로 영위합니다."
    return raw


def _flat(frags):
    return typed_fragments_from_raw(corp_id=_CORP_ID, frags=frags, filing_meta=_FILING_META)


def test_감사보고서_조각은_소재지_선택칸을_작성_입력_지원칸으로_싣는다() -> None:
    raw = _identity_raw(
        SOURCE_KIND_DART_AUDIT_REPORT,
        ("identity:corporate_identity", _LOCATION_SLOT),
    )

    conversion = _flat({1: raw})

    assert conversion.rejected_count == 0
    (fragment,) = conversion.fragments
    assert _LOCATION_SLOT in fragment.supported_claim_slots
    assert "identity:corporate_identity" in fragment.supported_claim_slots


def test_공식웹과_반기보고서는_소재지_선택칸을_주장할_수_없다() -> None:
    assert _LOCATION_SLOT not in document_slots_for_formal_source_kind(
        SOURCE_KIND_OFFICIAL_WEB_PAGE
    )
    assert _LOCATION_SLOT not in document_slots_for_formal_source_kind(
        SOURCE_KIND_DART_SEMIANNUAL_REPORT
    )
    raw = _identity_raw(SOURCE_KIND_OFFICIAL_WEB_PAGE, (_LOCATION_SLOT,))

    conversion = _flat({1: raw})

    assert conversion.fragments == ()
    assert conversion.rejected_count == 1


def test_조회기록_커버리지는_선택칸을_주장하지_않는다() -> None:
    for source_kind in (SOURCE_KIND_DART_AUDIT_REPORT, SOURCE_KIND_OFFICIAL_WEB_PAGE):
        assert _LOCATION_SLOT not in attempt_slots_for_formal_source_kind(source_kind)
    assert _LOCATION_SLOT in document_slots_for_formal_source_kind(
        SOURCE_KIND_DART_AUDIT_REPORT
    )


def test_관계법인_각주_legacy조각은_7장_packet에만_간다() -> None:
    frags = _all_legacy_frags()
    footnote_number = next(
        number
        for number, raw in frags.items()
        if raw["종류"] == LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE
    )
    frags[footnote_number]["원문위치"] = "평문 문자 10-80"

    packet_set = build_section_evidence_packet_set(
        corp_id=_CORP_ID,
        source_generation_sha256=_GENERATION,
        frags=frags,
        filing_meta=_FILING_META,
    )

    owners = {
        packet.section_id
        for packet in packet_set.packets
        if any(
            fragment.fragment_id == str(footnote_number)
            for fragment in packet.fragments
        )
    }
    assert owners == {"operations_partners"}


# ── real.py 배선: 엔진 보충 함수 호출과 계약 밖 반환의 무시 ─────────────


def test_파이프라인은_정본_종류이름과_관계자_앵커로_엔진_보충을_부른다() -> None:
    calls: list[dict[str, object]] = []

    def add_entity_scope_footnotes(frags, filing_text, **kwargs):
        calls.append({"frags": frags, "filing_text": filing_text, **kwargs})
        added = dict(frags)
        added[9] = {"종류": kwargs["kind"], "원문": "각주 원문", "원문위치": "평문 문자 1-5"}
        return added, 1

    engine = SimpleNamespace(add_entity_scope_footnotes=add_entity_scope_footnotes, FRAG_CHARS=321)
    frags = {4: {"종류": LEGACY_KIND_RELATED_PARTY, "원문": "관계자 원문"}}

    result, added = real._add_entity_scope_footnotes(engine, frags, "문서 원문")

    assert added == 1
    assert result[9]["종류"] == LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE
    assert calls[0]["kind"] == LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE
    assert calls[0]["anchor_kinds"] == (LEGACY_KIND_RELATED_PARTY,)
    assert calls[0]["max_chars"] == 321
    assert calls[0]["filing_text"] == "문서 원문"


def test_보충_함수가_없거나_계약이_다르면_원래_조각을_그대로_쓴다() -> None:
    frags = {4: {"종류": LEGACY_KIND_RELATED_PARTY, "원문": "관계자 원문"}}

    assert real._add_entity_scope_footnotes(SimpleNamespace(), frags, "원문") == (frags, 0)
    assert real._add_entity_scope_footnotes(MagicMock(), frags, "원문") == (frags, 0)
    assert real._add_entity_scope_footnotes(
        SimpleNamespace(add_entity_scope_footnotes=lambda *a, **k: ({}, -1)), frags, "원문"
    ) == (frags, 0)
    assert real._add_entity_scope_footnotes(
        SimpleNamespace(add_entity_scope_footnotes=lambda *a, **k: (frags, 1)), frags, ""
    ) == (frags, 0)
