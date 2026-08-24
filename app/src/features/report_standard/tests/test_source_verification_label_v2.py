"""엔진 v2(사실 카드 없음) 부록 「사실 검증」 표시를 검증한다.

★ 왜 이 파일을 새로 두나 — v1 경로는 이미 test_section_content_contract.py가
  ``build_demo_report()``(fact_records가 있는 v1 보고서)로 덮고 있다. v2는
  fact_records를 아예 안 만들어 그 픽스처를 못 쓴다. v2 전용 최소 Report를
  직접 조립해 문장 뒤 «확인/해석» 등급 → 부록 라벨 변환만 따로 검증한다.
"""

from __future__ import annotations

from dataclasses import replace

from src.features.pipeline.port import FactRecord, Grade, Report, ReportSection
from src.features.provenance.sources import Source, SourceKind
from src.features.report_standard.section_content import (
    _V2_INTERPRETATION_MARKER,
    source_verification_label,
)

#: 엔진 v2 render.py가 실제로 Report에 박아 넣는 schema_version.
#: composer.render.ENGINE_V2_SCHEMA_VERSION과 같은 값 — render.py를
#: import하지 않기 위해 값만 옮겨 적었다(이 파일이 검증하는 함수 자체가
#: fact_records 유무로 분기하므로, 이 값이 정확히 일치할 필요는 없지만
#: 실제 v2 보고서 모양과 맞춰 둔다).
_ENGINE_V2_SCHEMA_VERSION = "company-report-v2-composer"


def _v2_report(
    *,
    prose_lines_by_section: dict[str, list[tuple[str, str]]],
    citations: list[Source],
) -> Report:
    sections = [
        ReportSection(cell=cell, title=cell, prose_lines=lines)
        for cell, lines in prose_lines_by_section.items()
    ]
    return Report(
        company="테스트기업",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=sections,
        citations=list(citations),
        fact_records=[],
        schema_version=_ENGINE_V2_SCHEMA_VERSION,
    )


def _source(number: int, source_id: str) -> Source:
    return Source(
        number=number,
        kind=SourceKind.OTHER,
        label="테스트 자료",
        source_id=source_id,
    )


def test_v2_전부_확인_문장이면_사실_검증_완료로_표시한다() -> None:
    source = _source(1, "v2-frag-a")
    report = _v2_report(
        prose_lines_by_section={
            "past_changes": [
                ("작년 매출은 100억원이다. [1]", ""),
                ("올해 초 신사업을 시작했다. [1]", ""),
            ]
        },
        citations=[source],
    )

    assert source_verification_label(report, source.source_id) == "사실 검증 완료"


def test_v2_해석_문장이_섞이면_부분_검증으로_표시한다() -> None:
    source = _source(1, "v2-frag-b")
    report = _v2_report(
        prose_lines_by_section={
            "past_changes": [
                ("작년 매출은 100억원이다. [1]", ""),
                # 절충안 규칙상 해석 문장은 자기 번호를 원래 안 보이지만,
                # 같은 번호를 확인 문장이 이미 보여주고 있어도 이 함수가
                # 그 사실을 알아채려면 최소한 해석 문장 자신이 그 번호를
                # 보여주는 경우를 잡아야 한다 — 여기서는 그 경우를 직접
                # 재현한다(고아 번호 되살리기로 실제 보일 수 있는 모양).
                ("이 추세는 확대될 전망이다. [1] — 해석", ""),
            ]
        },
        citations=[source],
    )

    assert source_verification_label(report, source.source_id) == "부분 검증"


def test_v2_해석_문장만_있어도_부분_검증으로_표시한다() -> None:
    """전부 확인도 아니고(해석만 있음) 문장이 없는 것도 아니므로 부분 검증이다."""

    source = _source(1, "v2-frag-c")
    report = _v2_report(
        prose_lines_by_section={
            "past_changes": [
                ("이 추세는 확대될 전망이다. [1] — 해석", ""),
            ]
        },
        citations=[source],
    )

    assert source_verification_label(report, source.source_id) == "부분 검증"


def test_v2_인용된_문장이_없으면_본문_사실_없음으로_표시한다() -> None:
    source = _source(1, "v2-frag-d")
    report = _v2_report(
        prose_lines_by_section={
            "past_changes": [
                ("이 자료와 무관한 문장이다. [2]", ""),
            ]
        },
        citations=[source, _source(2, "v2-frag-other")],
    )

    assert source_verification_label(report, source.source_id) == "본문 사실 없음"


def test_v2_부록에_없는_source_id도_본문_사실_없음으로_표시한다() -> None:
    report = _v2_report(
        prose_lines_by_section={"past_changes": [("문장. [1]", "")]},
        citations=[_source(1, "v2-frag-e")],
    )

    assert (
        source_verification_label(report, "존재하지 않는-source-id")
        == "본문 사실 없음"
    )


def test_v1_fact_records가_있으면_v2_문장_모양과_무관하게_기존_카드_로직을_쓴다() -> None:
    """분기가 fact_records 유무로 정확히 갈리는지 — v2 모양 prose_lines를

    붙여도(«[1]» 마커·«— 해석» 표지) fact_records가 있으면 그건 안 보고
    카드만 본다는 것을 증명한다. 카드가 하나도 이 source_id를 안 가리키므로
    v1 로직대로 「본문 사실 없음」이 나와야 한다(v2였다면 [1]이 있어
    「사실 검증 완료」가 나왔을 문장 모양이다).
    """

    fact = FactRecord(
        fact_id="fact-1",
        source_id="fact-src-other",
        status="verified",
        verification_status="verified",
    )
    source = _source(1, "v2-frag-f")
    report = replace(
        _v2_report(
            prose_lines_by_section={
                "past_changes": [("작년 매출은 100억원이다. [1]", "")]
            },
            citations=[source],
        ),
        fact_records=[fact],
    )

    assert source_verification_label(report, source.source_id) == "본문 사실 없음"


def test_해석_표지_상수가_composer_원본과_같다() -> None:
    """★ 이 값은 composer에서 «베껴 온» 것이라 어긋나도 아무도 모른다.

    composer/render.py는 report_standard를 import하지 않는 방향으로 설계돼
    있어(render.py 머리말) ``_V2_INTERPRETATION_MARKER``를 composer에서
    import하지 않고 값만 손으로 옮겨 적었다(render.py가 SECTION_TAGS를
    report_standard에서 미러링하는 것과 같은 방식). 그런데 원본은
    ``INTERPRETATION_MARKER = f" — {GRADE_INTERPRETED}"`` 로 «계산된» 값이라,
    누가 ``GRADE_INTERPRETED``("해석")를 바꾸면 이쪽 미러는 조용히 안 따라간다.
    그러면 ``text.endswith(_V2_INTERPRETATION_MARKER)``가 영원히 False가 되어
    ``has_interpreted``가 늘 False → 모든 자료가 「사실 검증 완료」로 나온다.
    정확히 우리가 고친 「본문 사실 없음」 거짓 표시가 반대 방향으로
    되살아나는 셈이다. 그런데도 다른 시험은 하나도 안 깨진다 — 그래서 두
    값을 여기서 직접 맞대 본다(제품 코드가 아니라 시험이므로 composer를
    import해도 cross-feature import 금지 규칙에 걸리지 않는다).
    """

    from src.features.composer.render import INTERPRETATION_MARKER

    assert _V2_INTERPRETATION_MARKER == INTERPRETATION_MARKER
