"""전자공시 조각 부록에 «원문 주소»가 실리는지 못 박는다.

★ 왜 이 시험이 있나 (실측 결함) — 현대자동차 유료 실측에서 부록 출처 12건 중
  11건이 주소 없이 나갔다. 전자공시 절 조각(사업내용·MD&A 등)에는 조각 자체에
  주소가 없고, 주소를 가진 것은 «그 조각을 떠 온 문서»인데 그 문서 신원이
  render까지 오지 않았기 때문이다. 독자가 원문을 못 열면 근거 표기는 장식이다.
★ 여기서 지키는 것:
  ① filing_meta를 주면 전자공시 조각에 원문 주소·접수번호·공시일이 실린다.
  ② filing_meta가 없으면 예전 동작 그대로다 (주소를 지어내지 않는다).
  ③ 홈페이지·공식 IR 조각은 filing_meta가 있어도 «자기 주소»를 유지한다.
  ④ 공시일 모양이 아니면 날짜를 비운다 — 틀린 날짜는 없는 날짜보다 나쁘다.
"""

from __future__ import annotations

from typing import Any

from src.features.composer.constants import (
    DART_DOCUMENT_HOST,
    GRADE_CONFIRMED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FilingMeta,
    filing_meta_from_raw,
)
from src.features.composer.render import render_report
from src.features.provenance.sources import Source, SourceKind

#: 실측에서 쓰인 실제 모양의 접수번호 (18자리가 아니라 14자리 숫자다).
_RCEPT_NO = "20260813000494"
_EXPECTED_URL = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={_RCEPT_NO}"


# ══════════════════════════════════════════════════════════
# 시험 재료
# ══════════════════════════════════════════════════════════


def _raw_fragments() -> dict[int, dict[str, Any]]:
    """전자공시 조각 2개 + 홈페이지 조각 1개 — 세 갈래를 한 번에 본다."""
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비 전문기업이다."},
        2: {"종류": "MD&A", "원문": "2025년 매출은 전년 대비 늘었다."},
        3: {
            "종류": "홈페이지",
            "원문": "고객의 성공이 최우선 가치다.",
            "출처": "https://www.ganada.example/about",
            "문서일": "2026-08-01",
        },
    }


def _filing_meta() -> FilingMeta:
    return FilingMeta(
        document_id=_RCEPT_NO,
        title="반기보고서 (2026.06)",
        disclosed_at="2026-08-13",
    )


def _composed_report() -> ComposedReport:
    """세 조각을 각각 인용하는 최소 보고서. 장 삭제 없이 나머지는 안내문."""
    sections: list[ComposedSection] = []
    for section_id in SECTION_IDS:
        if section_id == "identity":
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(
                        ComposedSentence(
                            text="반도체 검사 장비를 주력으로 한다.",
                            citations=("1",),
                            grade=GRADE_CONFIRMED,
                        ),
                        ComposedSentence(
                            text="2025년 매출은 전년 대비 늘었다.",
                            citations=("2",),
                            grade=GRADE_CONFIRMED,
                        ),
                        ComposedSentence(
                            text="고객의 성공을 최우선 가치로 내세운다.",
                            citations=("3",),
                            grade=GRADE_CONFIRMED,
                        ),
                    ),
                )
            )
        else:
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(),
                    notice=NOTICE_INSUFFICIENT_EVIDENCE,
                )
            )
    return ComposedReport(
        sections=tuple(sections),
        summary=(
            ComposedSentence(
                text="반도체 검사 장비 중심의 사업 구조다.",
                citations=("1",),
                grade=GRADE_CONFIRMED,
            ),
            ComposedSentence(
                text="최근 매출은 성장 흐름이다.",
                citations=("2",),
                grade=GRADE_CONFIRMED,
            ),
            ComposedSentence(
                text="고객 가치를 앞세운 문화를 내세운다.",
                citations=("3",),
                grade=GRADE_CONFIRMED,
            ),
        ),
    )


def _render(*, with_filing_meta: bool):
    return render_report(
        "가나다전자(주)",
        _composed_report(),
        _raw_fragments(),
        None,
        filing_meta=_filing_meta() if with_filing_meta else None,
    )


def _source_of(report, number: int) -> Source:
    """부록 한 줄을 조각 번호로 집는다 (pipeline Report는 `citations`에 담는다)."""
    matched = [source for source in report.citations if source.number == number]
    assert matched, f"부록에 조각 {number}가 없습니다"
    return matched[0]


# ══════════════════════════════════════════════════════════
# ① filing_meta를 주면 전자공시 조각에 원문 주소가 실린다
# ══════════════════════════════════════════════════════════


def test_전자공시_조각에_원문_주소가_실린다():
    report = _render(with_filing_meta=True)

    for number in (1, 2):
        source = _source_of(report, number)
        assert source.kind is SourceKind.FILING
        assert source.url == _EXPECTED_URL
        assert source.document_id == _RCEPT_NO
        assert source.disclosed_at == "2026-08-13"
        assert source.host == DART_DOCUMENT_HOST


def test_전자공시_조각_라벨에_보고서명과_절이_함께_보인다():
    report = _render(with_filing_meta=True)

    assert _source_of(report, 1).label == "반기보고서 (2026.06) · 사업내용"
    assert _source_of(report, 2).label == "반기보고서 (2026.06) · MD&A"


def test_전자공시_조각의_위치는_어느_절인지를_남긴다():
    """원문 안에서 어디를 봐야 하는지가 없으면 주소만 있어도 못 찾는다."""
    report = _render(with_filing_meta=True)

    assert _source_of(report, 1).location == "사업내용"
    assert _source_of(report, 2).location == "MD&A"


# ══════════════════════════════════════════════════════════
# ② filing_meta가 없으면 예전 동작 그대로 (지어내지 않는다)
# ══════════════════════════════════════════════════════════


def test_공시_신원이_없으면_주소를_지어내지_않는다():
    report = _render(with_filing_meta=False)

    source = _source_of(report, 1)
    assert source.kind is SourceKind.FILING
    assert source.url == ""
    assert source.document_id == ""
    assert source.disclosed_at == ""
    assert source.host == ""
    assert source.label == "전자공시 사업내용"


# ══════════════════════════════════════════════════════════
# ③ 홈페이지 조각은 자기 주소를 지킨다
# ══════════════════════════════════════════════════════════


def test_홈페이지_조각은_공시_주소로_덮이지_않는다():
    report = _render(with_filing_meta=True)

    source = _source_of(report, 3)
    assert source.kind is SourceKind.OTHER
    assert source.url == "https://www.ganada.example/about"
    assert source.document_id == ""
    assert source.collected_at == "2026-08-01"


# ══════════════════════════════════════════════════════════
# ④ 공시 dict → FilingMeta 변환 규칙
# ══════════════════════════════════════════════════════════


def test_공시_dict를_신원으로_바꾼다():
    meta = filing_meta_from_raw(
        {"rcept_no": _RCEPT_NO, "report_nm": "반기보고서 (2026.06)", "rcept_dt": "20260813"}
    )

    assert meta is not None
    assert meta.document_id == _RCEPT_NO
    assert meta.title == "반기보고서 (2026.06)"
    assert meta.disclosed_at == "2026-08-13"


def test_접수번호가_없으면_신원을_만들지_않는다():
    assert filing_meta_from_raw({"report_nm": "반기보고서"}) is None
    assert filing_meta_from_raw(None) is None
    assert filing_meta_from_raw("공시") is None


def test_공시일_모양이_아니면_날짜를_비운다():
    """틀린 공시일은 없는 공시일보다 나쁘다 — 지어내지 않는다."""
    for raw in ("2026-08-13", "202608", "abcdefgh", "", "2026081"):
        meta = filing_meta_from_raw({"rcept_no": _RCEPT_NO, "rcept_dt": raw})
        assert meta is not None
        assert meta.disclosed_at == "", f"{raw!r}에서 날짜를 지어냈습니다"


def test_대문자_키_접수번호도_받는다():
    """DART 응답이 rceptNo로 오는 경로가 있어 두 이름을 모두 받는다."""
    meta = filing_meta_from_raw({"rceptNo": _RCEPT_NO})

    assert meta is not None
    assert meta.document_id == _RCEPT_NO
