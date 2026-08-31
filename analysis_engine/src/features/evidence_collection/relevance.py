"""장별 적합도 결정론 채점 — AI 호출 없이 키워드·구조 신호로만 판정한다.

★ v1 휴리스틱이다. 45개 슬롯마다 실제 DART 공시 표현을 전수 조사하지 않았다
(확인 못 함 — 최종 보고서에 한계로 남긴다). 키워드가 하나도 안 걸리면 조각을
버리지 않고 section_id·slot_id를 빈 문자열로 둔다(수집기는 판정하지 않고
남겨 두는 쪽 — 뒤 단계가 무엇을 더 볼지 정한다).
"""

from __future__ import annotations

from dataclasses import dataclass

from features.evidence_collection import constants as c

#: 슬롯별 키워드 신호. 값은 예시 표현이며 전수 검증되지 않았다(알려진 한계).
SLOT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "identity:corporate_identity": ("당사는", "주식회사", "법인", "설립"),
    "identity:business_definition": ("영위하는", "사업을 영위", "업종"),
    "identity:legal_scope": ("정관", "인가", "허가", "등록"),
    "identity:official_location": ("본점", "소재지", "주소"),
    "identity:self_positioning": ("선도", "대표하는", "자리매김"),

    "business_model:revenue_model": ("매출", "수익", "판매", "과금", "수수료"),
    "business_model:customer_type": ("고객사", "거래처", "수요처"),
    "business_model:sales_channel": ("유통", "채널", "직판", "대리점"),
    "business_model:regional_mix": ("내수", "수출", "해외", "지역별"),
    "business_model:value_exchange": ("제공한다", "대가", "가치"),

    "portfolio:product_role": ("주력 제품", "핵심 제품", "대표 제품"),
    "portfolio:portfolio_priority": ("우선순위", "집중", "중점"),
    "portfolio:customer_fit": ("맞춤", "적합", "요구"),
    "portfolio:revenue_link": ("매출 기여", "매출 비중"),
    "portfolio:lifecycle_stage": ("출시", "단종", "성장기", "도입기"),

    "past_changes:historical_performance": ("매출액", "영업이익", "당기순이익"),
    "past_changes:completed_execution": ("완료", "준공", "출시했다", "확대했다"),
    "past_changes:cumulative_change": ("증가", "감소", "전년 대비", "누적"),
    "past_changes:change_context": ("배경", "이유", "요인"),
    "past_changes:change_limit": ("제한적", "한계", "제약이 있었다"),

    "current_challenges:issue": ("과제", "위험", "리스크", "규제"),
    "current_challenges:response": ("대응", "대책", "조치"),
    "current_challenges:initial_signal": ("징후", "초기", "발생"),
    "current_challenges:unresolved_gap": ("미해결", "지속되고", "여전히"),
    "current_challenges:next_check": ("점검", "모니터링", "확인 예정"),

    "future_strategy:stated_plan": ("계획이며", "추진할 계획", "예정이다"),
    "future_strategy:plan_status": ("진행", "검토 중", "착수"),
    "future_strategy:plan_timing": ("2025년", "2026년", "분기까지"),
    "future_strategy:plan_condition": ("조건부", "전제로"),
    "future_strategy:execution_signal": ("투자", "착공", "체결"),

    "operations_partners:value_chain": ("공급망", "가치사슬", "밸류체인"),
    "operations_partners:operating_role": ("생산", "제조", "운영한다"),
    "operations_partners:supply_relation": ("공급업체", "협력사", "원재료"),
    "operations_partners:distribution_relation": ("판매처", "유통망"),
    "operations_partners:partnership": ("제휴", "협약", "협력사와"),

    "culture:leadership": ("경영진", "대표이사", "리더십"),
    "culture:work_principle": ("핵심가치", "원칙에 따라", "행동강령"),
    "culture:decision_process": ("의사결정", "위원회를 통해"),
    "culture:organization_change": ("조직개편", "신설", "통합"),
    "culture:verified_case": ("수상", "인증받았다", "사례"),

    "competitive_position:comparison_target": ("동종업계", "경쟁사", "업계는"),
    "competitive_position:comparison_metric": ("점유율", "순위", "규모"),
    "competitive_position:comparison_basis": ("공시 기준", "근거로"),
    "competitive_position:comparison_judgment": ("경쟁력", "강점", "우위"),
    "competitive_position:limitation": ("확인되지 않는다", "한계", "제약"),
}

#: 장별 구조 신호(제목 줄에 등장하면 가산점). SLOT_KEYWORDS와 같은 v1 한계.
SECTION_HEADING_HINTS: dict[str, tuple[str, ...]] = {
    "identity": ("회사의 개요",),
    "business_model": ("사업의 내용",),
    "portfolio": ("주요 제품", "제품 및 서비스"),
    "past_changes": ("재무에 관한 사항", "요약재무정보"),
    "current_challenges": ("경영진단", "위험관리"),
    "future_strategy": ("향후 계획",),
    "operations_partners": ("계약 및 협력관계", "생산 및 설비"),
    "culture": ("임원 및 직원",),
    "competitive_position": ("시장 현황",),
}


@dataclass(frozen=True)
class SlotScore:
    section_id: str
    slot_id: str
    score_millis: int
    reason_codes: tuple[str, ...]


def score_fragment_text(text: str, section_heading: str = "") -> SlotScore | None:
    """조각 원문 하나에 가장 잘 맞는 (장, 슬롯)을 고른다. 신호가 없으면 None.

    동점이면 SLOT_KEYWORDS 선언 순서(=장·슬롯 어휘 정본 순서)의 앞선 슬롯이
    이긴다 — dict 순회는 삽입 순서를 보존하므로 결정론이 유지된다.
    """
    best: SlotScore | None = None
    for slot_id, keywords in SLOT_KEYWORDS.items():
        hits = [keyword for keyword in keywords if keyword in text]
        if not hits:
            continue
        section_id = c.SLOT_SECTION_OF[slot_id]
        score = min(c.RELEVANCE_MAX_SCORE_MILLIS, len(hits) * c.RELEVANCE_KEYWORD_HIT_SCORE_MILLIS)
        reason_codes = [f"keyword_hit:{slot_id}"]
        for hint in SECTION_HEADING_HINTS.get(section_id, ()):
            if hint in section_heading:
                score = min(c.RELEVANCE_MAX_SCORE_MILLIS, score + c.RELEVANCE_HEADING_BONUS_MILLIS)
                reason_codes.append(f"heading_match:{section_id}")
                break
        candidate = SlotScore(
            section_id=section_id, slot_id=slot_id, score_millis=score,
            reason_codes=tuple(reason_codes),
        )
        if best is None or candidate.score_millis > best.score_millis:
            best = candidate
    return best
