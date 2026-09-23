"""뉴스 «예고 → 실행 보도» 대체를 못 박는다 (2026-09-23 총괄 결정, P14).

같은 정책 안에서도 발표 완료와 미래 시행은 서로 다른 행동이다.
원문 행동의 명시적 완료가 확인될 때만 예고를 대체한다.
★ 여기서 지키는 것:
  ① 같은 사건 열쇠의 «뒤따른 completed 보도»가 본문에 있을 때만 예고를 뺀다.
  ② 실행 보도가 없으면 「이달 중」 같은 상대 표현을 날짜 셈으로 지우지 않는다.
  ③ 열쇠·시제 봉인이 없거나 공식 자료가 섞이면 아무것도 단정하지 않는다.
  ④ 다른 기사 사이는 같은 사건·시제와 전체 문장 동등성이 있어야 합친다.
"""

from __future__ import annotations

from src.features.composer.constants import NOTICE_DUPLICATE_MOVED, SECTION_IDS
from src.features.composer.dedupe import (
    MovedFactRecord,
    drop_cross_section_duplicates,
    drop_superseded_news_plans,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)

_예고_문장 = "가나다전자는 이달 중 청소년 보호 정책의 세부 계획을 발표할 예정이다."
_실행_문장 = "가나다전자는 청소년 보호 정책의 세부 계획을 발표했다."


def _sentence(text: str, citations: tuple[str, ...]) -> ComposedSentence:
    return ComposedSentence(text=text, citations=citations, grade="확인")


def _report(**by_section: tuple[ComposedSentence, ...]) -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id, sentences=by_section.get(section_id, ())
            )
            for section_id in SECTION_IDS
        )
    )


def _news(
    fragment_id: str,
    *,
    key: str,
    temporal: str,
    event_on: str = "",
    published: str = "2026-09-01",
    text: str = "",
) -> CollectedFragment:
    from hashlib import sha256
    text = text or (_예고_문장 if temporal == "planned" else _실행_문장)
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="news",
        text=text,
        source_url=f"https://media.example/news/{fragment_id}",
        document_title=f"기사 {fragment_id}",
        document_date=published,
        document_identity=f"document:media.example:{fragment_id}",
        document_content_sha256=sha256(f"기사 {fragment_id}\n{text}".encode("utf-8")).hexdigest(),
        formal_source_kind="news",
        source_publisher="가나다경제",
        news_event_key=key,
        news_temporal_status=temporal,
        news_claim_kind="company_plan" if temporal == "planned" else "reported_fact",
        news_source_category="official_release",
        news_grounded=True,
        news_event_on=event_on,
    )


def _official(fragment_id: str) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="회사 공식 자료",
        text="가나다전자는 공식 자료에서 사업 구조를 밝혔다.",
        source_url="https://company.example/about",
        formal_source_kind="official_identity_verified_web_page",
    )


def _texts(report: ComposedReport, section_id: str) -> list[str]:
    for section in report.sections:
        if section.section_id == section_id:
            return [sentence.text for sentence in section.sentences]
    raise AssertionError(f"{section_id} 장이 없습니다")


# ══════════════════════════════════════════════════════════
# ① 실행 보도가 있으면 같은 사건의 예고를 뺀다
# ══════════════════════════════════════════════════════════


def test_실행_보도가_있으면_같은_사건의_예고를_뺀다() -> None:
    fragments = (
        _news("p1", key="ev-청소년", temporal="planned", published="2026-09-02"),
        _news("c1", key="ev-청소년", temporal="completed", published="2026-09-08"),
    )
    report = _report(
        future_strategy=(
            _sentence(_실행_문장, ("c1",)),
            _sentence(_예고_문장, ("p1",)),
        ),
    )
    result, dropped = drop_superseded_news_plans(report, fragments=fragments)
    assert dropped == 1
    assert _texts(result, "future_strategy") == [_실행_문장]


def test_예고만_있던_장이_비면_안내문과_이동_장부가_남는다() -> None:
    fragments = (
        _news("p1", key="ev-정책", temporal="planned", published="2026-09-02"),
        _news("c1", key="ev-정책", temporal="completed", published="2026-09-08"),
    )
    sink: list[MovedFactRecord] = []
    report = _report(
        current_challenges=(_sentence(_예고_문장, ("p1",)),),
        past_changes=(_sentence(_실행_문장, ("c1",)),),
    )
    result, dropped = drop_superseded_news_plans(
        report, fragments=fragments, moved_facts_sink=sink
    )
    assert dropped == 1
    emptied = next(
        section for section in result.sections
        if section.section_id == "current_challenges"
    )
    assert emptied.notice == NOTICE_DUPLICATE_MOVED
    assert emptied.moved_to_sections == ("past_changes",)
    assert [record.owner_section_id for record in sink] == ["past_changes"]
    assert sink[0].from_section_id == "current_challenges"


# ══════════════════════════════════════════════════════════
# ② 근거가 모자라면 아무것도 빼지 않는다
# ══════════════════════════════════════════════════════════


def test_실행_보도가_없으면_이달_중_예고를_날짜셈으로_지우지_않는다() -> None:
    # 기준일이 발표 예정 시점을 지났더라도, 실행을 확인한 보도가 없으면 남긴다.
    fragments = (
        _news("p1", key="ev-정책", temporal="planned", published="2026-09-02"),
    )
    report = _report(future_strategy=(_sentence(_예고_문장, ("p1",)),))
    result, dropped = drop_superseded_news_plans(report, fragments=fragments)
    assert dropped == 0
    assert _texts(result, "future_strategy") == [_예고_문장]


def test_다른_사건의_실행_보도는_예고를_대체하지_못한다() -> None:
    fragments = (
        _news("p1", key="ev-정책", temporal="planned", published="2026-09-02"),
        _news("c1", key="ev-다른사건", temporal="completed", published="2026-09-08"),
    )
    report = _report(
        future_strategy=(_sentence(_예고_문장, ("p1",)),),
        past_changes=(_sentence(_실행_문장, ("c1",)),),
    )
    _result, dropped = drop_superseded_news_plans(report, fragments=fragments)
    assert dropped == 0


def test_봉인이_없는_보도는_근거로_쓰지_않는다() -> None:
    fragments = (
        _news("p1", key="", temporal="planned", published="2026-09-02"),
        _news("c1", key="ev-정책", temporal="completed", published="2026-09-08"),
    )
    report = _report(
        future_strategy=(_sentence(_예고_문장, ("p1",)),),
        past_changes=(_sentence(_실행_문장, ("c1",)),),
    )
    _result, dropped = drop_superseded_news_plans(report, fragments=fragments)
    assert dropped == 0


def test_공식_자료가_섞인_문장은_예고로_단정하지_않는다() -> None:
    fragments = (
        _news("p1", key="ev-정책", temporal="planned", published="2026-09-02"),
        _news("c1", key="ev-정책", temporal="completed", published="2026-09-08"),
        _official("f1"),
    )
    report = _report(
        future_strategy=(_sentence(_예고_문장, ("p1", "f1")),),
        past_changes=(_sentence(_실행_문장, ("c1",)),),
    )
    _result, dropped = drop_superseded_news_plans(report, fragments=fragments)
    assert dropped == 0


def test_실행_보도가_예고보다_이르면_대체하지_않는다() -> None:
    fragments = (
        _news("p1", key="ev-정책", temporal="planned", published="2026-09-08"),
        _news("c1", key="ev-정책", temporal="completed", published="2026-09-02"),
    )
    report = _report(
        future_strategy=(_sentence(_예고_문장, ("p1",)),),
        past_changes=(_sentence(_실행_문장, ("c1",)),),
    )
    _result, dropped = drop_superseded_news_plans(report, fragments=fragments)
    assert dropped == 0


# ══════════════════════════════════════════════════════════
# ③ 같은 사건·같은 시제 봉인의 기사 간 바꿔쓰기 (닮음 층)
# ══════════════════════════════════════════════════════════

#: 어미가 비슷해도 다른 기사의 전체 문장 동등성을 추론하지 않는다.
#: 새 기사 간 경로만 보수적으로 닫고, 같은 조각·문서의 기존 비교는 유지한다.
_바꿔쓰기_원문 = (
    "가나다전자는 청소년 이용 제한 정책을 도입해 보호 체계를 "
    "강화했다고 공식 발표했다."
)
_바꿔쓰기_변형 = (
    "가나다전자는 청소년 이용 제한 정책을 도입하여 보호 체계를 "
    "강화했다고 공식 발표했다."
)


def test_같은_사건_같은_시제라도_다른기사의_바꿔쓰기는_보존한다() -> None:
    fragments = (
        _news("a1", key="ev-파트너십", temporal="completed", published="2026-09-01"),
        _news("a2", key="ev-파트너십", temporal="completed", published="2026-09-02"),
    )
    report = _report(
        operations_partners=(_sentence(_바꿔쓰기_원문, ("a1",)),),
        identity=(_sentence(_바꿔쓰기_변형, ("a2",)),),
    )
    _result, dropped = drop_cross_section_duplicates(report, fragments=fragments)
    assert dropped == 0


def test_시제가_다른_봉인끼리는_닮아도_비교하지_않는다() -> None:
    # 예고와 실행 보도는 서로 다른 사실이다 — 닮음 층이 합쳐 버리면 안 된다.
    fragments = (
        _news("a1", key="ev-파트너십", temporal="planned", published="2026-09-01"),
        _news("a2", key="ev-파트너십", temporal="completed", published="2026-09-02"),
    )
    report = _report(
        operations_partners=(_sentence(_바꿔쓰기_원문, ("a1",)),),
        identity=(_sentence(_바꿔쓰기_변형, ("a2",)),),
    )
    _result, dropped = drop_cross_section_duplicates(report, fragments=fragments)
    assert dropped == 0


def test_사건_열쇠가_다르면_닮아도_비교하지_않는다() -> None:
    fragments = (
        _news("a1", key="ev-하나", temporal="completed", published="2026-09-01"),
        _news("a2", key="ev-둘", temporal="completed", published="2026-09-02"),
    )
    report = _report(
        operations_partners=(_sentence(_바꿔쓰기_원문, ("a1",)),),
        identity=(_sentence(_바꿔쓰기_변형, ("a2",)),),
    )
    _result, dropped = drop_cross_section_duplicates(report, fragments=fragments)
    assert dropped == 0


def test_같은_봉인이라도_주장이_다르면_남는다() -> None:
    # 같은 사건을 다룬 두 기사가 서로 다른 세부 사실을 보태면 둘 다 남는다.
    fragments = (
        _news("a1", key="ev-파트너십", temporal="completed", published="2026-09-01"),
        _news("a2", key="ev-파트너십", temporal="completed", published="2026-09-02"),
    )
    report = _report(
        operations_partners=(_sentence(_바꿔쓰기_원문, ("a1",)),),
        identity=(
            _sentence(
                "회사의 신인개발 부문은 캐스팅팀과 트레이닝팀으로 구성되어 "
                "연습생을 모집하고 체계적인 트레이닝을 제공한다.",
                ("a2",),
            ),
        ),
    )
    _result, dropped = drop_cross_section_duplicates(report, fragments=fragments)
    assert dropped == 0
