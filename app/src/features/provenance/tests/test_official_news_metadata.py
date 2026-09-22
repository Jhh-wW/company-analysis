"""목록 조각이 공개 출처의 세 생성 경로까지 같은 값으로 간다."""
from dataclasses import replace
from datetime import date

import pytest

from src.features.company_comparison.official_sources import dart_profile_attestation_material
from src.features.composer import render, public_manifest
from src.features.pipeline.comparison_transport import _formal_source_from_document
from src.features.pipeline.evidence_transport import typed_fragments_from_raw
from src.features.pipeline.official_evidence_transport_adapter import merge_official_evidence_fragments
from src.features.pipeline.tests.test_comparison_transport_dates import _formal_result, _CORP_CODE
from src.features.provenance.citations import build_citations
from src.features.provenance.sources import (
    Source,
    SourceKind,
    has_valid_provenance_seal,
    official_web_source_fields,
    source_label_display,
    source_status_display,
)


TITLE = "뤼튼테크놀로지스, 상장 주관사 선정 착수"
URL = "https://wrtn.io/news/뤼튼테크놀로지스-상장-주관사-선정-착수/"
COMPANY = "주식회사 뤼튼테크놀로지스"


@pytest.mark.parametrize("has_metadata", [False, True])
def test_공식_목록_Source와_부록_표기가_세_경로에서_같다(has_metadata):
    result = _formal_result("official_web_page")
    attestation_id, evidence = dart_profile_attestation_material(
        profile={"status": "000", "corp_code": _CORP_CODE, "corp_name": COMPANY, "hm_url": "https://wrtn.io"},
        corp_code=_CORP_CODE, company_name=COMPANY,
    )
    candidates = []
    for candidate in result.candidates:
        documents = tuple(replace(
            document, canonical_url="https://wrtn.io/news/", publisher="wrtn.io",
            title="뤼튼테크놀로지스", collected_at="2026-09-22",
            domain_attestation_source_id=attestation_id, domain_attestation_evidence=evidence,
        ) for document in candidate.documents)
        fragments = tuple(replace(
            fragment, location=URL if has_metadata else "https://wrtn.io/news/ · 목록 2번째 항목",
            item_title=TITLE if has_metadata else "",
            item_published_on="2026-09-02" if has_metadata else "",
            item_url=URL if has_metadata else "",
        ) for fragment in candidate.fragments)
        candidates.append(replace(candidate, documents=documents, fragments=fragments))
    result = replace(result, candidates=tuple(candidates))
    raw, _ = merge_official_evidence_fragments({}, result)
    converted = typed_fragments_from_raw(corp_id=_CORP_CODE, frags=raw, filing_meta=None)
    fragment = converted.fragments[0]
    number = int(fragment.fragment_id)
    rendered = render._build_source(render._fragment_metas((fragment,))[0], number, COMPANY, [])
    expected = public_manifest._expected_source(fragment, number=number, company_name=COMPANY, used_in=[], filing_meta=None)
    document = next(document for candidate in result.candidates for document in candidate.documents)
    compared = _formal_source_from_document(
        number=number, raw=raw[number], document=document, company_name=COMPANY,
        collected_on="2026-09-22", evidence_hashes=rendered.evidence_hashes, exact_hashes=rendered.exact_evidence_hashes,
    )
    legacy = build_citations({number: {**raw[number], "종류": "홈페이지"}}, filing=None, collected_on=date(2026, 9, 22), company_publisher=COMPANY)[0]
    for source in (rendered, expected, compared, legacy):
        assert has_valid_provenance_seal(source)
        assert source.title == (TITLE if has_metadata else "")
        assert source.published_at == ("2026-09-02" if has_metadata else "")
        assert source.url == (URL if has_metadata else "https://wrtn.io/news/")
        assert "#" not in source.location
        if has_metadata:
            assert source.location == URL
            assert source_label_display(source) == TITLE + " · 회사 공식 웹"
            assert source_status_display(source) == "2026-09-02 발표 · 회사 공식 웹 · 2026-09-22 확인"
        else:
            assert source_label_display(source) == "뤼튼테크놀로지스 · 주식회사 뤼튼테크놀로지스"


def test_옛_목록_가짜_앵커는_원문_위치에서_제거한다():
    source = build_citations({27: {"종류": "홈페이지", "원문": TITLE,
        "출처": "https://wrtn.io/news/", "원문위치": "https://wrtn.io/news/#1"}},
        filing=None, collected_on=date(2026, 9, 22), company_publisher=COMPANY)[0]
    assert source.title == ""
    assert source.location == "https://wrtn.io/news/ · 목록 2번째 항목"


def test_공식_IR_갈래도_옛_가짜_앵커를_지운다():
    """★ 앵커 청소가 IR 분기 «뒤»에 있으면 IR 출처만 `#3`이 그대로 인쇄된다.

    `#N`은 옛 수집기가 목록 순번을 붙이려고 만든 글자라 어느 갈래로 가든
    원문에 없는 자리를 가리킨다. 갈래마다 따로 청소하면 한 갈래가 빠진다.
    """

    fields = official_web_source_fields(
        source_type="회사 공식 IR", title="2025년 3분기 실적발표",
        published_at="2026-01-02", url="https://wrtn.io/ir",
        location="https://wrtn.io/ir#3",
    )

    assert "#" not in fields["location"]
    assert fields["location"] == "https://wrtn.io/ir · 목록 4번째 항목"
    # IR의 발행일은 공식 확정값이다 — 위치가 목록 문구로 바뀌어도 지우지 않는다
    # (발표일 지우기는 제목까지 함께 버리는 일반 웹 갈래에만 있다).
    assert fields["published_at"] == "2026-01-02"
    assert fields["title"] == "2025년 3분기 실적발표"


def test_공식_웹_상태_열은_호스트를_버리지_않는다():
    """★ 「더 많이 말하는 쪽이 정본」 — 보여 줄 수 있는 칸을 빼지 않는다.

    공식 웹 + 발표일 갈래는 발표일·채널·확인일만 적고 호스트를 버렸다.
    호스트는 독자가 «어느 사이트에서 왔는지» 아는 유일한 칸이다.
    """

    # kind는 실제 네 생성 경로가 공식 웹 출처에 붙이는 값과 같다(실측: OTHER).
    source = Source(
        number=3, kind=SourceKind.OTHER, label="뉴스", source_type="회사 공식 웹",
        published_at="2026-09-02", collected_at="2026-09-22", domain="wrtn.io",
    )

    assert source_status_display(source) == (
        "2026-09-02 발표 · 회사 공식 웹 · wrtn.io · 2026-09-22 확인"
    )
