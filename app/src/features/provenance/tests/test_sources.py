"""출처 목록 시험 — 왕복(쓰기→읽기) 일치가 핵심이다.

정본: 확정/07_출력/2_규칙/01_배치와근거표기.md
"""

from __future__ import annotations

import pytest

from src.features.provenance.sources import (
    Source,
    SourceKind,
    count_missing_dates,
    parse_sources,
    render_sources,
)

# 기획서 예시 그대로 — 줄이면 시험의 뜻이 없어진다.
공시_출처 = Source(
    number=2,
    kind=SourceKind.FILING,
    label="감사보고서 제16장 수익인식 주석",
    disclosed_at="2024-03-15",
    collected_at="2026-08-13",
)
뉴스_출처 = Source(
    number=5,
    kind=SourceKind.NEWS,
    label="OO경제",
    published_at="2025-03-12",
    domain="mk.co.kr",
)

기획서_예시_출처목록 = (
    "[출처]\n"
    " [2] 감사보고서 제16장 수익인식 주석\n"
    "     2024-03-15 공시 · 수집 2026-08-13\n"
    " [5] OO경제 2025-03-12  (mk.co.kr)"
)


# ══════════════════════════════════════════════════════════
# is_valid — 뉴스는 보도일 · 도메인이 반드시 있어야 한다
# ══════════════════════════════════════════════════════════


def test_공시_출처는_날짜가_없어도_유효하다():
    assert Source(number=1, kind=SourceKind.FILING, label="사업보고서").is_valid is True


def test_뉴스는_보도일과_도메인이_있어야_유효하다():
    assert 뉴스_출처.is_valid is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"published_at": "", "domain": "mk.co.kr"},
        {"published_at": "2025-03-12", "domain": ""},
        {"published_at": "", "domain": ""},
    ],
)
def test_뉴스는_보도일이나_도메인이_빠지면_무효다(kwargs):
    source = Source(number=5, kind=SourceKind.NEWS, label="OO경제", **kwargs)
    assert source.is_valid is False


def test_번호가_0이하면_무효다():
    assert Source(number=0, kind=SourceKind.FILING, label="사업보고서").is_valid is False


def test_이름이_비어_있으면_무효다():
    assert Source(number=1, kind=SourceKind.FILING, label="  ").is_valid is False


# ══════════════════════════════════════════════════════════
# 쓰기 — 기획서 예시와 문자 그대로 같아야 한다
# ══════════════════════════════════════════════════════════


def test_기획서_예시와_렌더링_결과가_한_글자도_다르지_않다():
    assert render_sources([공시_출처, 뉴스_출처]) == 기획서_예시_출처목록


def test_공시일만_있으면_공시일만_적는다():
    source = Source(
        number=1, kind=SourceKind.FILING, label="사업보고서", disclosed_at="2024-03-15"
    )
    assert render_sources([source]) == "[출처]\n [1] 사업보고서\n     2024-03-15 공시"


def test_수집일만_있으면_수집일만_적는다():
    source = Source(
        number=1, kind=SourceKind.FILING, label="사업보고서", collected_at="2026-08-13"
    )
    assert render_sources([source]) == "[출처]\n [1] 사업보고서\n     수집 2026-08-13"


def test_날짜가_전혀_없으면_둘째_줄이_없다():
    source = Source(number=1, kind=SourceKind.OTHER, label="회사 홈페이지")
    assert render_sources([source]) == "[출처]\n [1] 회사 홈페이지"


def test_빈_목록은_머리말만_낸다():
    assert render_sources([]) == "[출처]"


# ══════════════════════════════════════════════════════════
# 읽기 — 기획서 예시를 그대로 되읽는다
# ══════════════════════════════════════════════════════════


def test_기획서_예시를_파싱하면_두_출처가_나온다():
    parsed = parse_sources(기획서_예시_출처목록)
    assert len(parsed) == 2
    assert parsed[0] == 공시_출처
    assert parsed[1] == 뉴스_출처


def test_번호는_새로_매기지_않고_그대로_읽는다():
    """AI가 고른 조각 번호(2·5처럼 건너뛴 번호)를 그대로 보존한다."""
    parsed = parse_sources(기획서_예시_출처목록)
    assert [s.number for s in parsed] == [2, 5]


def test_출처가_없는_블록은_빈_목록이다():
    assert parse_sources("[출처]") == []


def test_출처_블록이_아닌_글자는_무시한다():
    잡음_섞인_글 = "아무 상관 없는 문장\n\n" + 기획서_예시_출처목록 + "\n뒤에 붙은 잡음"
    parsed = parse_sources(잡음_섞인_글)
    assert len(parsed) == 2


# ══════════════════════════════════════════════════════════
# ★ 왕복(쓰기 → 읽기) 일치 — 이 시험이 이 파일의 핵심이다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "source",
    [
        공시_출처,
        뉴스_출처,
        Source(
            number=1, kind=SourceKind.FILING, label="사업보고서", disclosed_at="2024-03-15"
        ),
        Source(
            number=3, kind=SourceKind.FILING, label="사업보고서", collected_at="2026-08-13"
        ),
        Source(number=9, kind=SourceKind.OTHER, label="회사 홈페이지"),
    ],
)
def test_출처_하나를_쓰고_다시_읽으면_같다(source):
    round_tripped = parse_sources(render_sources([source]))
    assert round_tripped == [source]


def test_출처_목록_전체를_쓰고_다시_읽으면_같다():
    원본 = [
        공시_출처,
        뉴스_출처,
        Source(number=7, kind=SourceKind.OTHER, label="채용공고 원문"),
    ]
    왕복 = parse_sources(render_sources(원본))
    assert 왕복 == 원본


def test_두_번_왕복해도_더_이상_바뀌지_않는다():
    """render→parse→render→parse — 두 번째 왕복부터는 반드시 안정돼야 한다."""
    원본 = [공시_출처, 뉴스_출처]
    한번 = parse_sources(render_sources(원본))
    두번 = parse_sources(render_sources(한번))
    assert 한번 == 두번 == 원본


# ══════════════════════════════════════════════════════════
# 출처·수집일 누락 집계 (C3)
# ══════════════════════════════════════════════════════════


def test_날짜가_다_있으면_누락_0건이다():
    assert count_missing_dates([공시_출처]) == 0


def test_공시일이나_수집일이_하나라도_없으면_누락으로_센다():
    반쪽 = Source(
        number=1, kind=SourceKind.FILING, label="사업보고서", disclosed_at="2024-03-15"
    )
    assert count_missing_dates([반쪽]) == 1


def test_뉴스는_공시_날짜_누락_집계에서_빠진다():
    """뉴스의 보도일 검사는 is_valid가 이미 한다 — 이중으로 세지 않는다."""
    assert count_missing_dates([뉴스_출처]) == 0
