"""선택 후보 칸 운반 — 원문이 뒷받침하는 소재지를 살리되 필수 커버리지로 세지 않는다.

★ 왜 필요한가(2026-09-23 4차 실측) — 수집기가 「회사의 개요」 문단에 소재지 칸을
  붙여도 장 선택이 필수 수집 칸만 남기면 작가 입력의 지원 칸에서 사라져, 정확한
  소재지 문장이 검수 전에 탈락한다. 반대로 선택 칸이 필수 칸 충족·장 준비로
  세이면 자료가 모자란 장이 준비된 것처럼 보인다. 두 방향을 함께 고정한다.
"""

from __future__ import annotations

import pytest

from src.features.chapter_evidence.normalize import (
    normalize_documents,
    normalize_fragments,
)
from src.features.chapter_evidence.produce import produce_chapter_evidence_candidates
from src.features.chapter_evidence.select import select_section_fragments
from src.features.chapter_evidence.tests.fixtures import (
    make_attempt,
    make_document,
    make_fragment,
    sha256_of,
)
from src.shared.report_evidence.constants import EvidenceReadiness
from src.shared.report_evidence.logic import build_section_bundle
from src.shared.report_evidence.models import EvidenceFragment
from src.shared.report_evidence.policy import (
    OPTIONAL_CANDIDATE_SLOTS_BY_SECTION,
    REQUIRED_EVIDENCE_SECTION_IDS,
    candidate_slots_for,
    collector_slots_for,
    optional_candidate_slots_for,
    required_slots_for,
)
from src.shared.report_evidence.source_kind_policy import (
    FormalSourceKindContractError,
    validate_formal_candidate_sources,
)

_COMPANY = "00000001"
_DOC = "dart_audit_report:20260414000001"
_LOCATION = "identity:official_location"
_CORPORATE = "identity:corporate_identity"
_DEFINITION = "identity:business_definition"


def _fragment(fragment_id: str, slots: tuple[str, ...], text: str, score: int) -> dict[str, object]:
    raw = make_fragment(
        company_id=_COMPANY,
        fragment_id=fragment_id,
        document_id=_DOC,
        section_id="identity",
        slot_id=slots[0],
        text=text,
        score_millis=score,
    )
    raw["covered_slot_ids"] = slots
    return raw


def _produce(fragments: list[dict[str, object]], source_kind: str = "dart_audit_report"):
    document = make_document(
        company_id=_COMPANY,
        document_id=_DOC,
        source_kind=source_kind,
        exact_evidence_hashes=tuple(sha256_of(str(item["text"])) for item in fragments),
    )
    attempt = make_attempt(
        company_id=_COMPANY,
        attempt_id=f"document:{_DOC}",
        source_kind=source_kind,
        slot_ids=collector_slots_for("identity"),
        state="OK",
        reason_code="document_fetch_ok",
    )
    candidates = produce_chapter_evidence_candidates(
        company_id=_COMPANY,
        company_type="audit_only",
        documents=[document],
        fragments=fragments,
        attempts=[attempt],
    )
    return {candidate.section_id: candidate for candidate in candidates}


_OVERVIEW = "회사는 서울특별시 중구 예시로 10에 본점을 두고 있으며 설립된 주식회사입니다."


# ── 정책 ──────────────────────────────────────────────────


def test_선택_후보_칸은_필수_칸과_겹치지_않고_수집칸_뒤에_붙는다() -> None:
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        optional = optional_candidate_slots_for(section_id)
        assert set(optional).isdisjoint(required_slots_for(section_id))
        assert candidate_slots_for(section_id) == (
            *collector_slots_for(section_id),
            *optional,
        )
    assert OPTIONAL_CANDIDATE_SLOTS_BY_SECTION == {"identity": (_LOCATION,)}


# ── 살릴 것: 선택 칸 운반 ──────────────────────────────────


def test_감사보고서_조각의_소재지_선택칸은_장_후보에_그대로_실린다() -> None:
    candidates = _produce([_fragment("f1", (_CORPORATE, _LOCATION), _OVERVIEW, 500)])

    (fragment,) = candidates["identity"].fragments
    assert _LOCATION in fragment.covered_slot_ids
    validate_formal_candidate_sources(candidates.values())


def test_필수_대표_다음에_선택_사실이_고득점_반복근거보다_먼저_담긴다() -> None:
    """예산이 모자랄 때 선택 칸 대표가 같은 칸 반복 근거에 밀리지 않는다."""

    text_length = 60
    representative = _fragment("f1", (_CORPORATE,), "가" * text_length, 1000)
    definition = _fragment("f2", (_DEFINITION,), "나" * text_length, 900)
    repeated = _fragment("f3", (_CORPORATE,), "다" * text_length, 950)
    location_only = _fragment("f4", (_LOCATION,), "라" * text_length, 250)
    fragments = normalize_fragments(
        [representative, definition, repeated, location_only]
    )
    documents = normalize_documents(
        [
            make_document(
                company_id=_COMPANY,
                document_id=_DOC,
                source_kind="dart_audit_report",
                exact_evidence_hashes=tuple(fragment.text_sha256 for fragment in fragments),
            )
        ]
    )

    selection = select_section_fragments(
        section_id="identity",
        company_id=_COMPANY,
        documents=documents,
        fragments=fragments,
        max_chars=text_length * 3,
    )

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f1", "f2", "f4"]
    assert sum(len(fragment.text) for fragment in selection.fragments) <= text_length * 3
    assert "budget_truncated_fragments:1" in selection.reason_codes


#: 필수 대표 60+60자 뒤 80자가 남는 예산 — 반복 근거 80자와 작은 소재지 60자 중
#: 하나만 들어간다. 180자로 두면 반복 근거가 원래 안 맞아 «첫 대표만 시도» 옛
#: 구현도 통과하므로 시험이 대체 동작을 가르지 못한다.
_FALLBACK_BUDGET_CHARS = 200


def test_선택_대표가_예산_초과시_작은_후보로_대체한다() -> None:
    """상위 선택 대표가 안 맞으면 예산에 맞는 다른 후보를 찾아야 한다.

    필수 대표 120자 뒤 80자가 남는다. 소재지 상위 100자는 안 맞고, 하위 60자는
    맞는다. 첫 대표만 시도하고 포기하면 2단계에서 고득점 반복 근거 80자가 남은
    자리를 차지해 소재지를 잃는다.
    """

    text_length = 60
    representative = _fragment("f1", (_CORPORATE,), "가" * text_length, 1000)
    definition = _fragment("f2", (_DEFINITION,), "나" * text_length, 900)
    location_big = _fragment("f3", (_LOCATION,), "다" * 100, 500)
    location_small = _fragment("f4", (_LOCATION,), "라" * text_length, 250)
    repeated = _fragment("f5", (_CORPORATE,), "마" * 80, 950)
    fragments = normalize_fragments(
        [representative, definition, location_big, location_small, repeated]
    )
    documents = normalize_documents(
        [
            make_document(
                company_id=_COMPANY,
                document_id=_DOC,
                source_kind="dart_audit_report",
                exact_evidence_hashes=tuple(fragment.text_sha256 for fragment in fragments),
            )
        ]
    )

    selection = select_section_fragments(
        section_id="identity",
        company_id=_COMPANY,
        documents=documents,
        fragments=fragments,
        max_chars=_FALLBACK_BUDGET_CHARS,
    )

    ids = [fragment.fragment_id for fragment in selection.fragments]
    assert ids == ["f1", "f2", "f4"], "예산에 맞는 작은 소재지 후보가 담겨야 한다"
    assert "f5" not in ids, "반복 근거 80자가 소재지 자리를 차지하면 안 된다"
    assert sum(len(fragment.text) for fragment in selection.fragments) <= _FALLBACK_BUDGET_CHARS
    assert "budget_truncated_fragments:2" in selection.reason_codes


def test_선택_후보가_전부_예산_초과이면_포기하고_반복근거로_채운다() -> None:
    """맞는 후보가 정말 없으면 포기하고 2단계에서 나머지를 채운다."""

    text_length = 60
    representative = _fragment("f1", (_CORPORATE,), "가" * text_length, 1000)
    definition = _fragment("f2", (_DEFINITION,), "나" * text_length, 900)
    location_big = _fragment("f3", (_LOCATION,), "다" * 100, 500)
    repeated = _fragment("f4", (_CORPORATE,), "라" * text_length, 950)
    fragments = normalize_fragments(
        [representative, definition, location_big, repeated]
    )
    documents = normalize_documents(
        [
            make_document(
                company_id=_COMPANY,
                document_id=_DOC,
                source_kind="dart_audit_report",
                exact_evidence_hashes=tuple(fragment.text_sha256 for fragment in fragments),
            )
        ]
    )

    selection = select_section_fragments(
        section_id="identity",
        company_id=_COMPANY,
        documents=documents,
        fragments=fragments,
        max_chars=_FALLBACK_BUDGET_CHARS,
    )

    ids = [fragment.fragment_id for fragment in selection.fragments]
    assert "f3" not in ids, "예산 초과 소재지는 빠져야 한다"
    assert ids == ["f1", "f2", "f4"], "소재지 없이 반복근거가 남은 예산을 채운다"


# ── 막을 것: 커버리지 위장·후보 밖 칸 ─────────────────────


def test_선택_칸은_필수_칸_충족이나_장_준비로_세지_않는다() -> None:
    candidates = _produce([_fragment("f1", (_CORPORATE, _LOCATION), _OVERVIEW, 500)])
    identity = candidates["identity"]

    bundle = build_section_bundle(
        identity, required_slot_ids=collector_slots_for("identity")
    )

    assert identity.candidate_readiness is not EvidenceReadiness.READY
    assert bundle.readiness is not EvidenceReadiness.READY
    assert _LOCATION not in bundle.filled_slot_ids
    assert _LOCATION not in bundle.missing_slot_ids
    assert _DEFINITION in bundle.missing_slot_ids


def test_선택_후보가_아닌_칸은_장_선택에서_계속_잘린다() -> None:
    candidates = _produce(
        [
            _fragment("f1", (_CORPORATE, "identity:legal_scope"), _OVERVIEW, 500),
            _fragment("f2", ("identity:legal_scope",), "회사는 정관에 따라 인가를 받았습니다.", 500),
        ]
    )

    fragments = candidates["identity"].fragments
    assert [fragment.fragment_id for fragment in fragments] == ["f1"]
    assert fragments[0].covered_slot_ids == (_CORPORATE,)


def test_사전검사_묶음은_선택도_필수도_아닌_칸을_거절한다() -> None:
    candidates = _produce([_fragment("f1", (_CORPORATE, _LOCATION), _OVERVIEW, 500)])
    identity = candidates["identity"]
    fragment = identity.fragments[0]
    tampered = type(identity)(
        **{
            **{name: getattr(identity, name) for name in identity.__dataclass_fields__},
            "fragments": (
                EvidenceFragment(
                    **{
                        **{name: getattr(fragment, name) for name in fragment.__dataclass_fields__},
                        "covered_slot_ids": (_CORPORATE, "identity:legal_scope"),
                    }
                ),
            ),
        }
    )

    with pytest.raises(ValueError, match="필수 정책에 없는 의미 칸"):
        build_section_bundle(tampered, required_slot_ids=collector_slots_for("identity"))


def test_공식웹_문서가_소재지_선택칸을_주장하면_장후보_계약이_거절한다() -> None:
    candidates = _produce(
        [_fragment("f1", (_CORPORATE, _LOCATION), _OVERVIEW, 500)],
        source_kind="official_web_page",
    )

    with pytest.raises(FormalSourceKindContractError, match="소유하지 않은 의미 칸"):
        validate_formal_candidate_sources(candidates.values())
