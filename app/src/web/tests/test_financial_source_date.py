"""출처→저장→PDF/web 날짜 표시의 오프라인 조합 경계. 서버는 시작하지 않는다."""

import hashlib
import json
from dataclasses import replace
import re

import pytest

from src.features.composer.constants import DART_FINANCIAL_API_PREFIX
from src.features.composer.render import _build_source, _fragment_metas
from src.features.provenance.sources import has_valid_provenance_seal, source_status_display
from src.features.storage.reports import _citation_from_dict, _citation_to_dict
from src.features.export_pdf.logic import _source_status
from src.features.pipeline.port import Grade, Report, ReportSection
from src.features.report_standard.constants import SECTION_SPECS
from src.features.report_standard.public_projection import build_public_projection
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION
from src.web.tests.test_empty_section_display import render_result
from src.web.tests._visible_text import visible_text


@pytest.mark.parametrize("disclosed,collected,label", [
    ("2026-03-18", "2026-09-09", "2026-03-18 공시"),
    ("", "2026-09-09", "2026-09-09 확인"),
    ("", "", "기준일 미확인"),
])
def test_stored_source_signature_and_both_displays_preserve_date_meaning(disclosed, collected, label):
    text = f"{DART_FINANCIAL_API_PREFIX} 영업이익 12000(2025.01.01 ~ 2025.12.31)"
    raw = {7: {"종류": "재무", "원문": text, "문서일": collected,
               "financial_api_disclosed_at": disclosed}}
    source = _build_source(_fragment_metas(raw)[0], 7, "시험법인", ("past_changes",))
    assert source.disclosed_at == disclosed and source.collected_at == collected
    assert has_valid_provenance_seal(source)
    restored = _citation_from_dict(json.loads(json.dumps(_citation_to_dict(source))))
    assert restored == source and has_valid_provenance_seal(restored)
    assert restored.exact_evidence_hashes == [hashlib.sha256(text.encode()).hexdigest()]
    assert _source_status(restored).startswith(label)
    assert not has_valid_provenance_seal(replace(restored, disclosed_at="2026-03-19"))
    assert source_status_display(restored).startswith(label)
    report = Report(
        company="시험법인", job="", corp_type="", grade=Grade.PARTIAL,
        citations=[restored],
        sections=[ReportSection(spec.section_id, spec.title, display_number=spec.display_number)
                  for spec in SECTION_SPECS],
    )
    v2 = replace(report, schema_version=ENGINE_V2_SCHEMA_VERSION)
    sealed = replace(v2, public_projection=build_public_projection(v2))
    # 호출 횟수 대신 v1·v2·봉인 표시의 실제 부록 행에서 날짜와 그 뜻을 확인한다.
    for variant in (report, v2, sealed):
        html = render_result(variant)
        row = re.search(r'<tr id="src7">(.*?)</tr>', html, flags=re.DOTALL)
        assert row is not None
        text = visible_text(row.group(0))
        assert label in text
        if disclosed:
            assert collected not in text, "공시일 대신 수집일을 표시하면 안 됩니다"
