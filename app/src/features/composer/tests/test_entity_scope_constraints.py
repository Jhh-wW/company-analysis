"""같은 공시 문서의 인용 밖 관계법인 회계범위 각주를 «제약으로만» 운반하는 경계.

긍정 근거(자기 인용 원문)는 바꾸지 않고, 정확히 같은 DART 접수 신원 + 등록 종류 +
인용 밖 조각일 때만 제약 context를 만든다. 법인 이름은 익명이다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.composer.entity_scope_constraints import (
    EntityScopeContext,
    build_entity_scope_contexts,
    is_canonical_dart_identity,
)
from src.features.composer.port import CollectedFragment
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE,
    LEGACY_KIND_RELATED_PARTY,
)

DOCUMENT = "document:dart.fss.or.kr:20260414000008"
OTHER_DOCUMENT = "document:dart.fss.or.kr:20250414000003"
RELATED = "회사는 가람 Holdings에 운영자금을 대여하였습니다."
FOOTNOTE = (
    "당기말 현재 구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 "
    "(*) 일반기업회계기준 경과규정에 따라 종속기업에서 제외되었습니다."
)


def _fragment(fid: str, kind: str, text: str, identity: str = DOCUMENT) -> CollectedFragment:
    return CollectedFragment(fid, kind, text, document_identity=identity)


def _by_id(*fragments: CollectedFragment) -> dict[str, CollectedFragment]:
    return {fragment.fragment_id: fragment for fragment in fragments}


def test_same_document_uncited_footnote_becomes_a_constraint_only():
    frags = _by_id(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED),
                   _fragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, FOOTNOTE))
    before = dict(frags)
    (context,) = build_entity_scope_contexts(("1",), frags)
    assert context == EntityScopeContext(DOCUMENT, {"1": RELATED}, {"2": FOOTNOTE})
    assert frags == before  # 조각·인용은 읽기만 한다
    with pytest.raises(TypeError):
        context.constraint_sources["3"] = "추가"  # type: ignore[index]


@pytest.mark.parametrize("footnote_identity", [OTHER_DOCUMENT, ""])
def test_other_or_empty_document_footnote_is_not_borrowed(footnote_identity):
    frags = _by_id(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED),
                   _fragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, FOOTNOTE, footnote_identity))
    assert build_entity_scope_contexts(("1",), frags) == ()


@pytest.mark.parametrize("identity", [
    "",                                              # legacy 직접 호출 — 대상 밖
    "url:https://dart.fss.or.kr/dsaf001/main.do",     # 접수번호 없는 URL 신원
    "document:dart.fss.or.kr:2026041400000",         # 13자리
    "document:dart.fss.or.kr:2026041400000A",
    "document:example.com:20260414000008",           # 다른 호스트
    f" {DOCUMENT}",                                  # 공백 변형
])
def test_non_canonical_identity_is_out_of_scope_even_if_both_match(identity):
    frags = _by_id(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED, identity),
                   _fragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, FOOTNOTE, identity))
    assert not is_canonical_dart_identity(identity)
    assert build_entity_scope_contexts(("1",), frags) == ()


def test_only_the_registered_footnote_kind_is_carried():
    frags = _by_id(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED),
                   _fragment("2", LEGACY_KIND_RELATED_PARTY, FOOTNOTE),
                   _fragment("3", "감사보고서 재무", FOOTNOTE))
    assert build_entity_scope_contexts(("1",), frags) == ()


def test_cited_footnote_stays_a_self_citation_not_a_constraint():
    frags = _by_id(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED),
                   _fragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, FOOTNOTE))
    assert build_entity_scope_contexts(("1", "2"), frags) == ()


def test_one_context_per_cited_document_in_citation_order():
    other_related = "회사는 나래 Studio와 용역 거래를 하였습니다."
    other_footnote = FOOTNOTE.replace("가람 Holdings", "나래 Studio")
    frags = _by_id(
        _fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED),
        _fragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, FOOTNOTE),
        _fragment("3", LEGACY_KIND_RELATED_PARTY, other_related, OTHER_DOCUMENT),
        _fragment("4", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, other_footnote, OTHER_DOCUMENT),
    )
    contexts = build_entity_scope_contexts(("3", "1", "3"), frags)
    assert [context.document_identity for context in contexts] == [OTHER_DOCUMENT, DOCUMENT]
    assert contexts[0].cited_sources == {"3": other_related}
    assert contexts[0].constraint_sources == {"4": other_footnote}
    assert contexts[1].constraint_sources == {"2": FOOTNOTE}


def test_unknown_citation_ids_are_ignored_without_inventing_sources():
    frags = _by_id(_fragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, FOOTNOTE))
    assert build_entity_scope_contexts(("9",), frags) == ()
    moved = replace(frags["2"], document_identity="")
    assert build_entity_scope_contexts(("2",), {"2": moved}) == ()


@pytest.mark.parametrize("field", ["document_content_sha256", "reporting_period"])
def test_known_full_document_hash_or_period_conflict_is_not_bound(field):
    related = replace(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED), **{field: "a" * 64})
    footnote = replace(_fragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, FOOTNOTE), **{field: "b" * 64})
    assert build_entity_scope_contexts(("1",), _by_id(related, footnote)) == ()
    same = replace(footnote, **{field: "a" * 64})
    assert len(build_entity_scope_contexts(("1",), _by_id(related, same))) == 1


def test_unknown_hash_or_period_is_neither_invented_nor_treated_as_conflict():
    related = replace(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED),
                      document_content_sha256="a" * 64, reporting_period="2025")
    footnote = _fragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, FOOTNOTE)  # 두 칸 모두 빈 legacy
    (context,) = build_entity_scope_contexts(("1",), _by_id(related, footnote))
    assert dict(context.constraint_sources) == {"2": FOOTNOTE}
    assert footnote.document_content_sha256 == "" and footnote.reporting_period == ""


#: 칸마다 서로 다른 두 «알려진 값» — 문서 전체 해시(발췌 해시 아님)·보고기간 대칭.
KNOWN_PAIRS = pytest.mark.parametrize(("field", "first", "second"), [
    ("document_content_sha256", "a" * 64, "b" * 64),
    ("reporting_period", "2025", "2024"),
], ids=["hash", "period"])
SECOND_FOOTNOTE = FOOTNOTE.replace("40,000", "41,000")


def _footnote(fid: str, text: str, identity: str = DOCUMENT, **values: str) -> CollectedFragment:
    return replace(_fragment(fid, LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, text, identity), **values)


@KNOWN_PAIRS
def test_footnotes_that_conflict_with_each_other_under_unknown_citation_make_no_context(field, first, second):
    """인용 값을 모르면 서로 다른 알려진 값의 각주 중 하나를 고를 근거가 없다(첫 값·다수결 금지)."""
    frags = _by_id(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED),
                   _footnote("2", FOOTNOTE, **{field: first}),
                   _footnote("3", SECOND_FOOTNOTE, **{field: second}))
    assert build_entity_scope_contexts(("1",), frags) == ()
    # 같은 값의 각주를 하나 더 둬도(다수결) 충돌은 그대로다.
    frags["4"] = _footnote("4", FOOTNOTE + " ", **{field: first})
    assert build_entity_scope_contexts(("1",), frags) == ()


@KNOWN_PAIRS
def test_known_citation_value_still_selects_only_the_matching_footnote(field, first, second):
    frags = _by_id(replace(_fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED), **{field: first}),
                   _footnote("2", FOOTNOTE, **{field: first}),
                   _footnote("3", SECOND_FOOTNOTE, **{field: second}))
    (context,) = build_entity_scope_contexts(("1",), frags)
    assert dict(context.constraint_sources) == {"2": FOOTNOTE}


@KNOWN_PAIRS
def test_same_known_values_or_unknown_values_keep_the_normal_context(field, first, second):
    related = _fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED)
    same = _by_id(related, _footnote("2", FOOTNOTE, **{field: first}),
                  _footnote("3", SECOND_FOOTNOTE, **{field: first}))
    unknown_mix = _by_id(related, _footnote("2", FOOTNOTE, **{field: first}), _footnote("3", SECOND_FOOTNOTE))
    for frags in (same, unknown_mix):
        (context,) = build_entity_scope_contexts(("1",), frags)
        assert dict(context.constraint_sources) == {"2": FOOTNOTE, "3": SECOND_FOOTNOTE}


@KNOWN_PAIRS
def test_conflicting_document_drops_only_its_own_context(field, first, second):
    other_related = "회사는 나래 Studio와 용역 거래를 하였습니다."
    other_footnote = FOOTNOTE.replace("가람 Holdings", "나래 Studio")
    frags = _by_id(
        _fragment("1", LEGACY_KIND_RELATED_PARTY, RELATED),
        _footnote("2", FOOTNOTE, **{field: first}),
        _footnote("3", SECOND_FOOTNOTE, **{field: second}),
        _fragment("4", LEGACY_KIND_RELATED_PARTY, other_related, OTHER_DOCUMENT),
        _footnote("5", other_footnote, OTHER_DOCUMENT, **{field: first}),
    )
    (context,) = build_entity_scope_contexts(("1", "4"), frags)
    assert context.document_identity == OTHER_DOCUMENT
    assert dict(context.cited_sources) == {"4": other_related}
    assert dict(context.constraint_sources) == {"5": other_footnote}
