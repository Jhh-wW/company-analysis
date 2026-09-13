"""공식 사업부문 별칭이 실제 DART 법인 후보로 연결되는지 검증한다."""

from __future__ import annotations

from src.features.business_candidate import logic
from src.features.business_candidate.dart_identity import (
    DartCompanyRecord,
    build_dart_company_index,
    generate_dart_company_matches,
)
from src.features.business_candidate.official_relations import (
    OFFICIAL_ALIAS_MATCH_KIND,
    OFFICIAL_RELATION_DATA_VERSION,
    SK_AX_RELATION,
    official_alias_relation_for,
)


def _sk_index():
    return build_dart_company_index(
        [
            DartCompanyRecord(
                corp_code="00181712",
                corp_name="SK",
                corp_eng_name="SK Inc.",
                stock_code="034730",
                modify_date="20260914",
            )
        ]
    )


def test_official_alias_requires_registered_dart_entity():
    relation = official_alias_relation_for("SK AX")

    assert relation is SK_AX_RELATION
    assert relation.corp_code == "00181712"
    assert relation.stock_code == "034730"
    assert relation.checked_on == "2026-09-14"
    assert relation.data_version == OFFICIAL_RELATION_DATA_VERSION
    assert relation.source_urls == (
        "https://www.sk-inc.com/kr/ir/faq.aspx",
        "https://www.skax.co.kr/company/news-room/3284",
    )

    matches = generate_dart_company_matches(_sk_index(), "SK AX")
    assert len(matches) == 1
    assert matches[0].record.corp_code == relation.corp_code
    assert matches[0].record.stock_code == relation.stock_code
    assert matches[0].match_kind == OFFICIAL_ALIAS_MATCH_KIND
    assert generate_dart_company_matches(build_dart_company_index([]), "SK AX") == ()

    # 부분 문자열·문자열 치환으로 다른 법인을 끌어오지 않는다.
    assert generate_dart_company_matches(_sk_index(), "SK AX CIC") == ()
    mixed_index = build_dart_company_index(
        [
            DartCompanyRecord(
                "00181712", "SK", "SK Inc.", "034730", "20260914"
            ),
            # 색인에 별도 법인이 있어도 문자열 치환으로 target의 식별자가
            # 바뀌지 않고, 공식 별칭 후보가 우선되어야 한다.
            DartCompanyRecord("00999999", "SK AX CIC", "", "", "20260914"),
        ]
    )
    mixed_matches = generate_dart_company_matches(mixed_index, "SK AX")
    assert mixed_matches[0].record.corp_code == relation.corp_code
    assert mixed_matches[0].record.stock_code == relation.stock_code


class _DartProvider:
    costs_money = False
    provider_name = "DART"

    def search(self, **_kwargs):
        return [
            logic.RawBusinessCandidate(
                candidate_name="SK",
                source_label="전자공시(DART) 기업개황",
                source_url="https://opendart.fss.or.kr/",
                provider_name="DART",
                candidate_ref="00181712",
                stock_code="034730",
                modify_date="20260914",
                english_name="SK Inc.",
                name_match_kind=OFFICIAL_ALIAS_MATCH_KIND,
            )
        ]


def test_alias_candidate_preserves_selection_and_report_scope(monkeypatch):
    monkeypatch.setattr(logic, "_RATE_HISTORY", logic.budget_logic.RateHistory())

    result = logic.resolve_candidates(
        _DartProvider(),
        company="SK AX",
        address_hint="",
        rate_key="official-sk-ax",
        now=1.0,
    )

    assert result.status is logic.ResolutionStatus.OK
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.candidate_ref == "00181712"
    assert candidate.stock_code == "034730"
    assert candidate.name_match_kind == OFFICIAL_ALIAS_MATCH_KIND
    assert candidate.evidence[:3] == (
        SK_AX_RELATION.relation,
        SK_AX_RELATION.report_scope,
        "관계 확인 근거: " + " · ".join(SK_AX_RELATION.source_urls),
    )
    chips = logic.candidate_match_chips(
        candidate,
        query="SK AX",
        address_hint="",
    )
    assert chips[0].label == "공식 사업부문 별칭"
    assert chips[0].tone == logic.CHIP_TONE_PART
