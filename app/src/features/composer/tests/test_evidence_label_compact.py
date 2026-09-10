"""조각 라벨 다이어트를 못 박는다 — 종류·문서명 반복을 줄이되 원문·인용은 그대로.

★ 왜 이 시험이 있나 (corpus-measure 실측, 2026-09-10)
  flat 모드는 아홉 장이 같은 조각 블록을 공유해, 조각마다 반복되는
  종류 라벨(예: ``dart_business_report``)·문서명(예: ``사업보고서
  (2025.12)``)·숫자 원문위치(예: ``18656-19172``)가 공유 원문 블록의
  19.9%(SM)~24.5%(우리은행)를 차지했다. 이 시험은 그 반복을 줄이는 렌더
  변경이 (1) 조각 원문·번호·인용 검증에 쓰이는 값은 한 글자도 건드리지
  않고, (2) 실제로 유의미하게 줄어드는지를 지킨다.

★ 지키는 것
  (a) 숫자 오프셋 원문위치(``\\d+-\\d+`` 또는 숫자만)는 라벨에서 빠지고,
      글자로 된 위치(제목·문단 경로)는 그대로 남는다.
  (b) ``report_evidence.constants``의 모든 공식 문서 종류가 짧은 표시명으로
      바뀐다 — 빠진 종류가 있으면 이 시험이 즉시 깨진다.
  (c) 「문서 목록」 머리말은 (종류, 문서명) 조합당 «한 번»만 나오고, 조각
      줄은 그 목록의 기호만 가리킨다.
  (d) 기호 배정은 같은 입력에 대해 항상 같다(결정적).
  (e) 반복이 큰 합성 200조각 블록에서 옛 렌더 대비 12% 이상 줄어든다.
  (f) 「문서 목록」은 ``shared_evidence_prefix=True``의 공유 앞부분
      (``cache_prefix_chars`` 범위) 안에 들어간다 — 캐시 적중을 깨지 않는다.

★ 2026-09-10 추가 — 세 다이어트를 독립적으로 끌 수 있어야 한다(팀장 지시,
  유료 비교 실행에서 품질이 떨어지면 하나씩 되돌려 원인을 가르기 위함).
  (g) ``EVIDENCE_LABEL_*`` 상수 하나만 꺼도 그 다이어트만 사라진다.
  (h) ④만 켠 상태(②③ 끔)의 렌더는 「옛 형식에서 숫자 원문위치만 빠진」
      모양과 글자 그대로 같다.
  (i) 셋 다 끄면 2026-09-10 변경 «이전»과 바이트가 같다.

★ 2026-09-10 추가 — flat 렌더 경로(``fragments_from_raw``)와 packet 렌더
  경로(``evidence_transport._collected_fragment_from_raw``)가 실제로 같은
  raw_kind로 수렴하는지, 진짜 변환 함수로 확인한다(corpus-measure 실측
  코멘트: ``formal_source_kind``는 typed raw dict에서만 채워지고, 두 실측
  실행 모두 flat 경로로 강등됐다는 지적에 대한 코드 레벨 답).
  (j) typed(공식 문서) raw dict → flat/packet 둘 다 ``formal_source_kind``가
      같은 SOURCE_KIND_* 값으로 수렴(단, flat 변환 자체는 그 필드를 항상
      비운다 — ``kind`` 폴백이 같은 값을 낸다).
  (k) legacy(한글 종류) raw dict → flat/packet 둘 다 ``formal_source_kind``는
      항상 비고 ``kind``가 한글 그대로 라벨이 된다.

★ 2026-09-10 추가(2차, 독립 검토 P2 3건) — 유료 비교 실행 전에 고친다.
  (l) 작가가 산문에 ``(문서 A)``를 흉내 내면 검출·제거된다(대괄호 인용
      흉내와 같은 방어). 일반 괄호 문구는 오탐하지 않는다.
  (m) 조각 라벨의 ``(문서 X)``가 문서 목록의 X를 가리킨다는 안내가 공유
      앞부분에 한 번 들어간다.
  (n) 문서 목록에서 표시가 같아지는 두 줄 이상이 있으면 원래 종류 문자열을
      괄호로 덧붙여 구분한다. 결정성은 유지된다.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from src.features.composer import logic as composer_logic
from src.features.composer.constants import (
    DOCUMENT_LIST_GUIDE,
    DOCUMENT_LIST_HEAD,
    EVIDENCE_LABEL_DOCUMENT_HEADER,
    EVIDENCE_LABEL_OMIT_NUMERIC_LOCATION,
    EVIDENCE_LABEL_SHORT_KIND,
    PROMPT_FRAGMENTS_HEAD,
    PROMPT_FRAGMENT_LOCATION_LABEL,
    SOURCE_KIND_DISPLAY_NAMES,
)
from src.features.composer.logic import (
    _document_symbol,
    _is_numeric_offset_location,
    _render_fragments,
    _strip_inline_citation_markers,
    build_section_prompt,
    contains_inline_citation_marker,
)
from src.features.composer.news_block import _is_news_fragment
from src.features.composer.news_usage import news_metadata
from src.features.composer.port import (
    CollectedFragment,
    filing_meta_from_raw,
    fragments_from_raw,
)
from src.features.company_comparison.official_sources import (
    dart_profile_attestation_material,
)
from src.features.pipeline import evidence_transport
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
    SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS,
    EvidenceReadiness,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult

_ALL_EVIDENCE_SOURCE_KINDS = FORMAL_DOCUMENT_SOURCE_KINDS | SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS


def _fragment(**overrides) -> CollectedFragment:
    fields = {
        "fragment_id": "1",
        "kind": "dart_business_report",
        "text": "본문 원문 그대로.",
        "formal_source_kind": "dart_business_report",
        "document_title": "사업보고서 (2025.12)",
        "location": "",
    }
    fields.update(overrides)
    return CollectedFragment(**fields)


# ══════════════════════════════════════════════════════════
# (a) 숫자 오프셋 원문위치는 빠지고, 글자 위치는 남는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "location,expected_numeric",
    [
        ("18656-19172", True),
        ("500-900", True),
        ("12345", True),
        ("II. 사업의 내용", False),
        ("PDF p.1 1문단", False),
        ("회사소개 > 개요", False),
        ("12345-abc", False),  # 글자가 섞이면 숫자 오프셋이 아니다
        ("", False),
    ],
)
def test_숫자_오프셋_판정이_실제_생산기의_모양과_맞는다(location, expected_numeric):
    # analysis_engine/evidence_collection/collect.py의
    # `location=f"{candidate.start}-{candidate.end}"` 모양만 True여야 한다.
    assert _is_numeric_offset_location(location) is expected_numeric


def test_숫자_오프셋_위치는_라벨에서_빠진다():
    fragment = _fragment(location="18656-19172")
    prompt = _render_fragments([fragment])

    assert "18656-19172" not in prompt
    assert PROMPT_FRAGMENT_LOCATION_LABEL not in prompt


def test_글자로_된_위치는_라벨에_그대로_남는다():
    fragment = _fragment(location="II. 사업의 내용")
    prompt = _render_fragments([fragment])

    assert f"{PROMPT_FRAGMENT_LOCATION_LABEL}: II. 사업의 내용" in prompt


def test_숫자만_있는_위치도_빠진다():
    fragment = _fragment(location="12345")
    prompt = _render_fragments([fragment])

    assert "12345" not in prompt
    assert PROMPT_FRAGMENT_LOCATION_LABEL not in prompt


# ══════════════════════════════════════════════════════════
# (b) 모든 공식 source kind가 표시명으로 바뀐다 — 빠진 종류 없음
# ══════════════════════════════════════════════════════════


def test_report_evidence_종류_전부가_표시명_표에_있다():
    missing = _ALL_EVIDENCE_SOURCE_KINDS - SOURCE_KIND_DISPLAY_NAMES.keys()
    assert not missing, f"표시명이 없는 종류: {sorted(missing)}"


@pytest.mark.parametrize("kind", sorted(_ALL_EVIDENCE_SOURCE_KINDS))
def test_각_종류가_원문_대신_표시명으로_문서목록에_찍힌다(kind):
    fragment = _fragment(
        formal_source_kind=kind, kind=kind, document_title="", location=""
    )
    prompt = _render_fragments([fragment])
    expected_label = SOURCE_KIND_DISPLAY_NAMES[kind]

    assert f"A: {expected_label}" in prompt
    # 표시명이 원래 문자열과 다른 종류는(대부분) 원문 종류 문자열이 새지 않아야 한다.
    if expected_label != kind:
        assert kind not in prompt


def test_표에_없는_종류는_원래_문자열_그대로_보여준다():
    fragment = _fragment(
        formal_source_kind="", kind="사업내용", document_title="", location=""
    )
    prompt = _render_fragments([fragment])

    assert "A: 사업내용" in prompt


# ══════════════════════════════════════════════════════════
# (c) 문서 목록 머리말은 문서당 한 번, 조각 줄은 기호만
# ══════════════════════════════════════════════════════════


def test_문서목록_머리말은_문서_조합당_한_번만_나온다():
    fragments = [
        _fragment(fragment_id="1", document_title="사업보고서 (2025.12)"),
        _fragment(fragment_id="2", document_title="사업보고서 (2025.12)"),
        _fragment(fragment_id="3", document_title="사업보고서 (2025.12)"),
        _fragment(
            fragment_id="4",
            formal_source_kind="dart_semiannual_report",
            kind="dart_semiannual_report",
            document_title="반기보고서 (2025.06)",
        ),
    ]
    prompt = _render_fragments(fragments)

    assert prompt.count(DOCUMENT_LIST_HEAD) == 1
    # 같은 (종류, 문서명) 조합인 세 조각은 문서 목록에 한 줄만 남긴다.
    assert prompt.count("사업보고서 (2025.12)") == 1
    assert prompt.count("반기보고서 (2025.06)") == 1

    document_list_entries = re.findall(r"^[A-Z]+: .+$", prompt, re.MULTILINE)
    assert len(document_list_entries) == 2  # 두 문서 조합 → 두 줄


def test_조각_줄은_문서_기호만_쓰고_종류_문서명을_반복하지_않는다():
    fragments = [
        _fragment(fragment_id="1", document_title="사업보고서 (2025.12)"),
        _fragment(fragment_id="2", document_title="사업보고서 (2025.12)"),
    ]
    prompt = _render_fragments(fragments)

    fragment_lines = [
        line for line in prompt.splitlines() if line.startswith("[조각 ")
    ]
    assert len(fragment_lines) == 2
    for line in fragment_lines:
        assert re.match(r"^\[조각 \d+\] \(문서 [A-Z]+\) ", line)
        assert "dart_business_report" not in line
        assert "사업보고서 (2025.12)" not in line


def test_다른_문서는_다른_기호를_받는다():
    fragments = [
        _fragment(fragment_id="1", document_title="사업보고서 (2025.12)"),
        _fragment(
            fragment_id="2",
            formal_source_kind="dart_semiannual_report",
            kind="dart_semiannual_report",
            document_title="반기보고서 (2025.06)",
        ),
    ]
    prompt = _render_fragments(fragments)

    line_1 = next(line for line in prompt.splitlines() if line.startswith("[조각 1]"))
    line_2 = next(line for line in prompt.splitlines() if line.startswith("[조각 2]"))
    symbol_1 = re.search(r"\(문서 ([A-Z]+)\)", line_1).group(1)
    symbol_2 = re.search(r"\(문서 ([A-Z]+)\)", line_2).group(1)
    assert symbol_1 != symbol_2


def test_기호는_26개를_넘으면_두_글자로_넘어간다():
    assert _document_symbol(1) == "A"
    assert _document_symbol(26) == "Z"
    assert _document_symbol(27) == "AA"
    assert _document_symbol(28) == "AB"
    assert _document_symbol(52) == "AZ"
    assert _document_symbol(53) == "BA"


def test_지원_주장슬롯이_원문위치_뒤_괄호_끝에_그대로_온다():
    # 기존 정규식(`지원 주장슬롯: ([^)]+)\)`)이 계속 맞아야 한다 — 위치가
    # 바뀌면 이 정규식으로 읽는 다른 시험들이 원문위치까지 슬롯으로 읽는다.
    fragment = _fragment(
        location="II. 사업의 내용", supported_claim_slots=("identity:legal_entity",)
    )
    prompt = _render_fragments([fragment], show_supported_claim_slots=True)

    match = re.search(r"\[조각 1\] \([^\n]*지원 주장슬롯: ([^)]+)\)", prompt)
    assert match is not None
    assert match.group(1) == "identity:legal_entity"


def test_뉴스_조각은_메타데이터를_그대로_유지한다():
    fragment = _fragment(
        fragment_id="1",
        kind="news",
        formal_source_kind="news",
        document_title="회사 신제품 출시 기사",
        location="본문 2문단",
        source_publisher="가나다경제",
        document_date="2026-05-01",
        news_claim_kind="사실 보도",
        news_source_category="경제지",
    )
    assert _is_news_fragment(fragment)
    prompt = _render_fragments([fragment])

    assert news_metadata(fragment) in prompt
    assert f"{PROMPT_FRAGMENT_LOCATION_LABEL}: 본문 2문단" in prompt
    # evidence_text는 JSON 문자열로 그대로 인용된다 (기존 규칙 유지).
    assert json.dumps(fragment.text, ensure_ascii=False) in prompt


# ══════════════════════════════════════════════════════════
# (d) 기호 배정은 결정적이다
# ══════════════════════════════════════════════════════════


def test_같은_입력이면_기호_배정이_항상_같다():
    def _build():
        return [
            _fragment(fragment_id="1", document_title="사업보고서 (2025.12)"),
            _fragment(
                fragment_id="2",
                formal_source_kind="official_web_page",
                kind="official_web_page",
                document_title="",
                location="회사소개 > 개요",
            ),
            _fragment(fragment_id="3", document_title="사업보고서 (2025.12)"),
        ]

    prompt_1 = _render_fragments(_build())
    prompt_2 = _render_fragments(_build())
    assert prompt_1 == prompt_2


# ══════════════════════════════════════════════════════════
# (e) 합성 200조각 블록에서 12% 이상 줄어든다
# ══════════════════════════════════════════════════════════

_DART_KINDS = (
    "dart_business_report",
    "dart_audit_report",
    "dart_consolidated_audit_report",
    "dart_semiannual_report",
    "dart_quarterly_report",
)
_DART_DOCS = ("사업보고서 (2025.12)", "사업보고서 (2024.12)")
_WEB_DOCS = ("회사 소개 페이지", "제품 안내 페이지", "채용 안내 페이지", "IR 자료실")


def _synthetic_corpus(n: int = 200) -> list[CollectedFragment]:
    """SM 실측(DART typed 조각이 압도적 다수, 문서 수는 적고 조각 수는 많음)을
    본떠 만든 합성 조각 목록 — 실제 원문은 없으므로 문장은 합성이다."""
    fragments: list[CollectedFragment] = []
    n_dart = int(n * 0.7)
    n_web = int(n * 0.2)
    n_news = n - n_dart - n_web
    offset = 10_000
    for i in range(n_dart):
        kind = _DART_KINDS[i % len(_DART_KINDS)]
        doc = _DART_DOCS[i % len(_DART_DOCS)]
        start = offset + i * 600
        fragments.append(
            _fragment(
                fragment_id=str(len(fragments) + 1),
                kind=kind,
                formal_source_kind=kind,
                document_title=doc,
                location=f"{start}-{start + 480}",
                text=f"사업 개요 문단 {i}: 회사는 매출과 영업이익이 늘었다고 밝혔다.",
            )
        )
    for i in range(n_web):
        doc = _WEB_DOCS[i % len(_WEB_DOCS)]
        fragments.append(
            _fragment(
                fragment_id=str(len(fragments) + 1),
                kind="official_web_page",
                formal_source_kind="official_web_page",
                document_title=doc,
                location=f"회사소개 > 개요 {i % 5}",
                text=f"홈페이지 소개 문단 {i}: 회사의 비전과 제품 라인업을 설명한다.",
            )
        )
    for i in range(n_news):
        fragments.append(
            _fragment(
                fragment_id=str(len(fragments) + 1),
                kind="news",
                formal_source_kind="news",
                document_title=f"뉴스 기사 제목 {i}",
                location=f"본문 {i % 3}문단",
                text=f"보도 문단 {i}: 회사가 신제품을 출시했다고 보도했다.",
            )
        )
    return fragments


def _old_render_fragments(
    fragments,
    *,
    show_supported_claim_slots: bool = False,
    omit_numeric_location: bool = False,
) -> str:
    """2026-09-10 변경 «이전» 렌더 — 절감률의 기준선이다 (옛 형식을 그대로 재구성).

    프로덕션 코드를 복사한 게 아니라, 이 파일이 바꾸기 전 `_render_fragments`가
    실제로 냈던 문자열 모양을 그대로 다시 적은 것이다(회귀 기준선 고정용).

    ``omit_numeric_location``: 기본 False(순수 옛 형식, 위치는 전부 보임).
    True면 ④(숫자 원문위치 제거)만 적용한 «④만 켠 상태」의 기대값을 만든다
    — ②③은 여전히 옛 형식(종류 전체 문자열 인라인)이다.
    """
    lines = [PROMPT_FRAGMENTS_HEAD]
    for fragment in fragments:
        label = fragment.formal_source_kind or fragment.kind or "자료"
        if _is_news_fragment(fragment):
            label += " · 메타데이터 " + news_metadata(fragment)
        if fragment.document_title:
            label = f"{label}·{fragment.document_title}"
        show_location = fragment.location and (
            not omit_numeric_location
            or not _is_numeric_offset_location(fragment.location)
        )
        if show_location:
            label = f"{label} · {PROMPT_FRAGMENT_LOCATION_LABEL}: {fragment.location}"
        if show_supported_claim_slots:
            supported = ", ".join(fragment.supported_claim_slots) or "없음"
            label = f"{label} · 지원 주장슬롯: {supported}"
        evidence_text = (
            json.dumps(fragment.text, ensure_ascii=False)
            if _is_news_fragment(fragment)
            else fragment.text
        )
        lines.append(f"[조각 {fragment.fragment_id}] ({label}) {evidence_text}\n")
    return "".join(lines)


def test_합성_200조각에서_옛_렌더_대비_12퍼센트_이상_줄어든다():
    fragments = _synthetic_corpus(200)
    old_prompt = _old_render_fragments(fragments)
    new_prompt = _render_fragments(fragments)

    # 조각 원문·번호는 한 글자도 안 바뀐다 — 줄어드는 건 라벨뿐이어야 한다.
    for fragment in fragments:
        assert fragment.text in new_prompt
        assert f"[조각 {fragment.fragment_id}]" in new_prompt

    reduction = 1 - (len(new_prompt) / len(old_prompt))
    assert reduction >= 0.12, f"절감률 {reduction:.4%}이 12% 미만이다"


# ══════════════════════════════════════════════════════════
# (f) 문서 목록은 공유 앞부분(cache_prefix_chars) 안에 들어간다
# ══════════════════════════════════════════════════════════


def test_문서목록이_공유_앞부분_안에_들어간다():
    fragments = [
        _fragment(fragment_id="1", document_title="사업보고서 (2025.12)"),
        _fragment(
            fragment_id="2",
            formal_source_kind="dart_semiannual_report",
            kind="dart_semiannual_report",
            document_title="반기보고서 (2025.06)",
        ),
    ]
    prompt = build_section_prompt(
        "가나다전자(주)",
        "identity",
        fragments,
        None,
        (),
        shared_evidence_prefix=True,
    )

    shared_prefix = prompt[: prompt.cache_prefix_chars]
    assert DOCUMENT_LIST_HEAD in shared_prefix
    assert "A: 사업보고서 (2025.12)" in shared_prefix
    assert "B: 반기보고서 (2025.06)" in shared_prefix


# ══════════════════════════════════════════════════════════
# (g)-(i) 세 다이어트를 독립적으로 끌 수 있다 (2026-09-10 팀장 지시)
# ══════════════════════════════════════════════════════════


def test_기본값은_셋_다_켜져_있다():
    assert EVIDENCE_LABEL_OMIT_NUMERIC_LOCATION is True
    assert EVIDENCE_LABEL_SHORT_KIND is True
    assert EVIDENCE_LABEL_DOCUMENT_HEADER is True


def test_넷을_끄면_숫자_원문위치가_다시_보인다(monkeypatch):
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_OMIT_NUMERIC_LOCATION", False)

    fragment = _fragment(location="18656-19172")
    prompt = _render_fragments([fragment])

    assert f"{PROMPT_FRAGMENT_LOCATION_LABEL}: 18656-19172" in prompt


def test_둘을_끄면_문서목록에_원래_종류_문자열이_찍힌다(monkeypatch):
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_SHORT_KIND", False)

    fragment = _fragment(document_title="", location="")
    prompt = _render_fragments([fragment])

    assert "A: dart_business_report" in prompt
    assert "A: 공시" not in prompt


def test_셋을_끄면_문서목록_없이_조각_줄에_종류_문서명이_인라인으로_반복된다(monkeypatch):
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_DOCUMENT_HEADER", False)

    fragments = [
        _fragment(fragment_id="1", document_title="사업보고서 (2025.12)"),
        _fragment(fragment_id="2", document_title="사업보고서 (2025.12)"),
    ]
    prompt = _render_fragments(fragments)

    assert DOCUMENT_LIST_HEAD not in prompt
    # 3을 끈 대가 - 문서명이 두 조각 줄 모두에 반복된다.
    assert prompt.count("사업보고서 (2025.12)") == 2
    fragment_lines = [line for line in prompt.splitlines() if line.startswith("[조각 ")]
    assert len(fragment_lines) == 2
    for line in fragment_lines:
        assert "(문서 " not in line
        assert "공시·사업보고서 (2025.12)" in line


def test_넷만_켠_상태는_옛_형식에서_원문위치만_빠진_모양과_문자그대로_같다(monkeypatch):
    """팀장이 지정한 핵심 사례 - 2,3을 끄고 4만 켠 렌더가, 옛 형식에서
    숫자 원문위치만 빠진 모양과 문자 그대로 같아야 한다."""
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_SHORT_KIND", False)
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_DOCUMENT_HEADER", False)
    # EVIDENCE_LABEL_OMIT_NUMERIC_LOCATION은 기본값 True 그대로 둔다 - 이게 4만 켠 상태다.

    fragments = [
        _fragment(
            fragment_id="1",
            document_title="사업보고서 (2025.12)",
            location="18656-19172",  # 숫자 오프셋 - 4가 뺀다
        ),
        _fragment(
            fragment_id="2",
            kind="official_web_page",
            formal_source_kind="official_web_page",
            document_title="",
            location="회사소개 > 개요",  # 글자 위치 - 4가 있어도 남는다
            supported_claim_slots=("identity:legal_entity",),
        ),
    ]

    actual = _render_fragments(fragments, show_supported_claim_slots=True)
    expected = _old_render_fragments(
        fragments, show_supported_claim_slots=True, omit_numeric_location=True
    )
    assert actual == expected
    # 2,3을 끈 티가 실제로 난다는 것도 같이 확인 - 원래 종류·문서명이 인라인으로 보인다.
    assert "dart_business_report·사업보고서 (2025.12)" in actual


def test_셋_다_끄면_2026_09_10_이전과_바이트가_완전히_같다(monkeypatch):
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_OMIT_NUMERIC_LOCATION", False)
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_SHORT_KIND", False)
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_DOCUMENT_HEADER", False)

    fragments = _synthetic_corpus(50)
    actual = _render_fragments(fragments)
    expected = _old_render_fragments(fragments, omit_numeric_location=False)
    assert actual == expected


# ══════════════════════════════════════════════════════════
# (j)-(k) flat 렌더 경로와 packet 렌더 경로가 같은 raw_kind로 수렴한다
# (2026-09-10, corpus-measure 지적에 대한 코드 레벨 확인 - 팀장 지시)
# ══════════════════════════════════════════════════════════


def _official_evidence_with_one_dart_document(
    company_id: str, receipt_number: str, text: str
) -> tuple[OfficialEvidenceCollectionResult, str]:
    """OfficialEvidenceCollectionResult는 아홉 장 후보를 «모두» 요구한다
    (``runtime_port.py``의 생성자 검증) — 8개는 채우기용 공식 웹페이지
    후보, 실제로 조사할 대상은 첫 장의 DART 사업보고서 조각 하나뿐이다.
    ``test_full_evidence_end_to_end.py``의 ``_official_evidence`` fixture와
    같은 패턴이다(간소화 버전).
    """
    attestation_source_id, attestation_evidence = dart_profile_attestation_material(
        profile={
            "status": "000",
            "corp_code": company_id,
            "corp_name": "가나다전자",
            "hm_url": "https://official.example/",
        },
        corp_code=company_id,
        company_name="가나다전자",
    )
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document_id = f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt_number}"
    target_section_id = REQUIRED_EVIDENCE_SECTION_IDS[0]

    candidates: list[ChapterEvidenceCandidates] = []
    for index, section_id in enumerate(REQUIRED_EVIDENCE_SECTION_IDS):
        slots = collector_slots_for(section_id)
        if section_id == target_section_id:
            source_kind = SOURCE_KIND_DART_BUSINESS_REPORT
            section_document_id = document_id
            canonical_url = (
                f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_number}"
            )
            publisher = "금융감독원"
            section_text = text
            attestation_id, attestation_body = "", ""
        else:
            source_kind = SOURCE_KIND_OFFICIAL_WEB_PAGE
            section_document_id = f"official-filler-{index}"
            canonical_url = f"https://official.example/evidence/{index}"
            publisher = "가나다전자"
            section_text = f"가나다전자 공식 자료 채우기 문단 {index}."
            attestation_id, attestation_body = attestation_source_id, attestation_evidence
        section_text_sha256 = (
            text_sha256 if section_id == target_section_id else hashlib.sha256(
                section_text.encode("utf-8")
            ).hexdigest()
        )
        document = CollectedEvidenceDocument(
            company_id=company_id,
            document_id=section_document_id,
            canonical_url=canonical_url,
            source_tier=SourceTier.TIER_1_OFFICIAL,
            source_kind=source_kind,
            publisher=publisher,
            title="사업보고서 (2025.12)"
            if section_id == target_section_id
            else f"공식 자료 {index}",
            published_on="2026-03-30",
            collected_at="2026-03-30",
            content_sha256=hashlib.sha256(
                f"문서 전체 {index}: {section_text}".encode("utf-8")
            ).hexdigest(),
            exact_evidence_hashes=(section_text_sha256,),
            identity_binding="dart_profile_homepage",
            usable_ranges=(DocumentTextRange(0, len(section_text)),),
            collector_version="test-collector-v1",
            parser_version="test-parser-v1",
            requirement=SourceRequirement.REQUIRED,
            domain_attestation_source_id=attestation_id,
            domain_attestation_evidence=attestation_body,
        )
        fragment_evidence = EvidenceFragment(
            company_id=company_id,
            fragment_id=f"official-fragment-{index}",
            document_id=section_document_id,
            location=f"section:{section_id}",
            text_sha256=section_text_sha256,
            text=section_text,
            section_id=section_id,
            slot_id=slots[0],
            covered_slot_ids=slots,
            score_millis=1000,
            reason_codes=("exact_fixture_match",),
        )
        candidates.append(
            ChapterEvidenceCandidates(
                company_id=company_id,
                section_id=section_id,
                documents=(document,),
                fragments=(fragment_evidence,),
                attempts=(),
                candidate_readiness=EvidenceReadiness.READY,
                reason_codes=(),
                estimated_tokens=max(1, len(section_text) // 4),
                max_chars=10_000,
                max_estimated_tokens=10_000,
            )
        )
    return (
        OfficialEvidenceCollectionResult(company_id=company_id, candidates=tuple(candidates)),
        document_id,
    )


def test_typed_공식문서는_flat과_packet_변환이_같은_raw_kind로_수렴한다():
    """official_evidence_transport_adapter가 만드는 typed raw dict가
    flat 변환(fragments_from_raw)과 packet 변환
    (evidence_transport._collected_fragment_from_raw) 양쪽에서 같은
    raw_kind로 수렴하는지, 진짜 프로덕션 변환 함수로 확인한다. 실제
    네트워크·DART 호출 0건 - 두 변환 함수만 직접 부른다.
    """
    company_id = "00199999"
    text = "회사는 사업보고서에서 반도체 검사 장비 사업을 밝혔다."
    receipt_number = "20260330000009"
    official_evidence, document_id = _official_evidence_with_one_dart_document(
        company_id, receipt_number, text
    )
    frags, added = merge_official_evidence_fragments({}, official_evidence)
    assert added == 9  # 아홉 장 후보 전부가 typed 조각으로 들어간다

    # -- flat 변환: fragments_from_raw (port.py, composer 입력) --
    flat_fragments = fragments_from_raw(frags)
    assert len(flat_fragments) == 9
    flat_fragment = next(f for f in flat_fragments if f.document_title == "사업보고서 (2025.12)")
    # flat 변환은 이 필드를 아예 읽지 않는다 - 항상 빈 채로 남는다(코드로 확인).
    assert flat_fragment.formal_source_kind == ""
    assert flat_fragment.kind == SOURCE_KIND_DART_BUSINESS_REPORT

    # -- packet 변환: evidence_transport._collected_fragment_from_raw --
    # DART 종류 조각의 공개 번호를 골라낸다 (나머지 8개는 채우기용 홈페이지).
    target_number = next(
        number
        for number, raw in frags.items()
        if raw.get("종류") == SOURCE_KIND_DART_BUSINESS_REPORT
    )
    packet_fragment, section_ids = evidence_transport._collected_fragment_from_raw(  # noqa: SLF001
        target_number,
        frags[target_number],
        corp_id=company_id,
        filing_meta=filing_meta_from_raw(
            {
                "report_nm": "사업보고서 (2025.12)",
                "rcept_no": receipt_number,
                "rcept_dt": "20260330",
            }
        ),
        seen_origin_ids=set(),
    )
    assert section_ids
    assert packet_fragment.formal_source_kind == SOURCE_KIND_DART_BUSINESS_REPORT
    assert packet_fragment.document_title == "사업보고서 (2025.12)"

    # -- 두 변환의 raw_kind(= formal_source_kind or kind)가 같다 --
    flat_raw_kind = flat_fragment.formal_source_kind or flat_fragment.kind or "자료"
    packet_raw_kind = packet_fragment.formal_source_kind or packet_fragment.kind or "자료"
    assert flat_raw_kind == packet_raw_kind == SOURCE_KIND_DART_BUSINESS_REPORT

    # -- 렌더 결과도 두 경로 모두 같은 표시명으로 수렴한다 (기호는 다른 8개
    #    채우기 문서가 섞여 있어 A로 고정되지 않을 수 있으므로 내용만 본다) --
    flat_prompt = _render_fragments(flat_fragments)
    packet_prompt = _render_fragments((packet_fragment,))
    assert re.search(r"[A-Z]+: 사업보고서 \(2025\.12\) · 공시", flat_prompt)
    assert re.search(r"[A-Z]+: 사업보고서 \(2025\.12\) · 공시", packet_prompt)


def test_legacy_한글종류_raw는_flat과_packet_모두에서_한글_그대로_라벨이_된다():
    """v1 raw 조각(종류 = 한글 문자열, 예: 사업내용)은
    _TYPED_DART_SECTION_FRAGMENT_KIND가 실제로 만드는 모양이다
    (real._typed_dart_legacy_fragments - corpus-measure가 지적한 바로 그
    producer). typed 필수 메타데이터가 없어 두 변환 모두 이 조각을 legacy로
    취급하고, formal_source_kind는 항상 비며 kind가 그대로 라벨이 된다는
    것을 진짜 변환 함수로 확인한다.
    """
    raw = {
        1: {
            "종류": "사업내용",
            "원문": "가나다전자는 반도체 검사 장비를 만드는 법인이다.",
        }
    }

    flat_fragments = fragments_from_raw(raw)
    assert flat_fragments[0].formal_source_kind == ""
    assert flat_fragments[0].kind == "사업내용"

    packet_fragment, section_ids = evidence_transport._collected_fragment_from_raw(  # noqa: SLF001
        1,
        raw[1],
        corp_id="00199999",
        filing_meta=filing_meta_from_raw(
            {
                "report_nm": "사업보고서 (2025.12)",
                "rcept_no": "20260315000123",
                "rcept_dt": "20260315",
            }
        ),
        seen_origin_ids=set(),
    )
    assert section_ids  # 사업내용은 legacy 종류표에 등록돼 최소 한 장에 배정된다
    assert packet_fragment.formal_source_kind == ""
    assert packet_fragment.kind == "사업내용"

    flat_prompt = _render_fragments(flat_fragments)
    packet_prompt = _render_fragments((packet_fragment,))
    # 표시명 표에 없는 한글 종류는 두 경로 모두 원문 그대로 문서 목록에 남는다
    # - SOURCE_KIND_DISPLAY_NAMES는 source_kind 문서-종류만 다루고, 이
    # 한글 라벨은 장 슬롯 주제라 압축 대상이 아니다(설계상 의도).
    assert "A: 사업내용" in flat_prompt
    assert "A: 사업내용" in packet_prompt


# ══════════════════════════════════════════════════════════
# (l) (문서 X) 흉내는 검출·제거된다 (2026-09-10, 독립 검토 P2)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "text",
    [
        "회사는 매출이 늘었다(문서 A).",
        "회사는 매출이 늘었다(문서 AA).",
        "회사는 매출이 늘었다 (문서 B).",
    ],
)
def test_문서기호_흉내는_인용_흉내로_검출된다(text):
    assert contains_inline_citation_marker(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "(주)가나다전자는 성장했다.",
        "실적은 (단위: 원) 표와 같다.",
        "배당정책은 (안정적) 유지된다.",
        "부문은 (사업부문 A) 소속이다.",  # "문서"가 아닌 다른 한글 + 대문자
        "종속회사(가나다상사)는 별도다.",
        "회사는 2025년에 성장했다.",
    ],
)
def test_일반_괄호_문구는_인용_흉내로_오탐하지_않는다(text):
    assert contains_inline_citation_marker(text) is False


def test_문서기호_흉내는_산문에서_제거된다():
    cleaned = _strip_inline_citation_markers("회사는 매출이 늘었다(문서 A).")
    assert "(문서" not in cleaned
    assert "매출이 늘었다" in cleaned


def test_일반_괄호_문구는_제거되지_않는다():
    text = "(주)가나다전자는 매출이 늘었다."
    assert _strip_inline_citation_markers(text) == text


def test_숫자_인용_흉내_검출은_그대로_동작한다():
    # 기존 숫자 규칙이 문서 기호 규칙을 추가하며 깨지지 않았는지 회귀 확인.
    assert contains_inline_citation_marker("회사는 매출이 늘었다[조각 1].")
    assert contains_inline_citation_marker("회사는 매출이 늘었다(1).")
    assert contains_inline_citation_marker("회사는 매출이 늘었다(12, 3).")
    assert not contains_inline_citation_marker("회사는 2026년에 창립했다.")


# ══════════════════════════════════════════════════════════
# (m) (문서 X) 안내가 공유 앞부분에 한 번 들어간다 (2026-09-10, 독립 검토 P2)
# ══════════════════════════════════════════════════════════


def test_문서기호_안내가_문서목록_뒤에_한_번_들어간다():
    fragments = [
        _fragment(fragment_id="1", document_title="사업보고서 (2025.12)"),
        _fragment(fragment_id="2", document_title="사업보고서 (2025.12)"),
    ]
    prompt = _render_fragments(fragments)

    assert prompt.count(DOCUMENT_LIST_GUIDE) == 1
    assert prompt.index(DOCUMENT_LIST_HEAD) < prompt.index(DOCUMENT_LIST_GUIDE)
    assert prompt.index(DOCUMENT_LIST_GUIDE) < prompt.index("[조각 1]")


def test_문서목록이_꺼지면_안내도_같이_빠진다(monkeypatch):
    monkeypatch.setattr(composer_logic, "EVIDENCE_LABEL_DOCUMENT_HEADER", False)

    fragments = [_fragment(fragment_id="1", document_title="사업보고서 (2025.12)")]
    prompt = _render_fragments(fragments)

    assert DOCUMENT_LIST_GUIDE not in prompt


def test_문서기호_안내는_공유_앞부분_안에_들어간다():
    fragments = [_fragment(fragment_id="1", document_title="사업보고서 (2025.12)")]
    prompt = build_section_prompt(
        "가나다전자(주)", "identity", fragments, None, (), shared_evidence_prefix=True
    )

    assert DOCUMENT_LIST_GUIDE in prompt[: prompt.cache_prefix_chars]


# ══════════════════════════════════════════════════════════
# (n) 문서 목록에서 표시가 같아지면 원래 종류로 구분한다
# (2026-09-10, 독립 검토 P2)
# ══════════════════════════════════════════════════════════


def test_표시가_같아지는_두_문서는_원래_종류로_구분된다():
    fragments = [
        _fragment(
            fragment_id="1",
            kind="dart_business_report",
            formal_source_kind="dart_business_report",
            document_title="사업보고서 (2025.12)",
        ),
        _fragment(
            fragment_id="2",
            kind="dart_audit_report",
            formal_source_kind="dart_audit_report",
            document_title="사업보고서 (2025.12)",
        ),
    ]
    prompt = _render_fragments(fragments)

    assert "A: 사업보고서 (2025.12) · 공시(dart_business_report)" in prompt
    assert "B: 사업보고서 (2025.12) · 공시(dart_audit_report)" in prompt


def test_겹치지_않는_문서는_원래_종류가_안_붙는다():
    fragments = [
        _fragment(fragment_id="1", document_title="사업보고서 (2025.12)"),
        _fragment(
            fragment_id="2",
            kind="official_web_page",
            formal_source_kind="official_web_page",
            document_title="",
        ),
    ]
    prompt = _render_fragments(fragments)

    assert "A: 사업보고서 (2025.12) · 공시" in prompt
    assert "A: 사업보고서 (2025.12) · 공시(dart_business_report)" not in prompt
    assert "(official_web_page)" not in prompt


def test_빈_문서명끼리_겹쳐도_원래_종류로_구분된다():
    # 독립 검토가 지적한 사례 - 문서명이 없는 조각은 표시명 하나만 남아
    # 구분이 완전히 사라질 수 있다.
    fragments = [
        _fragment(
            fragment_id="1",
            kind="dart_business_report",
            formal_source_kind="dart_business_report",
            document_title="",
        ),
        _fragment(
            fragment_id="2",
            kind="dart_semiannual_report",
            formal_source_kind="dart_semiannual_report",
            document_title="",
        ),
    ]
    prompt = _render_fragments(fragments)

    assert "A: 공시(dart_business_report)" in prompt
    assert "B: 공시(dart_semiannual_report)" in prompt


def test_구분_붙여도_기호_배정은_그대로_결정적이다():
    def _build():
        return [
            _fragment(
                fragment_id="1",
                kind="dart_business_report",
                formal_source_kind="dart_business_report",
                document_title="사업보고서 (2025.12)",
            ),
            _fragment(
                fragment_id="2",
                kind="dart_audit_report",
                formal_source_kind="dart_audit_report",
                document_title="사업보고서 (2025.12)",
            ),
        ]

    assert _render_fragments(_build()) == _render_fragments(_build())
