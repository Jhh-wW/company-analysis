"""앱 사업 범위 문맥 계약의 변조 차단과 엔진 사본 정합성."""
import hashlib
import json
from pathlib import Path

import pytest

from src.shared.report_evidence.section_context import parse_section_context, section_context_fingerprint


def _raw():
    text = "[장비사업부문]"
    fragment = "국내 고객에게 직접 판매한다."
    document = text + "\n" + fragment
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    item = {
        "version": "source-section-context-v1", "document_id": "document-a",
        "document_sha256": digest(document), "text": text,
        "location": f"0-{len(text)}", "text_sha256": digest(text),
        "scope_location": f"0-{len(document)}",
        "fragment_location": f"{len(text)+1}-{len(document)}", "fragment_sha256": digest(fragment),
    }
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")), document


def test_business_scope_is_bound_to_document_and_fragment_but_not_actor():
    raw, document = _raw()
    item = parse_section_context(raw, document_text=document)
    assert item["text"] == "[장비사업부문]"
    assert "actor" not in item
    assert section_context_fingerprint(raw) == hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.parametrize("key,value", [
    ("document_id", "document-b"), ("document_sha256", "f" * 64),
    ("fragment_location", "0-4"), ("fragment_sha256", "e" * 64),
])
def test_reassignment_to_other_evidence_is_rejected(key, value):
    raw, _ = _raw()
    with pytest.raises(ValueError):
        parse_section_context(raw, **{key: value})


def test_duplicate_key_or_noncanonical_json_cannot_change_context_hash():
    raw, _ = _raw()
    with pytest.raises(ValueError):
        parse_section_context(raw.replace("{", '{ "version":"source-section-context-v1",', 1))
    with pytest.raises(ValueError):
        parse_section_context(raw + " ")


def test_mirror_parser_and_constants_are_identical():
    root = next(path for path in Path(__file__).resolve().parents if (path / "analysis_engine").is_dir())
    app = root / "app/src/shared/report_evidence"
    engine = root / "analysis_engine/src/features/evidence_collection"
    for name in ("section_context.py", "section_context_constants.py"):
        source = (app / name).read_text(encoding="utf-8").replace("src.shared.report_evidence", "features.evidence_collection")
        assert source == (engine / name).read_text(encoding="utf-8")
