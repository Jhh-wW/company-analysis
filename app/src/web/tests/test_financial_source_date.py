"""출처→저장→PDF/web 날짜 표시의 오프라인 조합 경계. 서버는 시작하지 않는다."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import re

from jinja2 import Environment
import pytest

from src.features.composer.constants import DART_FINANCIAL_API_PREFIX
from src.features.composer.render import _build_source, _fragment_metas
from src.features.provenance.sources import has_valid_provenance_seal, source_status_display
from src.features.storage.reports import _citation_from_dict, _citation_to_dict
from src.features.export_pdf.logic import _source_status


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
    # 2026-09-22: 웹 템플릿의 날짜 분기 두 곳이 세 채널 공용 도우미
    # ``source_status_display(c)`` 호출로 바뀌었다. 템플릿이 그 도우미를 두 곳에서
    # 쓰는지와, 도우미가 같은 날짜 뜻을 내는지를 나눠 확인한다. 네트워크나 앱 설정은 없다.
    path = Path(__file__).resolve().parents[1] / "templates/result.html"
    assert path.read_text(encoding="utf-8").count("source_status_display(c)") == 2
    assert source_status_display(restored).startswith(label)
