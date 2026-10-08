"""공식 DART 부모·부분조각의 정확 합집합과 중첩 원문 결속."""
import copy
import hashlib

import pytest

from src.web.official_evidence_adapter import _classified_evidence_location_bindings


def _envelope():
    text = "회사는 사업 제약을 확인하여 제품 공급을 개선하고 있습니다."
    start = text.index("사업")
    end = text.index("하여") + len("하여")
    rows = []
    for index, (left, right) in enumerate(((0, len(text)), (start, end))):
        own = text[left:right]
        rows.append({"company_id": "00000000", "document_id": "dart_business_report:20260101000001",
            "fragment_id": f"dart_business_report:20260101000001:frag{index}",
            "location": f"{left}-{right}", "text": own,
            "text_sha256": hashlib.sha256(own.encode()).hexdigest()})
    return {"documents": [{"company_id": "00000000", "document_id": rows[0]["document_id"],
        "source_kind": "dart_business_report", "canonical_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260101000001",
        "usable_ranges": [{"start": 0, "end": len(text)}],
        "exact_evidence_hashes": [row["text_sha256"] for row in rows],
        "exact_evidence_bindings": [{"location": row["location"], "text_sha256": row["text_sha256"]} for row in rows]}],
        "fragments": rows}


def test_parent_and_exact_subset_preserve_one_range():
    _classified_evidence_location_bindings(_envelope(), company_id="00000000")


@pytest.mark.parametrize("mutation", ("overlap_text", "range_gap", "sidecar", "outside"))
def test_containment_does_not_replace_exact_provenance(mutation):
    envelope = copy.deepcopy(_envelope())
    document = envelope["documents"][0]
    fragment = envelope["fragments"][1]
    if mutation == "overlap_text":
        old_sha = fragment["text_sha256"]
        fragment["text"] = "가" * len(fragment["text"])
        fragment["text_sha256"] = hashlib.sha256(fragment["text"].encode()).hexdigest()
        document["exact_evidence_hashes"].remove(old_sha)
        document["exact_evidence_hashes"].append(fragment["text_sha256"])
        document["exact_evidence_bindings"][1]["text_sha256"] = fragment["text_sha256"]
    elif mutation == "range_gap":
        document["usable_ranges"][0]["end"] += 1
    elif mutation == "sidecar":
        document["exact_evidence_bindings"].pop()
    else:
        fragment["location"] = f"100-{100 + len(fragment['text'])}"
        document["exact_evidence_bindings"][1]["location"] = fragment["location"]
    with pytest.raises(ValueError):
        _classified_evidence_location_bindings(envelope, company_id="00000000")
