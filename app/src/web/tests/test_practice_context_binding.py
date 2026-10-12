"""정렬·중복과 무관하게 문서에 봉인한 예시 문맥을 정확 대조한다."""
import copy
import hashlib
from dataclasses import replace

import pytest

from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fragments import build_fragments
from src.features.homepage.wide_types import WideCollectionResult, WideDocumentIdentity
from src.web.official_evidence_adapter import _classified_evidence_location_bindings


def _envelope():
    ranges = ("예를 들어 이렇게 요청합니다.",) + tuple(
        f"요청 항목 {index}의 완료 상태를 검수합니다." for index in range(10)
    )
    document = WideDocumentIdentity(
        company_id="c1", document_id="d1", canonical_url="https://example.org/guide",
        source_kind="official_web_page", publisher="가람소프트", title="교육 예시",
        published_on="", collected_at="2026-10-10",
        content_sha256=hashlib.sha256("\n".join(ranges).encode()).hexdigest(),
        identity_binding="root", usable_ranges=ranges, collector_version="homepage-wide-collector/5",
        parser_version="homepage-wide-parser/2", requirement="REQUIRED", source_tier="TIER_1_OFFICIAL",
    )
    fragments = build_fragments(document, company_id="c1")
    assert len(fragments) >= 10
    # 같은 원문이 여러 의미칸에 배정되더라도 문맥은 하나로 봉인한다.
    fragments += (replace(fragments[0], fragment_id=fragments[0].fragment_id + ":other"),)
    return to_evidence_mappings(
        result=WideCollectionResult(company_id="c1", documents=(document,), attempts=()),
        fragments=fragments,
    )


def test_multiple_sections_and_two_digit_positions_keep_canonical_bindings():
    envelope = _envelope()
    _classified_evidence_location_bindings(envelope, company_id="c1")
    envelope["fragments"].reverse()
    _classified_evidence_location_bindings(envelope, company_id="c1")


@pytest.mark.parametrize("change", ["missing", "one_duplicate_missing", "replaced", "extra"])
def test_lost_or_changed_context_is_not_silently_accepted(change):
    envelope = copy.deepcopy(_envelope())
    if change == "missing":
        for fragment in envelope["fragments"]:
            fragment.pop("practice_context_json", None)
    elif change == "one_duplicate_missing":
        envelope["fragments"][-1].pop("practice_context_json")
    elif change == "replaced":
        envelope["documents"][0]["exact_practice_context_bindings"][0]["practice_context_sha256"] = "f" * 64
    else:
        envelope["documents"][0]["exact_practice_context_bindings"].append(
            envelope["documents"][0]["exact_practice_context_bindings"][0].copy())
    with pytest.raises(ValueError, match="예시·안내 문맥"):
        _classified_evidence_location_bindings(envelope, company_id="c1")
