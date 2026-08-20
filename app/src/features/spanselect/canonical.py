"""새 회사분석 정본에 맞춰 원문 문장을 고르고 의미 섹션에 배치한다.

AI의 역할은 이미 수집된 문장의 번호와 배치할 섹션을 고르는 것뿐이다.
보고서에 표시할 사실 문장은 프로그램이 원문에서 복사하고, 기존 W1~W3
검사로 다시 대조한다. 경쟁사 비교는 비교사 공식 자료가 따로 수집된 경우에만
만들 수 있으므로 이 단계에서는 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from src.features.company_specificity.logic import assess_claim
from src.features.spanselect.logic import number_sentences


CANONICAL_SOURCE_SECTION_IDS: tuple[str, ...] = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
)


SECTION_GUIDES: dict[str, str] = {
    "identity": (
        "회사가 공식적으로 정의한 존재 목적·정체성·산업 내 역할. "
        "회사 소개·공시처럼 회사가 책임지는 자료만"
    ),
    "business_model": (
        "구매자·사용자·수혜자, 제공 가치, 과금 방식, 수익 경로, "
        "핵심·성장·진입 시장을 설명하는 현재 사실"
    ),
    "portfolio": (
        "현재 실제 출시·판매·운영·투자·유통 확대가 확인되는 핵심 제품·서비스·"
        "브랜드·IP·사업. 단순 계획이나 제품 목록은 제외"
    ),
    "past_changes": (
        "기준일 전 36개월 안에 이미 실행했고 결과나 상태 변화가 확인된 사건, "
        "또는 완료된 3개 사업연도의 실제 실적"
    ),
    "current_challenges": (
        "기준일에도 해결되지 않은 회사 고유 문제와 회사가 이미 시작한 대응. "
        "외부 전망·일반 업계 위험은 제외"
    ),
    "future_strategy": (
        "회사가 공식 발표했지만 아직 실행 결과가 확인되지 않은 계획·목표·조건부 일정. "
        "애널리스트 전망은 제외"
    ),
    "operations_partners": (
        "현재 반복 작동하는 내부 생산·기술·데이터·운영 체계와 외부 파트너·유통·"
        "라이선스의 확인된 역할"
    ),
    "culture": (
        "공식 채용·문화 자료가 밝힌 전사 가치 또는 조직·상황·행동 범위가 명시된 "
        "실제 업무 사례. 직무별 KPI·지원 전략은 제외"
    ),
}


CLAIM_TYPES_BY_SECTION: dict[str, frozenset[str]] = {
    "identity": frozenset({"official_identity", "operating_scope"}),
    "business_model": frozenset({"revenue_model", "customer_market"}),
    "portfolio": frozenset({"priority_product"}),
    "past_changes": frozenset({"completed_execution", "change_interpretation"}),
    "current_challenges": frozenset({"current_issue", "current_response"}),
    "future_strategy": frozenset({"future_plan"}),
    "operations_partners": frozenset({"operating_core", "partner_role"}),
    "culture": frozenset({"official_value", "work_example"}),
}

MARKET_PRIORITIES: tuple[str, ...] = ("핵심", "성장", "진입")
PRODUCT_ROLES: tuple[str, ...] = ("주력", "성장", "안정", "신규")
PRIORITY_SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "출시·운영": re.compile(r"출시|공개|도입|가동|운영|판매|생산|공급|서비스"),
    "매출·이용증가": re.compile(r"매출|판매량|출하량|이용자|가입자|증가|성장"),
    "투자·증설": re.compile(r"투자|증설|설비|공장|라인|인수|연구개발"),
    "유통·지역확대": re.compile(r"유통|판매망|채널|진출|수출|해외|지역|국가"),
    "공식우선과제": re.compile(r"전략|핵심|중점|우선|주력|집중"),
    "파트너확대": re.compile(r"파트너|제휴|협력|공동|계약"),
}


_PROMPT = """공식 근거 기반 회사분석 보고서의 사실 배치 작업이다.
문장을 새로 쓰지 말고 아래 목록의 문장 번호만 고른다.

규칙
1. 답은 section_id와 sid만 낸다. 원문에 없는 해석·원인·우위·전망을 만들지 않는다.
2. 같은 sid는 한 섹션에만 배치한다. 한 문장에 상태가 섞여 있으면 고르지 않는다.
3. 개발완료·검증·MOU·계약·납품·매출·반복매출을 서로 같은 상태로 취급하지 않는다.
4. 특정 자회사·제품·지역의 사실을 상위 회사·제품군·시장 전체로 넓히지 않는다.
5. 과거는 완료 사실, 현재는 미해결 문제와 진행 중 대응, 미래는 미실행 공식 계획이다.
6. 원문에 직접 인과가 없으면 원인·기여·영향을 뜻하는 문장을 고르지 않는다.
7. 주가·목표가·투자의견·급여·근속·복지·직무별 KPI·자기소개서·면접 조언은 제외한다.
8. 맞는 근거가 없는 섹션은 비운다. 일반론으로 채우지 않는다.
9. claim_type은 문장의 실제 역할과 일치하게 고른다. 고객·시장에는 market_priority,
   현재 중점 제품에는 product_role과 원문에서 직접 확인되는 서로 다른 priority_signals
   두 개 이상을 함께 낸다.
10. current_response는 같은 답 안의 current_issue sid를 response_to_sid로 연결한다.
    change_interpretation은 같은 답 안의 completed_execution sid를 basis_sids로 연결한다.
11. completed_execution은 원문에 직접 적힌 사건 연도 또는 날짜를 event_date로 낸다.
    공시 발표일을 사건일로 대신 쓰지 않는다.

섹션
{section_guides}

후보 문장
{candidate_lines}
"""


@dataclass(frozen=True)
class CanonicalPick:
    """검증된 원문 문장 하나와 그 의미 섹션."""

    section_id: str
    sentence: str
    fragment_id: int
    sid: str = ""
    claim_type: str = ""
    subject_label: str = ""
    market_priority: str = ""
    product_role: str = ""
    response_to_sid: str = ""
    basis_sids: tuple[str, ...] = field(default_factory=tuple)
    priority_signals: tuple[str, ...] = field(default_factory=tuple)
    event_date: str = ""


def build_prompt(candidate_lines: list[str]) -> str:
    guides = "\n".join(
        f"- {section_id}: {SECTION_GUIDES[section_id]}"
        for section_id in CANONICAL_SOURCE_SECTION_IDS
    )
    return _PROMPT.format(
        section_guides=guides,
        candidate_lines="\n".join(candidate_lines),
    )


def answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_id": {
                            "type": "string",
                            "enum": list(CANONICAL_SOURCE_SECTION_IDS),
                        },
                        "sid": {"type": "string"},
                        "claim_type": {
                            "type": "string",
                            "enum": sorted(
                                {
                                    claim_type
                                    for values in CLAIM_TYPES_BY_SECTION.values()
                                    for claim_type in values
                                }
                            ),
                        },
                        "subject_label": {"type": "string"},
                        "market_priority": {
                            "type": "string",
                            "enum": ["", *MARKET_PRIORITIES],
                        },
                        "product_role": {
                            "type": "string",
                            "enum": ["", *PRODUCT_ROLES],
                        },
                        "response_to_sid": {"type": "string"},
                        "basis_sids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        },
                        "priority_signals": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(PRIORITY_SIGNAL_PATTERNS),
                            },
                            "uniqueItems": True,
                        },
                        "event_date": {"type": "string"},
                    },
                    "required": [
                        "section_id",
                        "sid",
                        "claim_type",
                        "subject_label",
                        "market_priority",
                        "product_role",
                        "response_to_sid",
                        "basis_sids",
                        "priority_signals",
                        "event_date",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def select_canonical_spans(
    client: Any,
    frags: dict[int, dict[str, str]],
    steps: list[dict[str, Any]],
    *,
    engine: Any,
    company: str,
    model: str = "",
) -> tuple[list[CanonicalPick], list[dict[str, str]]]:
    """번호 선택→원문 복사→W1~W3 대조를 거쳐 canonical 사실을 돌려준다."""

    sent_map, candidate_lines, excluded = number_sentences(
        frags, engine.split_sentences
    )
    engine_model = getattr(engine, "MODEL", "")
    used_model = model or engine_model
    if model:
        engine.MODEL = model
    try:
        payload, usage = engine._ask(
            client,
            build_prompt(candidate_lines),
            answer_schema(),
            max_tokens=engine.GEN_MAX_TOKENS,
        )
    finally:
        if model:
            engine.MODEL = engine_model

    if isinstance(usage, dict):
        usage["model"] = used_model
    selected = list((payload or {}).get("items") or [])
    steps.append(
        {
            "step": "8_정본_사실배치",
            "usage": usage,
            "문장후보수": len(sent_map),
            "제외후보수": excluded,
            "선택수": len(selected),
        }
    )

    if not payload:
        return [], []

    draft_items: list[Any] = []
    used_sids: set[str] = set()
    rejected: list[dict[str, str]] = []
    picks_by_sid: dict[str, CanonicalPick] = {}
    for item in selected:
        section_id = str(item.get("section_id") or "")
        sid = str(item.get("sid") or "")
        claim_type = str(item.get("claim_type") or "")
        found = sent_map.get(sid)
        if section_id not in CANONICAL_SOURCE_SECTION_IDS or found is None:
            rejected.append({"sid": sid, "reason": "없는 번호 또는 섹션"})
            continue
        if sid in used_sids:
            rejected.append({"sid": sid, "reason": "같은 사실 중복 배치"})
            continue
        fragment_id, sentence = found
        if fragment_id is None:
            rejected.append({"sid": sid, "reason": "원문 조각 없음"})
            continue
        if claim_type not in CLAIM_TYPES_BY_SECTION.get(section_id, frozenset()):
            rejected.append({"sid": sid, "reason": "섹션과 claim_type 불일치"})
            continue

        subject_label = " ".join(str(item.get("subject_label") or "").split())
        if subject_label and subject_label.casefold() not in sentence.casefold():
            rejected.append({"sid": sid, "reason": "대상 이름이 원문에 없음"})
            continue
        market_priority = str(item.get("market_priority") or "")
        product_role = str(item.get("product_role") or "")
        response_to_sid = str(item.get("response_to_sid") or "")
        basis_sids = tuple(dict.fromkeys(str(value) for value in item.get("basis_sids") or [] if str(value)))
        priority_signals = tuple(
            dict.fromkeys(
                str(value)
                for value in item.get("priority_signals") or []
                if str(value) in PRIORITY_SIGNAL_PATTERNS
            )
        )
        event_date = str(item.get("event_date") or "").strip()
        if claim_type == "customer_market":
            if not subject_label or market_priority not in MARKET_PRIORITIES:
                rejected.append({"sid": sid, "reason": "고객·시장 우선순위 없음"})
                continue
        elif market_priority:
            rejected.append({"sid": sid, "reason": "고객·시장 외 우선순위 입력"})
            continue
        if claim_type == "priority_product":
            if not subject_label or product_role not in PRODUCT_ROLES:
                rejected.append({"sid": sid, "reason": "제품 포트폴리오 역할 없음"})
                continue
            supported_signals = tuple(
                signal
                for signal in priority_signals
                if PRIORITY_SIGNAL_PATTERNS[signal].search(sentence)
            )
            if len(supported_signals) < 2:
                rejected.append({"sid": sid, "reason": "원문에 중점 추진 신호 2개 미만"})
                continue
            priority_signals = supported_signals
        elif product_role or priority_signals:
            rejected.append({"sid": sid, "reason": "중점 제품 외 제품 역할·신호 입력"})
            continue
        if claim_type != "current_response" and response_to_sid:
            rejected.append({"sid": sid, "reason": "대응 외 문제 연결 입력"})
            continue
        if claim_type != "change_interpretation" and basis_sids:
            rejected.append({"sid": sid, "reason": "변화 해석 외 근거 연결 입력"})
            continue
        if claim_type == "completed_execution":
            if not re.fullmatch(r"20\d{2}(?:-\d{2}-\d{2})?", event_date):
                rejected.append({"sid": sid, "reason": "완료 실행의 사건 연도·날짜 없음"})
                continue
            event_year = event_date[:4]
            if event_year not in sentence:
                rejected.append({"sid": sid, "reason": "사건 연도·날짜가 원문에 없음"})
                continue
            if len(event_date) == 10:
                compact_sentence = re.sub(r"\s", "", sentence)
                date_variants = {
                    event_date,
                    event_date.replace("-", "."),
                    event_date.replace("-", "/"),
                    f"{event_date[:4]}년{int(event_date[5:7])}월{int(event_date[8:10])}일",
                }
                if not any(value in compact_sentence for value in date_variants):
                    rejected.append({"sid": sid, "reason": "사건 날짜가 원문에 없음"})
                    continue
        elif event_date:
            rejected.append({"sid": sid, "reason": "완료 실행 외 항목에 사건일 입력"})
            continue
        used_sids.add(sid)
        picks_by_sid[sid] = CanonicalPick(
            section_id=section_id,
            sentence=sentence,
            fragment_id=int(fragment_id),
            sid=sid,
            claim_type=claim_type,
            subject_label=subject_label,
            market_priority=market_priority,
            product_role=product_role,
            response_to_sid=response_to_sid,
            basis_sids=basis_sids,
            priority_signals=priority_signals,
            event_date=event_date,
        )

    invalid_links: set[str] = set()
    for sid, pick in picks_by_sid.items():
        if pick.claim_type == "current_response":
            target = picks_by_sid.get(pick.response_to_sid)
            if target is None or target.claim_type != "current_issue":
                rejected.append({"sid": sid, "reason": "같은 답의 미해결 문제와 대응이 연결되지 않음"})
                invalid_links.add(sid)
        if pick.claim_type == "change_interpretation":
            bases = [picks_by_sid.get(value) for value in pick.basis_sids]
            if not bases or any(
                base is None or base.claim_type != "completed_execution"
                for base in bases
            ):
                rejected.append({"sid": sid, "reason": "같은 답의 완료 실행과 변화 해석이 연결되지 않음"})
                invalid_links.add(sid)

    pick_by_draft_key: dict[tuple[int, str, str], CanonicalPick] = {}
    for sid, pick in picks_by_sid.items():
        if sid in invalid_links:
            continue
        draft_items.append(
            engine.DraftItem(
                sentence=pick.sentence,
                fragment_id=pick.fragment_id,
                block=pick.section_id,
            )
        )
        pick_by_draft_key[(pick.fragment_id, pick.sentence, pick.section_id)] = pick

    checked = engine.check_draft(
        draft_items,
        {number: str(frag.get("원문") or "") for number, frag in frags.items()},
        [],
    )
    rejected.extend(
        {"sid": "", "reason": reason}
        for _item, reason in checked.deleted
    )
    steps.append(
        {
            "step": "10_정본_원문대조",
            "유지": len(checked.kept),
            "삭제": len(checked.deleted) + len(rejected),
            "삭제사유": [item["reason"] for item in rejected[:10]],
        }
    )
    kept: list[CanonicalPick] = []
    for item in checked.kept:
        if item.fragment_id is None:
            continue
        fragment = frags.get(int(item.fragment_id), {})
        decision = assess_claim(
            str(item.block),
            str(item.sentence),
            source_kind=str(fragment.get("종류") or ""),
            company=company,
        )
        if not decision.passed:
            rejected.append(
                {
                    "sid": "",
                    "reason": decision.reason or "섹션별 사실 기준 미달",
                }
            )
            continue
        selected_pick = pick_by_draft_key.get(
            (int(item.fragment_id), str(item.sentence), str(item.block))
        )
        if selected_pick is not None:
            kept.append(selected_pick)
    return kept, rejected
