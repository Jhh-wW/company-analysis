"""중복 소유 판정이 의미 칸·시제 봉인을 존중하는지 못 박는다 (P18).

★ 왜 이 시험이 있나 (실측 — 저장 실측) — 2장 «사업 구조와 수익 모델»이 실적·
  투자 «보도»를 여러 문장으로 다뤄 깊이 우위로 사실을 소유했고, 6장 «성장
  전략»에 이미 일어난 청소년 대응 사건이 남았다.
★ 여기서 지키는 것:
  ① typed 조각이 지원한다고 봉인한 장이 있으면 소유 후보를 그 장으로 좁힌다.
  ② 봉인이 없으면(legacy) 종전 규칙 그대로다 — 근거 없이 소유를 옮기지 않는다.
  ③ 뉴스 봉인이 전부 completed면 미래 계획 장(6장)은 동점 소유를 갖지 않는다.
"""

from __future__ import annotations

from src.features.composer.constants import SECTION_IDS
from src.features.composer.dedupe import (
    MovedFactRecord,
    drop_cross_section_duplicates,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION

_실적_문장 = (
    "가나다전자의 연간 매출은 전년 대비 크게 증가해 성장 흐름을 이어 갔다고 "
    "보도가 전했다."
)
_실적_문장_변형 = (
    "가나다전자의 연간 매출은 전년 대비 크게 증가해 성장 흐름을 이어 갔다는 "
    "보도가 있었다."
)
_대응_문장 = (
    "가나다전자는 청소년 이용 제한 정책을 도입해 보호 체계를 강화하는 대응을 "
    "이미 시행하고 있다."
)
_대응_문장_변형 = (
    "가나다전자는 청소년 이용 제한 정책을 도입해 보호 체계를 강화하는 대응을 "
    "이어 갈 계획이다."
)


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
    slots: tuple[str, ...] = (),
    key: str = "",
    temporal: str = "",
) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="news",
        text="가나다전자 보도 원문",
        source_url=f"https://media.example/news/{fragment_id}",
        document_title=f"기사 {fragment_id}",
        document_date="2026-09-01",
        document_identity=f"document:media.example:{fragment_id}",
        formal_source_kind="news",
        source_publisher="가나다경제",
        supported_claim_slots=slots,
        news_event_key=key,
        news_temporal_status=temporal,
        news_claim_kind=("company_plan" if temporal == "planned" else "reported_fact") if temporal else "",
        news_grounded=bool(temporal),
    )


def _texts(report: ComposedReport, section_id: str) -> list[str]:
    for section in report.sections:
        if section.section_id == section_id:
            return [sentence.text for sentence in section.sentences]
    raise AssertionError(f"{section_id} 장이 없습니다")


def _past_slot() -> str:
    return sorted(CLAIM_SLOTS_BY_SECTION["past_changes"])[0]


def test_의미_칸이_깊이_우위를_이긴다() -> None:
    # 조각이 «4장 칸을 지원한다»고 봉인했으면, 2장이 그 조각을 더 많이
    # 인용했어도 4장이 소유한다.
    fragments = (_news("n1", slots=(_past_slot(),)),)
    sink: list[MovedFactRecord] = []
    report = _report(
        business_model=(
            _sentence(_실적_문장, ("n1",)),
            _sentence("가나다전자는 구독과 광고 두 축으로 수익을 얻는다고 보도됐다.", ("n1",)),
            _sentence("가나다전자는 기업 고객 비중을 늘리고 있다고 보도됐다.", ("n1",)),
        ),
        past_changes=(_sentence(_실적_문장_변형, ("n1",)),),
    )
    result, dropped = drop_cross_section_duplicates(
        report, fragments=fragments, moved_facts_sink=sink
    )
    assert dropped == 1
    assert _실적_문장_변형 in _texts(result, "past_changes")
    assert _실적_문장 not in _texts(result, "business_model")
    assert [record.owner_section_id for record in sink] == ["past_changes"]
    assert sink[0].from_section_id == "business_model"


def test_봉인_없는_조각은_소유를_바꾸지_않는다() -> None:
    # legacy 조각(의미 칸 없음)은 종전 규칙 그대로 — 깊이 우위 장이 소유한다.
    fragments = (_news("n1"),)
    report = _report(
        business_model=(
            _sentence(_실적_문장, ("n1",)),
            _sentence("가나다전자는 구독과 광고 두 축으로 수익을 얻는다고 보도됐다.", ("n1",)),
            _sentence("가나다전자는 기업 고객 비중을 늘리고 있다고 보도됐다.", ("n1",)),
        ),
        past_changes=(_sentence(_실적_문장_변형, ("n1",)),),
    )
    result, dropped = drop_cross_section_duplicates(report, fragments=fragments)
    assert dropped == 1
    assert _실적_문장 in _texts(result, "business_model")
    assert _texts(result, "past_changes") == []


def test_완료_봉인_보도는_6장이_동점_소유를_갖지_않는다() -> None:
    # 이미 일어난 대응 사건(completed 봉인)은 미래 표지가 붙은 6장 문장이
    # 있어도 5장이 소유한다.
    fragments = (_news("n1", key="ev-대응", temporal="completed"),)
    report = _report(
        current_challenges=(_sentence(_대응_문장, ("n1",)),),
        future_strategy=(_sentence(_대응_문장_변형, ("n1",)),),
    )
    result, dropped = drop_cross_section_duplicates(report, fragments=fragments)
    assert dropped == 1
    assert _texts(result, "current_challenges") == [_대응_문장]
    assert _texts(result, "future_strategy") == []


def test_지원_장이_후보에_없으면_소유를_옮기지_않는다() -> None:
    # 조각이 3장 칸만 지원하는데 중복이 1장·7장 사이면, 지원 정보로는 판단할
    # 수 없으므로 종전 규칙(깊이→정본 순서)이 그대로 정한다.
    portfolio_slot = sorted(CLAIM_SLOTS_BY_SECTION["portfolio"])[0]
    fragments = (_news("n1", slots=(portfolio_slot,)),)
    report = _report(
        identity=(_sentence(_실적_문장, ("n1",)),),
        operations_partners=(_sentence(_실적_문장_변형, ("n1",)),),
    )
    result, dropped = drop_cross_section_duplicates(report, fragments=fragments)
    assert dropped == 1
    assert _texts(result, "identity") == [_실적_문장]
    assert _texts(result, "operations_partners") == []
