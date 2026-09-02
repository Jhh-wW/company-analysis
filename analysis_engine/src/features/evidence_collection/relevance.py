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
    # 「당사는」는 거의 모든 공시 문단에 붙는 문법상 주어일 뿐 회사 정체성
    # 사실이 아니다. 이 약한 한 단어가 사업모델·고객·가치 문단을 identity로
    # 납치한 실측 결함 때문에 직접 정체성 표현만 남긴다.
    "identity:corporate_identity": ("주식회사", "법인", "설립"),
    "identity:business_definition": ("영위하는", "사업을 영위", "업종"),
    "identity:legal_scope": ("정관", "인가", "허가", "등록"),
    "identity:official_location": ("본점", "소재지", "주소"),
    "identity:self_positioning": ("선도", "대표하는", "자리매김"),

    # ★ 「매출」·「수익」 단독 키워드는 뺐다 — 「매출액은 2023년...」처럼 실적
    #   수치를 나열하는 문장(past_changes 몫)까지 전부 걸려, 수집기 1차
    #   표적 우선순위(아래 score_fragment_text) 때문에 실제로는 더 적합한
    #   보조 슬롯을 가리는 부작용이 있었다(실측). 「어떻게 돈을
    #   버는가」를 설명하는 구체적 표현만 남긴다.
    "business_model:revenue_model": ("판매에서 발생", "수익원", "과금", "수수료", "매출 구조"),
    "business_model:customer_type": ("고객사", "거래처", "수요처"),
    "business_model:sales_channel": ("유통", "채널", "직판", "대리점"),
    "business_model:regional_mix": ("내수", "수출", "해외", "지역별"),
    "business_model:value_exchange": ("제공한다", "대가", "가치"),

    "portfolio:product_role": ("주력 제품", "핵심 제품", "대표 제품"),
    "portfolio:portfolio_priority": ("우선순위", "집중", "중점"),
    "portfolio:customer_fit": ("맞춤", "적합", "요구"),
    "portfolio:revenue_link": ("매출 기여", "매출 비중"),
    "portfolio:lifecycle_stage": ("출시", "단종", "성장기", "도입기"),

    # ★ past_changes:historical_performance는 여기 없다. 구조화 실적기가
    #   재무 API 수치로 직접 채운다
    #   (constants.COLLECTOR_SLOTS_BY_SECTION 주석 참고).
    "past_changes:completed_execution": ("완료", "준공", "출시했다", "확대했다"),
    "past_changes:cumulative_change": ("증가", "감소", "전년 대비", "누적"),
    "past_changes:change_context": ("배경", "이유", "요인"),
    "past_changes:change_limit": ("제한적", "한계", "제약이 있었다"),

    "current_challenges:issue": ("과제", "위험", "리스크", "규제"),
    "current_challenges:response": ("대응", "대책", "조치"),
    # 「발생」 단독은 「매출이 판매에서 발생」 같은 수익모델 문장을 과제로
    # 오복제한다. 문제의 시작을 직접 뜻하는 좁은 표현만 쓴다.
    "current_challenges:initial_signal": ("징후", "초기", "문제가 발생", "위험이 발생"),
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

    # ★ 비교 대상·지표·근거·판단 4종(comparison_*·
    #   limitation)은 여기 없다. 구조화 검증기가 채운다. 수집기는 «자사가 스스로
    #   서술한 시장 내 위치·강점»만 self_context로 담는다(상대 이름·순위 비교
    #   없이 자사 서술만 — composer 45개 어휘에 없던 새 슬롯).
    # ★ 「선도」는 identity:self_positioning과도
    #   겹친다(둘 다 composer 45개 어휘의 자기규정 개념과 인접) — 알려진
    #   중복이며, 실제 배정은 점수·표제 힌트로 갈린다(동점이면 아래 채점
    #   함수가 수집기 슬롯을 우선한다).
    "competitive_position:self_context": (
        "경쟁력을 갖추고", "강점으로", "차별화된", "보유하고 있다", "자사의 강점",
        "시장점유율", "선도", "최초로", "독보적",
    ),
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

    전체 SLOT_KEYWORDS(수집기 필수 슬롯 + 보조 태그로 허용된 나머지 composer
    슬롯)를 한 번에 채점해 순수 점수가 가장 높은 슬롯을 쓴다.
    historical_performance·비교 4종은 SLOT_KEYWORDS에 아예 없으므로(다른
    엔진이 채움) 애초에 후보가 되지 않는다.

    ★ 동점일 때만 COLLECTOR_SLOTS_BY_SECTION(수집기 1차 표적)을
    우선한다 — «항상» 수집기 슬롯을 이기게 하면 다른
    장의 약한 우연 일치(예: 실적 문단에 스친 「고객사」 한 단어)가 그
    장의 훨씬 강한 진짜 신호를 밀어낸다(실측 — 아래
    tests/test_relevance.py에 회귀 시험으로 고정). 순수 점수 비교가
    먼저이고, 동점에서만 수집기 슬롯이 이긴다.
    """
    scores = score_fragment_slots(text, section_heading)
    return scores[0] if scores else None


def score_fragment_slots(text: str, section_heading: str = "") -> tuple[SlotScore, ...]:
    """원문에 직접 신호가 있는 한 장의 슬롯들을 결정론 순서로 돌려준다.

    한 문단은 현실에서 여러 사실을 함께 말한다. 예를 들어 「주요 매출은
    제품 판매에서 발생하며 고객사에 서비스를 제공한다」는 한 문단이지만
    revenue_model·customer_type·value_exchange라는 서로 다른 세 질문에
    각각 답한다. 예전의 단일 winner 방식은 나머지 두 근거를 버려 충분한
    공시도 9장 게이트에서 막았다.

    여기서는 **실제 키워드가 직접 맞은 슬롯만** 채점한 뒤, 가장 강한 슬롯의
    장을 그 문단의 의미 경계로 고정한다. 따라서 한 문단이 여러 칸을 채울 수는
    있어도, 「2025년」·「당사는」 같은 약한 우연 일치 때문에 여러 장으로
    흩어지지는 않는다. 문장·절 분리는 다음 수집기 세대의 측정 과제로 남기고,
    지금은 문단 provenance(원문 위치·해시)를 거짓으로 잘게 쪼개지 않는
    보수적 경계를 택한다.

    순서는 점수 내림차순 → 수집기 필수 슬롯 우선 → 정책 선언 순서라 실행마다
    같다. 기존 단일 결과 API ``score_fragment_text``는 이 튜플의 첫 값을
    돌려 이전 호출자와의 호환을 유지한다.
    """

    scored: list[tuple[int, int, bool, SlotScore]] = []
    for declaration_index, (slot_id, keywords) in enumerate(SLOT_KEYWORDS.items()):
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
        scored.append((
            declaration_index,
            len(hits),
            any(code.startswith("heading_match:") for code in reason_codes),
            SlotScore(
                section_id=section_id,
                slot_id=slot_id,
                score_millis=score,
                reason_codes=tuple(reason_codes),
            ),
        ))

    if not scored:
        return ()

    # 문단의 장을 슬롯 선언 순서가 아니라 그 장에서 발견된 직접 신호의
    # 합으로 고른다. 「당사는」 같은 한 신호와 고객·수익·가치 세 신호가
    # 동점 슬롯로 나왔을 때 앞에 선언된 identity가 이기는 결함을 막는다.
    # 제목 힌트는 직접 신호 총합·슬롯 수가 같은 경우에만 구조 근거로 쓴다.
    section_declaration_index: dict[str, int] = {}
    section_rank: dict[str, tuple[int, int, int, int, int]] = {}
    for declaration_index, hit_count, heading_matched, slot_score in scored:
        section_id = slot_score.section_id
        section_declaration_index.setdefault(section_id, declaration_index)
        previous = section_rank.get(section_id, (0, 0, 0, 0, 0))
        section_rank[section_id] = (
            previous[0] + hit_count,
            previous[1] + 1,
            max(previous[2], slot_score.score_millis),
            previous[3] + int(heading_matched),
            previous[4] + int(slot_score.slot_id in c.COLLECTOR_SLOT_IDS),
        )
    primary_section_id = min(
        section_rank,
        key=lambda section_id: (
            -section_rank[section_id][0],
            -section_rank[section_id][1],
            -section_rank[section_id][2],
            -section_rank[section_id][3],
            -section_rank[section_id][4],
            section_declaration_index[section_id],
        ),
    )

    scored.sort(key=lambda item: (
        -item[3].score_millis,
        0 if item[3].slot_id in c.COLLECTOR_SLOT_IDS else 1,
        item[0],
    ))
    return tuple(
        score
        for _index, _hit_count, _heading_matched, score in scored
        if score.section_id == primary_section_id
    )
