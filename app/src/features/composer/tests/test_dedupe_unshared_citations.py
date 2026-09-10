# -*- coding: utf-8 -*-
"""근거 조각을 공유하지 않는 장 간 중복도 잡는다 (모든 장 쌍에 같은 규칙).

★ 왜 필요한가 (실측) — 교육서비스 회사의 실제 유료 실행에서 3장과 7장이 같은
  수주 사실을 거의 같은 문장으로 실었다(글자 3-그램 겹침 0.8889). 그런데
  중복 제거는 한 문장도 빼지 않았다. 두 문장이 «같은 공시 문서의 서로 다른
  조각»(3장 v2-frag-8 / 7장 v2-frag-26·28)을 인용해서, «근거 조각을 하나
  이상 공유한다»는 조건에 걸리지 않았기 때문이다. 조각이 다르면 다른
  사실이라는 가정이 한 문서를 여러 조각으로 쪼개는 수집 구조에서 깨진다.

★ 넓히되 문턱을 높인다 — 근거 뒷받침이 없는 짝에는 더 높은 겹침을 요구한다.
  이 파일은 «잡아야 하는 짝»과 «지우면 안 되는 짝»을 실측 문장으로 함께
  못 박는다. 두 번째가 없으면 문턱을 낮춰도 시험이 초록이라 방어가 된다.
"""
from __future__ import annotations

from src.features.composer.constants import SECTION_IDS
from src.features.composer.dedupe import (
    _SAME_DOCUMENT_OVERLAP_THRESHOLD,
    _overlap,
    _signature,
    drop_cross_section_duplicates,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)

#: 실측 — 세 조각 모두 같은 사업보고서(rcept_no 20260316000476)에서 나왔다.
FILING_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260316000476"


def _filing_fragments(*fragment_ids: str) -> tuple[CollectedFragment, ...]:
    """한 공시 문서를 여러 조각으로 쪼갠 실제 모양을 만든다."""
    return tuple(
        CollectedFragment(fragment_id, "공시", "원문", source_url=FILING_URL)
        for fragment_id in fragment_ids
    )

# ── 실측: 같은 사실을 두 장이 각각 다른 조각으로 인용했다 ──
PORTFOLIO_ORDER_TEXT = (
    "2025년 당사는 삼성청년S/W 아카데미 위탁 운영 사업에서 34,917백만원, "
    "K-Digital Training(KDT) 프로그램에서 21,699백만원의 수주를 기록했으며, "
    "이는 정부 정책 기반 교육사업이 교육서비스 포트폴리오의 중요한 수익원임을 "
    "보여준다."
)
OPERATIONS_ORDER_TEXT = (
    "2025년 기준 삼성청년S/W 아카데미 위탁 운영 사업에서 34,917백만원, "
    "K-Digital Training(KDT) 프로그램에서 21,699백만원의 수주를 기록했으며, "
    "이는 정부 정책 기반 교육사업이 회사의 주요 수익원임을 보여준다."
)
# 3장이 이 조각을 한 문장 더 인용했다 — 소유 장 결정(깊이)의 실제 재료다.
PORTFOLIO_SEGMENT_TEXT = (
    "교육서비스 부문은 2024년 289,115백만원에서 2025년 253,902백만원으로 "
    "감소했으며, 이는 국내 경기위축에 따른 공공기관 및 교육예산 축소의 영향을 "
    "받은 것으로 보인다."
)

# ── 실측: 겹쳐 보이지만 «주장이 다른» 짝 (저장본 엔터사 보고서) ──
REVENUE_SEGMENTS_TEXT = (
    "하이브의 수익은 음반·음원, 공연, MD 및 라이선싱, 콘텐츠, 광고·출연료, "
    "팬클럽 등 기타 부문의 여섯 가지 사업 부문으로부터 발생한다."
)
PRODUCT_SEGMENTS_TEXT = (
    "하이브의 핵심 제품·서비스는 음반·음원, 공연, MD 및 라이선싱, 콘텐츠, "
    "광고·출연료, 팬클럽 등 기타 부문의 여섯 가지 사업 부문으로 구성되어 있다."
)


def _report(**by_section: tuple[ComposedSentence, ...]) -> ComposedReport:
    return ComposedReport(sections=tuple(
        ComposedSection(section_id, by_section.get(section_id, ()))
        for section_id in SECTION_IDS
    ))


def _texts(report: ComposedReport, section_id: str) -> list[str]:
    return [
        sentence.text
        for section in report.sections
        if section.section_id == section_id
        for sentence in section.sentences
    ]


# ══════════════════════════════════════════════════════════
# ① 잡아야 하는 짝 — 실측 3장 ↔ 7장
# ══════════════════════════════════════════════════════════


def _order_report() -> ComposedReport:
    return _report(
        portfolio=(
            ComposedSentence(PORTFOLIO_SEGMENT_TEXT, ("v2-frag-8", "v2-frag-153"), "확인"),
            ComposedSentence(PORTFOLIO_ORDER_TEXT, ("v2-frag-8",), "확인"),
        ),
        operations_partners=(
            ComposedSentence(
                OPERATIONS_ORDER_TEXT, ("v2-frag-26", "v2-frag-28"), "확인"
            ),
        ),
    )


def test_조각은_달라도_같은_문서면_거의_같은_문장을_한_장만_남긴다():
    assert not (
        {"v2-frag-8"} & {"v2-frag-26", "v2-frag-28"}
    ), "픽스처가 조각을 공유하면 기존 규칙이 잡아 이 시험이 무의미해집니다"

    cleaned, dropped = drop_cross_section_duplicates(
        _order_report(),
        fragments=_filing_fragments(
            "v2-frag-8", "v2-frag-26", "v2-frag-28", "v2-frag-153"
        ),
    )

    assert dropped == 1
    # 소유 장 = 그 근거를 더 여러 문장으로 다룬 3장 (깊이 2 vs 1).
    assert _texts(cleaned, "portfolio") == [
        PORTFOLIO_SEGMENT_TEXT,
        PORTFOLIO_ORDER_TEXT,
    ]
    assert _texts(cleaned, "operations_partners") == []


def test_문서까지_다르면_닮아도_지우지_않는다():
    """정말 다른 자료에서 온 두 사실은 표현이 닮아도 남긴다 — 기존 안전선."""
    other = tuple(
        CollectedFragment(fragment_id, "공시", "원문",
                          source_url=f"https://other.example/{fragment_id}")
        for fragment_id in ("v2-frag-8", "v2-frag-26", "v2-frag-28", "v2-frag-153")
    )

    cleaned, dropped = drop_cross_section_duplicates(_order_report(), fragments=other)

    assert dropped == 0
    assert _texts(cleaned, "operations_partners") == [OPERATIONS_ORDER_TEXT]


def test_조각을_넘기지_않으면_문서_판정이_꺼진다():
    """넘기지 않은 호출은 예전 동작 그대로다 — 그래서 배선 시험이 따로 있다."""
    _, dropped = drop_cross_section_duplicates(_order_report())
    assert dropped == 0


def test_문서_열쇠가_없는_조각끼리는_같은_문서로_묶이지_않는다():
    """빈 값끼리 묶여 서로 무관한 조각이 «같은 문서»가 되면 안 된다."""
    blank = tuple(
        CollectedFragment(fragment_id, "공시", "원문")
        for fragment_id in ("v2-frag-8", "v2-frag-26", "v2-frag-28", "v2-frag-153")
    )

    _, dropped = drop_cross_section_duplicates(_order_report(), fragments=blank)

    assert dropped == 0


def test_운영_경로가_조각을_실제로_넘긴다():
    """★ 배선 단정 — 넘기지 않으면 이 규칙은 운영에서 통째로 꺼진다.

    호출부의 인자를 직접 읽는다. 시험 안에서 따로 만들어 검사하면 운영 배선이
    빠져도 초록불이 된다.
    """
    import inspect

    from src.features.composer import pipeline

    source = inspect.getsource(pipeline)
    calls = source.count("drop_cross_section_duplicates(")
    # import 줄 1개는 호출이 아니다.
    assert calls >= 2, "호출부가 줄었습니다 — 배선 단정을 다시 맞추세요"
    assert source.count("fragments=_normalize_fragments(verification_fragments)") >= 2
    for chunk in source.split("drop_cross_section_duplicates(")[1:]:
        head = chunk[:600]
        assert "fragments=" in head, (
            "조각을 넘기지 않는 drop_cross_section_duplicates 호출이 있습니다"
        )


def test_실측_짝의_겹침이_문턱_위에_있다():
    """문턱을 올려 이 짝을 다시 놓치면 바로 빨간불이 되게 한다."""
    overlap = _overlap(
        _signature(PORTFOLIO_ORDER_TEXT), _signature(OPERATIONS_ORDER_TEXT)
    )
    assert round(overlap, 4) == 0.8889
    assert overlap >= _SAME_DOCUMENT_OVERLAP_THRESHOLD


# ══════════════════════════════════════════════════════════
# ② 지우면 안 되는 짝 — 목록이 길어서 겹칠 뿐 주장이 다르다
# ══════════════════════════════════════════════════════════


def test_긴_목록_때문에_겹쳐_보이는_다른_주장은_지우지_않는다():
    report = _report(
        business_model=(
            ComposedSentence(REVENUE_SEGMENTS_TEXT, ("3", "8"), "확인"),
        ),
        portfolio=(
            ComposedSentence(PRODUCT_SEGMENTS_TEXT, ("1", "2"), "확인"),
        ),
    )

    cleaned, dropped = drop_cross_section_duplicates(report)

    assert dropped == 0
    assert _texts(cleaned, "business_model") == [REVENUE_SEGMENTS_TEXT]
    assert _texts(cleaned, "portfolio") == [PRODUCT_SEGMENTS_TEXT]


def test_지우면_안_되는_짝의_겹침이_문턱_아래에_있다():
    """문턱을 내리면 이 짝이 지워진다 — 그 순간을 잡는 그물이다."""
    overlap = _overlap(
        _signature(REVENUE_SEGMENTS_TEXT), _signature(PRODUCT_SEGMENTS_TEXT)
    )
    assert round(overlap, 4) == 0.7843
    assert overlap < _SAME_DOCUMENT_OVERLAP_THRESHOLD


def test_문턱이_두_실측값_사이에_있다():
    """0.7843(남겨야 함) < 문턱 <= 0.8889(지워야 함)."""
    assert 0.7843 < _SAME_DOCUMENT_OVERLAP_THRESHOLD <= 0.8889


# ══════════════════════════════════════════════════════════
# ③ 인용을 공유하는 기존 경로는 그대로다
# ══════════════════════════════════════════════════════════


def test_인용을_공유하면_더_낮은_겹침에서도_그대로_잡는다():
    """넓힌 규칙이 기존 규칙을 대체하지 않는다."""
    left = (
        "회사는 국내 주요 대학과 협력해 재직자 대상 인공지능 교육 과정을 "
        "공동으로 개발해 운영하고 있다."
    )
    right = (
        "회사는 국내 주요 대학과 협력하여 재직자 대상 인공지능 교육 과정을 "
        "공동 개발해 제공하고 있다."
    )
    overlap = _overlap(_signature(left), _signature(right))
    assert overlap < _SAME_DOCUMENT_OVERLAP_THRESHOLD, (
        "이 픽스처는 넓힌 규칙이 아니라 기존(인용 공유) 규칙으로 잡혀야 합니다"
    )
    assert round(overlap, 4) == 0.7297

    report = _report(
        identity=(ComposedSentence(left, ("12",), "확인"),),
        culture=(ComposedSentence(right, ("12",), "확인"),),
    )
    cleaned, dropped = drop_cross_section_duplicates(report)

    assert dropped == 1
    assert _texts(cleaned, "identity") == [left]
    assert _texts(cleaned, "culture") == []
