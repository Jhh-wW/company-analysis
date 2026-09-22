# -*- coding: utf-8 -*-
"""운영 기본 표기(merged)에서 FULL 사전 봉인과 렌더 결과가 같은 글자인지 잰다.

★ 왜 이 시험이 있나 (2026-09-22 실측)
  인용 번호를 «보일지» 정하는 규칙이 렌더(`render.py`)와 사전 봉인
  (`public_manifest.py`) 두 곳에 따로 적혀 있었다. 해석 문장 번호를 보이도록
  바꾼 쪽이 렌더뿐이어서, 운영 기본값인 `CITATION_STYLE_MERGED`로 FULL
  보고서를 만들면 봉인 대조가 어긋나 `PublicManifestError`로 생성이 통째로
  죽었다. 그 예외를 잡는 곳은 운영 코드에 없다.

  기존 봉인 시험이 이 죽음을 못 잡은 이유는 전부 `CITATION_STYLE_INLINE`을
  골라서다(`test_render_style_normalizer.py`). inline은 모든 번호를 보이므로
  두 규칙의 «차이»가 나타나는 merged 경로를 한 번도 밟지 않았다.

★ 여기서 지키는 것
  ⓐ 확인 문장과 해석 문장이 «같은 조각»을 인용하는 장에서 봉인 == 렌더.
  ⓑ 요약 두 항목이 «같은 출처»를 인용할 때 봉인 == 렌더.
  ⓒ 규칙 «값» 자체 — 해석 문장도 번호를 보이고, 요약은 전부 번호를 보인다.
     ★ ⓐⓑ만 있으면 두 쪽이 «똑같이 틀려도» 초록이 된다. 규칙을 한 함수로
       합친 뒤에는 이 값 단정이 유일한 방어다.
  ⓓ 봉인이 렌더의 규칙 함수를 «실제로» 부른다 — 규칙을 다시 두 벌로 적으면
     ⓒ가 통과하더라도 이 배선 단정이 먼저 깨진다.

★ 픽스처는 실제 산출 PDF(주식회사 뤼튼테크놀로지스 2026-09-22)에 인쇄된
  문장을 글자 그대로 쓴다. 재구성본이 아니다.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.composer import public_manifest as public_manifest_module
from src.features.composer import render as render_module
from src.features.composer.constants import (
    CITATION_STYLE_INLINE,
    CITATION_STYLE_MERGED,
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    SECTION_IDS,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.public_manifest import build_public_structure_seal
from src.features.composer.render import render_report
from src.features.pipeline.port import Grade
from src.shared.report_generation.canonical import public_content_digests
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.models import PublicationPolicy, ReleaseDecision
from src.shared.report_quality.source_identity import document_identity_from_parts

#: 뤼튼 보고서 2쪽 3.1 — «확인» 등급으로 인쇄된 문장.
CONFIRMED_TEXT = (
    "회사의 주된 영업수익은 인공지능 소프트웨어 개발과 관련된 용역 매출과 "
    "인공지능 콘텐츠 매출로 구성된다."
)
#: 뤼튼 보고서 2쪽 3.2 — «— 해석» 표지를 달고 인쇄됐는데 번호가 없던 그 문장.
INTERPRETED_TEXT = (
    "지급수수료와 광고선전비의 급증은 콘텐츠 플랫폼 확대와 사용자 확보에 "
    "집중하는 사업 전략을 시사한다."
)
#: 뤼튼 보고서 2쪽 2.x — 요약 두 항목이 같은 출처를 인용하는 경우를 만들 짝.
SECOND_CONFIRMED_TEXT = (
    "회사는 일본 법인인 Wrtn Technologies Japan을 종속기업으로 두고 있으며, "
    "당기 중 해당 종속기업에 대여금 형태로 자금을 지원했다."
)

_URL = "https://company.example/report"
#: 3장(수익 모델) — 위 세 문장이 실린 장.
_SECTION_ID = "business_model"
_COMPANY_ID = "00123456"


def _fragment(text: str) -> CollectedFragment:
    return CollectedFragment(
        "1",
        "사업내용",
        text,
        source_url=_URL,
        document_identity=document_identity_from_parts(url=_URL),
    )


def _composed(
    sentences: tuple[ComposedSentence, ...],
    summary: tuple[ComposedSentence, ...] = (),
) -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(key, sentences if key == _SECTION_ID else ())
            for key in SECTION_IDS
        ),
        summary=summary,
    )


def _seal(composed: ComposedReport, fragment: CollectedFragment, style: str):
    return build_public_structure_seal(
        composed,
        (fragment,),
        None,
        filing_meta=None,
        composition_tables=(),
        table_presentation="table",
        company_id=_COMPANY_ID,
        evidence_generation_sha256="a" * 64,
        evidence_packet_sha256s=tuple(
            (key, str(index) * 64) for index, key in enumerate(SECTION_IDS, 1)
        ),
        company_name="시험회사",
        corp_type="",
        generated_at="",
        as_of_date="2026-09-22",
        analysis_period="",
        latest_performance_period="",
        citation_style=style,
    )


def _render_full(composed: ComposedReport, fragment: CollectedFragment, style: str, seal):
    """FULL 출고 상태까지 맞춘 Report — 공개 지문 계산이 이 값들을 읽는다."""
    rendered = render_report(
        "시험회사",
        composed,
        (fragment,),
        None,
        citation_style=style,
        as_of_date="2026-09-22",
        company_id=_COMPANY_ID,
        public_structure_seal=seal,
    )
    return replace(
        rendered,
        grade=Grade.COMPLETE,
        quality_contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        safety_decision=ReleaseDecision.RELEASE_ALLOWED.value,
        publication_policy=PublicationPolicy.STRUCTURED_SAFETY.value,
    )


def _section_lines(rendered) -> list[str]:
    section = next(item for item in rendered.sections if item.cell == _SECTION_ID)
    return [text for text, _hint in section.prose_lines]


# ══════════════════════════════════════════════════════════
# ⓐ 확인 + 해석이 같은 조각을 인용하는 장
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("style", (CITATION_STYLE_MERGED, CITATION_STYLE_INLINE))
def test_해석_문장이_있는_장의_봉인과_렌더가_같은_글자다(style):
    fragment = _fragment(f"{CONFIRMED_TEXT} {INTERPRETED_TEXT}")
    composed = _composed((
        ComposedSentence(CONFIRMED_TEXT, ("1",), GRADE_CONFIRMED),
        ComposedSentence(INTERPRETED_TEXT, ("1",), GRADE_INTERPRETED),
    ))

    seal = _seal(composed, fragment, style)
    rendered = _render_full(composed, fragment, style, seal)

    assert public_content_digests(rendered)[0] == seal.public_content_sha256


def test_해석_문장도_자기_인용_번호를_보인다():
    """★ ⓒ — 값 단정이 없으면 두 쪽이 같이 틀려도 위 지문 비교는 초록이다."""
    fragment = _fragment(f"{CONFIRMED_TEXT} {INTERPRETED_TEXT}")
    composed = _composed((
        ComposedSentence(CONFIRMED_TEXT, ("1",), GRADE_CONFIRMED),
        ComposedSentence(INTERPRETED_TEXT, ("1",), GRADE_INTERPRETED),
    ))

    seal = _seal(composed, fragment, CITATION_STYLE_MERGED)
    rendered = _render_full(composed, fragment, CITATION_STYLE_MERGED, seal)

    assert _section_lines(rendered) == [
        f"{CONFIRMED_TEXT} [1]",
        f"{INTERPRETED_TEXT} [1] — 해석",
    ]


# ══════════════════════════════════════════════════════════
# ⓑ 요약 두 항목이 같은 출처를 인용
# ══════════════════════════════════════════════════════════


def test_요약_두_항목이_같은_출처여도_봉인과_렌더가_같고_각자_번호를_보인다():
    fragment = _fragment(f"{CONFIRMED_TEXT} {SECOND_CONFIRMED_TEXT}")
    first = ComposedSentence(CONFIRMED_TEXT, ("1",), GRADE_CONFIRMED)
    second = ComposedSentence(SECOND_CONFIRMED_TEXT, ("1",), GRADE_CONFIRMED)
    composed = _composed((first, second), summary=(first, second))

    seal = _seal(composed, fragment, CITATION_STYLE_MERGED)
    rendered = _render_full(composed, fragment, CITATION_STYLE_MERGED, seal)

    assert public_content_digests(rendered)[0] == seal.public_content_sha256
    assert [item.text for item in rendered.summary_items] == [
        f"{CONFIRMED_TEXT} [1]",
        f"{SECOND_CONFIRMED_TEXT} [1]",
    ]


def test_본문은_같은_출처_묶음의_마지막_문장에만_번호를_단다():
    """★ merged 절충안이 살아 있는지 — 요약 규칙과 «다르다»는 것이 요점이다.

    이 대비가 없으면 「요약도 본문 규칙을 쓰면 되지 않나」로 되돌아간다.
    """
    fragment = _fragment(f"{CONFIRMED_TEXT} {SECOND_CONFIRMED_TEXT}")
    composed = _composed((
        ComposedSentence(CONFIRMED_TEXT, ("1",), GRADE_CONFIRMED),
        ComposedSentence(SECOND_CONFIRMED_TEXT, ("1",), GRADE_CONFIRMED),
    ))

    seal = _seal(composed, fragment, CITATION_STYLE_MERGED)
    rendered = _render_full(composed, fragment, CITATION_STYLE_MERGED, seal)

    assert public_content_digests(rendered)[0] == seal.public_content_sha256
    assert _section_lines(rendered) == [
        CONFIRMED_TEXT,
        f"{SECOND_CONFIRMED_TEXT} [1]",
    ]


# ══════════════════════════════════════════════════════════
# ⓓ 배선 — 봉인이 렌더의 규칙 함수를 실제로 부른다
# ══════════════════════════════════════════════════════════


def test_봉인이_렌더의_표기_규칙_함수를_그대로_부른다(monkeypatch):
    """★ 규칙을 다시 두 벌로 적으면 값이 같더라도 여기서 먼저 깨진다."""
    부른_규칙: list[str] = []
    원래_본문 = render_module.marker_visibility
    원래_요약 = render_module.summary_marker_visibility

    def 본문_spy(*args, **kwargs):
        부른_규칙.append("본문")
        return 원래_본문(*args, **kwargs)

    def 요약_spy(*args, **kwargs):
        부른_규칙.append("요약")
        return 원래_요약(*args, **kwargs)

    monkeypatch.setattr(render_module, "marker_visibility", 본문_spy)
    monkeypatch.setattr(render_module, "summary_marker_visibility", 요약_spy)

    fragment = _fragment(f"{CONFIRMED_TEXT} {INTERPRETED_TEXT}")
    sentence = ComposedSentence(CONFIRMED_TEXT, ("1",), GRADE_CONFIRMED)
    composed = _composed(
        (sentence, ComposedSentence(INTERPRETED_TEXT, ("1",), GRADE_INTERPRETED)),
        summary=(sentence,),
    )

    # 렌더는 부르지 않는다 — 봉인 «혼자» 규칙 함수를 쓰는지가 요점이다.
    _seal(composed, fragment, CITATION_STYLE_MERGED)

    assert "본문" in 부른_규칙, (
        "사전 봉인이 렌더의 본문 표기 규칙을 부르지 않습니다 — 규칙이 다시 "
        "두 벌이 됐을 가능성이 큽니다"
    )
    assert "요약" in 부른_규칙, (
        "사전 봉인이 렌더의 요약 표기 규칙을 부르지 않습니다"
    )


def test_봉인_모듈에_표기_규칙_사본이_남아_있지_않다():
    """★ 위 spy를 우회하는 «조용한 사본»을 이름으로도 한 번 막는다."""
    assert not hasattr(public_manifest_module, "_marker_visibility_expected"), (
        "표기 규칙 사본이 되살아났습니다 — 렌더와 갈라지면 FULL 생성이 죽습니다"
    )
