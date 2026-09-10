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

#: 실측 — 같은 공시의 legacy 조각과 typed 조각이 공유하는 문서 신원.
#: 두 생산 갈래(evidence_transport의 legacy 갈래·typed 갈래)가 같은 규칙으로
#: 만든다: DART 접수번호 기반 ``document:<host>:<접수번호>``.
FILING_IDENTITY = "document:dart.fss.or.kr:20260316000476"

#: 실측 — typed 조각만 갖는 문서 전체 원문 지문. legacy 조각은 이 필드가 생기기
#: 전 모양이라 항상 빈 문자열이다.
FILING_SHA256 = "73d6677" + "0" * 57


def _filing_fragments(*fragment_ids: str) -> tuple[CollectedFragment, ...]:
    """한 공시 문서를 여러 조각으로 쪼갠 실제 모양을 만든다."""
    return tuple(
        CollectedFragment(fragment_id, "공시", "원문", source_url=FILING_URL)
        for fragment_id in fragment_ids
    )


def _legacy_fragment(fragment_id: str, *, identity: str = FILING_IDENTITY):
    """운영 legacy 조각 — 문서 지문이 «항상» 빈 문자열이고 신원만 있다."""
    return CollectedFragment(
        fragment_id,
        "매출수주",
        "원문",
        document_identity=identity,
        document_content_sha256="",
    )


def _typed_fragment(
    fragment_id: str,
    *,
    identity: str = FILING_IDENTITY,
    sha256: str = FILING_SHA256,
):
    """운영 typed 조각 — 신원과 문서 지문을 «둘 다» 싣는다."""
    return CollectedFragment(
        fragment_id,
        "공시",
        "원문",
        source_url=FILING_URL,
        document_identity=identity,
        document_content_sha256=sha256,
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


def test_legacy조각과_typed조각이_섞여도_같은_문서로_묶인다():
    """★ 실측 실행이 정확히 이 모양이었다 — 열쇠 «하나»로는 갈라진다.

    3장이 인용한 조각은 legacy(지문 없음·신원만), 7장이 인용한 두 조각은
    typed(지문 있음)였다. 우선순위 하나만 열쇠로 쓰면 3장은 신원, 7장은
    지문이 열쇠가 되어 교집합이 비고 «같은 문서» 판정이 통째로 꺼진다.
    겹침은 0.8889로 문턱 위인데도 한 문장도 빠지지 않았다.
    """
    mixed = (
        _legacy_fragment("v2-frag-8"),
        _typed_fragment("v2-frag-26"),
        _typed_fragment("v2-frag-28"),
        _legacy_fragment("v2-frag-153"),
    )
    assert mixed[0].document_content_sha256 == ""
    assert mixed[1].document_content_sha256 != ""
    assert mixed[0].document_identity == mixed[1].document_identity

    cleaned, dropped = drop_cross_section_duplicates(_order_report(), fragments=mixed)

    assert dropped == 1
    assert _texts(cleaned, "portfolio") == [
        PORTFOLIO_SEGMENT_TEXT,
        PORTFOLIO_ORDER_TEXT,
    ]
    assert _texts(cleaned, "operations_partners") == []


def test_legacy와_typed가_서로_다른_문서면_닮아도_지우지_않는다():
    """열쇠를 집합으로 넓혀도 «다른 문서»의 안전선은 그대로다 — 음성 짝."""
    mixed = (
        _legacy_fragment("v2-frag-8", identity="document:dart.fss.or.kr:20250101000001"),
        _typed_fragment(
            "v2-frag-26",
            identity="document:dart.fss.or.kr:20260316000476",
            sha256="a" * 64,
        ),
        _typed_fragment(
            "v2-frag-28",
            identity="document:dart.fss.or.kr:20260316000476",
            sha256="a" * 64,
        ),
        _legacy_fragment(
            "v2-frag-153", identity="document:dart.fss.or.kr:20250101000001"
        ),
    )

    cleaned, dropped = drop_cross_section_duplicates(_order_report(), fragments=mixed)

    assert dropped == 0
    assert _texts(cleaned, "operations_partners") == [OPERATIONS_ORDER_TEXT]


def test_흔한_문서명은_다른_문서를_묶는_다리가_되지_않는다():
    """「사업보고서」 같은 제목은 강한 열쇠가 하나도 없을 때만 쓴다."""
    same_title = (
        CollectedFragment(
            "v2-frag-8",
            "공시",
            "원문",
            document_title="사업보고서",
            document_identity="document:dart.fss.or.kr:20250101000001",
        ),
        CollectedFragment(
            "v2-frag-26",
            "공시",
            "원문",
            document_title="사업보고서",
            document_identity="document:dart.fss.or.kr:20260316000476",
            document_content_sha256="b" * 64,
        ),
        CollectedFragment(
            "v2-frag-28",
            "공시",
            "원문",
            document_title="사업보고서",
            document_identity="document:dart.fss.or.kr:20260316000476",
            document_content_sha256="b" * 64,
        ),
        CollectedFragment(
            "v2-frag-153",
            "공시",
            "원문",
            document_title="사업보고서",
            document_identity="document:dart.fss.or.kr:20250101000001",
        ),
    )

    _, dropped = drop_cross_section_duplicates(_order_report(), fragments=same_title)

    assert dropped == 0


def test_강한_열쇠가_없으면_문서명으로_묶는다():
    """예전 동작 보존 — 지문·신원·주소가 하나도 없는 조각은 문서명이 열쇠다."""
    title_only = tuple(
        CollectedFragment(fragment_id, "공시", "원문", document_title="사업보고서")
        for fragment_id in ("v2-frag-8", "v2-frag-26", "v2-frag-28", "v2-frag-153")
    )

    _, dropped = drop_cross_section_duplicates(_order_report(), fragments=title_only)

    assert dropped == 1


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


def test_운영_경로가_조각을_실제로_넘긴다(monkeypatch):
    """★ 배선 단정 — 넘기지 않으면 이 규칙은 운영에서 통째로 꺼진다.

    운영 진입점(run_v2)을 실제로 돌리고 «그 호출이 받은 인자»를 단정한다.
    시험 안에서 따로 만들어 검사하면 운영 배선이 빠져도 초록불이 된다.

    ★ 예전 판은 소스 문자열을 세는 검사였는데
      ``source.count("fragments=_normalize_fragments(verification_fragments)")``
      가 dedupe와 무관한 다른 호출 두 건만으로도 충족돼 사실상 공전했다.
    """
    from src.features.composer import dedupe as dedupe_module
    from src.features.composer import pipeline
    from src.features.composer.logic import _normalize_fragments
    from src.features.composer.tests.test_pipeline import (
        _FakeReviewer,
        _FakeWriter,
        _raw_fragments,
    )

    seen: list[tuple[tuple, dict]] = []

    def spy(*args, **kwargs):
        seen.append((args, kwargs))
        return dedupe_module.drop_cross_section_duplicates(*args, **kwargs)

    monkeypatch.setattr(pipeline, "drop_cross_section_duplicates", spy)

    raw = _raw_fragments()
    pipeline.run_v2(
        "가나다전자", raw, None,
        writer_ask=_FakeWriter(), reviewer_ask=_FakeReviewer(),
        corp_type="상장사", as_of_date="2026-08-24",
    )

    assert seen, "운영 경로가 중복 제거를 아예 부르지 않았습니다"
    expected = _normalize_fragments(raw)
    assert expected, "이 시험의 조각 픽스처가 비면 아무것도 증명하지 못합니다"
    for _args, kwargs in seen:
        assert "fragments" in kwargs, (
            "조각을 넘기지 않는 drop_cross_section_duplicates 호출이 있습니다"
        )
        assert tuple(kwargs["fragments"]) == expected


def test_모든_호출부가_같은_조각을_넘긴다():
    """보충 경로처럼 이 시험이 못 도는 호출부까지 «구문»으로 전수 확인한다.

    문자열 세기가 아니라 실제 호출 노드만 본다 — 다른 함수의 호출이나 주석은
    세지 않는다.
    """
    import ast
    import inspect

    from src.features.composer import pipeline

    tree = ast.parse(inspect.getsource(pipeline))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "drop_cross_section_duplicates"
    ]
    assert len(calls) >= 2, "호출부가 줄었습니다 — 배선 단정을 다시 맞추세요"
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert "fragments" in keywords, (
            f"{call.lineno}행의 호출이 조각을 넘기지 않습니다"
        )
        assert ast.unparse(keywords["fragments"]) == (
            "_normalize_fragments(verification_fragments)"
        ), f"{call.lineno}행이 다른 조각을 넘깁니다"


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
