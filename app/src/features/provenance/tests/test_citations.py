"""출처 재료 뽑기(`citations.py`) 시험.

★ 뉴스 조각 문자열은 1판 엔진(`analysis_engine/tools/run_pilot.py` collect_news)
  이 실제로 만드는 모양을 그대로 옮겨 썼다 — 줄이면 시험의 뜻이 없어진다.
"""

from __future__ import annotations

import datetime as dt

from src.features.provenance.citations import build_citations
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    parse_sources,
    render_sources,
)

수집일 = dt.date(2026, 8, 15)

# 1판 엔진 실측 그대로 — "(보도일 보도 · 도메인) 제목. 설명"
실제_뉴스_원문 = (
    "(2025-03-12 보도 · mk.co.kr) 파마리서치, 하반기 리쥬란 교육…확대. "
    "8월 부산에서 첫 교육을 연다."
)


# ══════════════════════════════════════════════════════════
# 뉴스 — 원문 앞머리에서 보도일·도메인을 뽑는다
# ══════════════════════════════════════════════════════════


def test_실제_뉴스_원문에서_보도일과_도메인이_정확히_나온다():
    [source] = build_citations({5: {"종류": "뉴스", "원문": 실제_뉴스_원문}}, filing=None, collected_on=수집일)
    assert source.published_at == "2025-03-12"
    assert source.domain == "mk.co.kr"
    assert source.kind is SourceKind.NEWS
    assert source.is_valid is True


def test_뉴스_라벨은_제목까지만_쓴다():
    [source] = build_citations({1: {"종류": "뉴스", "원문": 실제_뉴스_원문}}, filing=None, collected_on=수집일)
    assert source.label == "파마리서치, 하반기 리쥬란 교육…확대"


def test_출처미상이면_도메인을_비운다():
    """지어낸 도메인을 넣지 않는다 — 자리표시자를 그대로 옮기지 않는다."""
    원문 = "(2025-03-12 보도 · 출처미상) 어떤 회사 소식. 본문 내용."
    [source] = build_citations({1: {"종류": "뉴스", "원문": 원문}}, filing=None, collected_on=수집일)
    assert source.domain == ""
    assert source.is_valid is False  # 뉴스는 도메인이 없으면 무효 (기존 규칙)


def test_형식이_안_맞는_뉴스_원문은_날짜_도메인을_비우고_원문_전체를_라벨로_남긴다():
    [source] = build_citations({1: {"종류": "뉴스", "원문": "형식이 다른 문장"}}, filing=None, collected_on=수집일)
    assert source.published_at == ""
    assert source.domain == ""
    assert source.label == "형식이 다른 문장"
    assert source.is_valid is False


# ══════════════════════════════════════════════════════════
# 공시 — filing의 보고서 이름 + 공시일 + 조각 종류
# ══════════════════════════════════════════════════════════

실제_filing = {
    "corp_code": "01078628",
    "corp_name": "에스엠",
    "rcept_no": "20260410002351",
    "report_nm": "사업보고서 (2025.12)",
    "rcept_dt": "20260312",
}


def test_공시_조각의_보고서_이름과_공시일이_맞는다():
    frags = {2: {"종류": "MD&A", "원문": "경영진단 및 분석 내용..."}}
    [source] = build_citations(frags, filing=실제_filing, collected_on=수집일)
    assert source.kind is SourceKind.FILING
    assert source.label == "사업보고서 (2025.12) · MD&A"
    assert source.disclosed_at == "2026-03-12"
    assert source.collected_at == "2026-08-15"
    assert source.publisher == "에스엠"  # DART가 아니라 공시에 책임지는 제출 회사
    assert source.host == "dart.fss.or.kr"


def test_공식_계획_문장이_있는_공시는_무조건_실제값으로_고정하지_않는다():
    frags = {
        6: {
            "종류": "신규사업전망",
            "원문": "회사는 해외 판매를 확대할 계획이다.",
        }
    }

    [source] = build_citations(frags, filing=실제_filing, collected_on=수집일)

    assert "계획" in source.fact_status
    assert source.source_type == "공식 공시"


def test_수집기가_명시한_사실상태를_공시_Source에_그대로_보존한다():
    frags = {
        6: {
            "종류": "사업내용",
            "원문": "회사는 설비 도입을 추진 중이다.",
            "사실상태": "공식 미실행 계획",
        }
    }

    [source] = build_citations(frags, filing=실제_filing, collected_on=수집일)

    assert source.fact_status == "공식 미실행 계획"


def test_filing이_없으면_보고서_이름_없이_조각_종류만_남긴다():
    frags = {1: {"종류": "수익인식", "원문": "수익 인식 기준..."}}
    [source] = build_citations(frags, filing=None, collected_on=수집일)
    assert source.label == "수익인식"
    assert source.disclosed_at == ""
    assert source.collected_at == "2026-08-15"  # 수집일은 filing과 무관하게 안다
    assert source.is_valid is True


def test_rcept_dt가_8자리_숫자가_아니면_공시일을_비운다():
    frag_filing = {**실제_filing, "rcept_dt": "2026-03-12"}  # 이미 변환된 값이 잘못 들어온 경우
    frags = {1: {"종류": "MD&A", "원문": "..."}}
    [source] = build_citations(frags, filing=frag_filing, collected_on=수집일)
    assert source.disclosed_at == ""


def test_재무_API_조각은_filing_날짜를_빌리지_않는다():
    """다른 DART API 호출에서 온 값이라 filing의 공시일과 같다는 보장이 없다."""
    frags = {3: {"종류": "재무", "원문": "주요계정(DART API): 매출액 1000(2025)"}}
    [source] = build_citations(frags, filing=실제_filing, collected_on=수집일)
    assert source.label == "전자공시 주요계정(DART API)"
    assert source.disclosed_at == ""
    assert source.collected_at == "2026-08-15"


# ══════════════════════════════════════════════════════════
# 홈페이지 — 실제 읽은 URL을 라벨로
# ══════════════════════════════════════════════════════════


def test_홈페이지_조각의_URL이_그대로_들어간다():
    frags = {4: {"종류": "홈페이지", "원문": "회사 소개...", "출처": "https://www.company.co.kr/about"}}
    [source] = build_citations(frags, filing=None, collected_on=수집일)
    assert source.kind is SourceKind.OTHER
    assert source.label == "https://www.company.co.kr/about"


def test_홈페이지_출처도_도메인_대신_회사_발행주체를_보존한다():
    frags = {
        4: {
            "종류": "홈페이지",
            "원문": "회사 소개...",
            "출처": "https://www.company.co.kr/about",
        }
    }
    [source] = build_citations(
        frags,
        filing=None,
        collected_on=수집일,
        company_publisher="주식회사 가나다",
    )
    assert source.publisher == "주식회사 가나다"
    assert source.host == "www.company.co.kr"


def test_홈페이지_조각에_출처가_없으면_지어내지_않고_일반_라벨을_쓴다():
    frags = {4: {"종류": "홈페이지", "원문": "회사 소개..."}}
    [source] = build_citations(frags, filing=None, collected_on=수집일)
    assert source.label == "회사 홈페이지"


# ══════════════════════════════════════════════════════════
# 조각 번호 보존 + 정렬
# ══════════════════════════════════════════════════════════


def test_조각_번호는_건너뛰어도_그대로_보존된다():
    frags = {
        5: {"종류": "뉴스", "원문": 실제_뉴스_원문},
        2: {"종류": "MD&A", "원문": "..."},
    }
    sources = build_citations(frags, filing=실제_filing, collected_on=수집일)
    assert [s.number for s in sources] == [2, 5]  # 번호 오름차순 정렬 + 원래 번호 유지


def test_빈_조각_목록은_빈_출처_목록이다():
    assert build_citations({}, filing=None, collected_on=수집일) == []


# ══════════════════════════════════════════════════════════
# ★ 왕복(쓰기 → 읽기) 일치 — build_citations가 만든 것도 깨지지 않아야 한다
# ══════════════════════════════════════════════════════════


def test_만든_출처_목록을_render_parse로_왕복시켜도_같다():
    frags = {
        2: {"종류": "MD&A", "원문": "경영진단..."},
        3: {"종류": "재무", "원문": "주요계정(DART API): 매출액 1000(2025)"},
        5: {"종류": "뉴스", "원문": 실제_뉴스_원문},
        7: {
            "종류": "홈페이지",
            "원문": "회사 소개...",
            "출처": "https://www.company.co.kr/about",
        },
    }
    원본 = build_citations(frags, filing=실제_filing, collected_on=수집일)
    assert all(s.is_valid for s in 원본)  # 왕복 보장은 유효한 출처에 대해서만 뜻이 있다

    왕복 = parse_sources(render_sources(원본))
    # Markdown은 사람이 보는 표시 필드만 왕복한다. canonical source_id·URL·
    # 발행처·원문 위치는 Report JSON 등록부가 보존하며 Markdown 파서가 지우면 안 되는
    # 공개본의 별도 계약이다.
    표시필드 = lambda s: (
        s.number,
        s.kind,
        s.label,
        s.disclosed_at,
        s.collected_at,
        s.published_at,
        s.domain,
    )
    assert [표시필드(s) for s in 왕복] == [표시필드(s) for s in 원본]


def test_selected_evidence_hashes_only_actual_collected_fragment_spans() -> None:
    raw = "첫 문장. 실제로 수집된 둘째 문장이다."
    [source] = build_citations(
        {2: {"종류": "MD&A", "원문": raw}},
        filing=실제_filing,
        collected_on=수집일,
        selected_evidence_by_fragment={
            2: ["실제로 수집된 둘째 문장이다.", "원문에 없는 지어낸 문장"],
        },
    )

    assert evidence_text_hash(raw) in source.evidence_hashes
    assert evidence_text_hash("실제로 수집된 둘째 문장이다.") in source.evidence_hashes
    assert evidence_text_hash("원문에 없는 지어낸 문장") not in source.evidence_hashes
