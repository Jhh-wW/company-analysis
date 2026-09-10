"""결정적 순위가 갈리지 않을 때만 후보 순서를 다시 매기는 보조 단계.

약어가 든 회사 이름은 같은 점수의 후보를 수십 개 만든다. 그때 화면에 보이는
상위 세 개는 이름·주소의 사전 순서로만 정해져, 정답이 뒤로 밀릴 수 있다. 이
모듈은 그 «갈리지 않는» 경우에만 AI에 순서를 물어 화면 순서를 다시 잡는다.

지키는 경계
- 후보를 더하거나 빼지 않는다. 순서만 바꾼다.
- 어떤 경우에도 회사를 확정하지 않는다. 사람이 고르는 절차는 그대로다.
- AI가 실패하거나 이상한 답을 주면 결정적 정렬 순서를 그대로 쓴다(fail-open).
- 프롬프트에 실리는 것은 사용자가 적은 회사 이름·주소 힌트와 후보의 공개
  필드(법인명·영문명·상장코드·주소)뿐이다. 개인정보는 담지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from src.features.business_candidate.constants import (
    AI_RERANK_AMBIGUITY_MARGIN,
    AI_RERANK_MAX_CANDIDATES,
    AI_RERANK_MIN_TIE,
    AI_RERANK_TIE_EPSILON,
    CANDIDATE_AI_RERANK_ENV_NAME,
    CANDIDATE_AI_RERANK_ENV_ON,
    MAX_CANDIDATES,
    RERANK_EXEMPT_TOP_KINDS,
)
from src.features.business_candidate.address_constants import ADDRESS_DISTRICT_STRENGTH
from src.features.business_candidate.address_match import address_match_strength

if TYPE_CHECKING:  # pragma: no cover - 순환 import를 피하려고 형만 빌린다
    from src.features.business_candidate.logic import BusinessCandidate


logger = logging.getLogger(__name__)

RerankAsk = Callable[[str], str]

#: AI가 준 순서를 실제로 적용했다(같은 순서를 확인해 준 경우도 포함).
RERANK_STATUS_APPLIED = "applied"
#: 결정적 규칙으로 이미 갈려서 AI를 부르지 않았다.
RERANK_STATUS_NO_TIE = "no_tie"
#: 응답을 엄격 규칙으로 읽지 못했다. 원래 순서를 쓴다.
RERANK_STATUS_INVALID = "invalid_response"
#: 예외·시간초과·worker 부족. 원래 순서를 쓴다.
RERANK_STATUS_FAILED = "failed"
#: 비용 승인을 못 받아 AI를 아예 부르지 않았다. 후보 검색 자체는 정상이다.
RERANK_STATUS_SKIPPED_BUDGET = "skipped_budget"


class _DuplicateJsonKey(ValueError):
    """같은 JSON 키가 뒤 값을 덮어쓰는 응답을 거절한다."""


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError


def ai_rerank_enabled() -> bool:
    """이 프로세스에서 AI 보조 재정렬을 써도 되는가.

    기본은 켜짐이다. 변수가 없으면 켜고, 정확히 ``"1"``도 켠다. 그 밖의 값은
    설정 실수로 보고 모두 끈다 — 돈이 드는 경로라 애매한 값에 열지 않는다.
    """

    raw = os.environ.get(CANDIDATE_AI_RERANK_ENV_NAME)
    if raw is None:
        return True
    return raw.strip() == CANDIDATE_AI_RERANK_ENV_ON


def should_rerank(ranked: Sequence["BusinessCandidate"], *, address_hint: str = "") -> bool:
    """화면 밖 공식 후보와의 차이가 불분명할 때 보완 순서를 묻는다."""

    candidates = tuple(ranked)
    if len(candidates) <= MAX_CANDIDATES:
        # 전부 보여 줄 수 있으면 순서를 바꿔도 사람이 보는 목록은 같다.
        return False
    if candidates[0].name_match_kind in RERANK_EXEMPT_TOP_KINDS:
        # 1위 근거가 «적은 이름이 그대로 맞았다»면 순서를 다시 물을 이유가 없다.
        # 뒤에 부분 일치 후보가 몇 개 딸려 오든 사람이 고를 1위는 이미 정해져 있다.
        return False
    if any(candidate.name_match_kind == "exact_id" for candidate in candidates):
        return False
    strengths = [address_match_strength(address_hint, candidate.address) for candidate in candidates]
    if strengths[0] >= ADDRESS_DISTRICT_STRENGTH and strengths[0] > max(strengths[1:]):
        return False
    top_score = float(candidates[0].score)
    tied = sum(
        1
        for candidate in candidates
        if abs(float(candidate.score) - top_score) <= AI_RERANK_TIE_EPSILON
    )
    if tied >= AI_RERANK_MIN_TIE:
        return True
    return (
        candidates[0].provider_name == "DART"
        and bool(candidates[0].name_match_kind)
        and top_score - float(candidates[MAX_CANDIDATES].score) <= AI_RERANK_AMBIGUITY_MARGIN
    )


def build_rerank_prompt(
    *,
    query: str,
    address_hint: str,
    candidates: Sequence["BusinessCandidate"],
) -> str:
    """후보의 공개 필드만 담은 strict JSON 재정렬 프롬프트를 만든다."""

    limited = tuple(candidates)[:AI_RERANK_MAX_CANDIDATES]
    payload: list[dict[str, object]] = []
    for index, candidate in enumerate(limited):
        entry: dict[str, object] = {
            "i": index,
            "한글명": candidate.candidate_name,
            "영문명": candidate.english_name,
            "상장코드": candidate.stock_code,
        }
        if candidate.address:
            entry["주소"] = candidate.address
        payload.append(entry)
    return (
        "아래 후보 회사 목록에서, 사용자가 적은 회사 이름(과 주소 힌트)에 가장 "
        "맞는 후보부터 순서대로 i 값을 나열하세요.\n"
        "응답은 설명이나 마크다운 없이 다음 꼴의 JSON 객체 하나만 반환하세요: "
        '{"order":[0,2,1]}\n'
        "확신이 없으면 order를 빈 목록으로 두세요. 목록에 없는 회사를 만들지 말고, "
        "설명·추측·회사 소개를 쓰지 마세요.\n"
        "사용자가 적은 회사 이름: "
        + json.dumps(query, ensure_ascii=False)
        + "\n사용자가 적은 주소 힌트: "
        + json.dumps(address_hint, ensure_ascii=False)
        + "\n후보:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def parse_rerank_order(text: object, count: int) -> tuple[int, ...] | None:
    """``{"order":[...]}`` 하나만 받아들이고 나머지는 전부 거절한다.

    부분 목록은 허용한다(AI가 앞쪽 몇 개만 확신하는 경우). 범위 밖·중복·정수가
    아닌 값이 하나라도 있으면 응답 전체를 버린다.
    """

    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"order"}:
        return None
    raw_order = payload.get("order")
    if not isinstance(raw_order, list) or len(raw_order) > count:
        return None
    seen: set[int] = set()
    order: list[int] = []
    for item in raw_order:
        # bool은 int의 하위형이라 따로 막지 않으면 True가 1로 통과한다.
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        if item < 0 or item >= count or item in seen:
            return None
        seen.add(item)
        order.append(item)
    return tuple(order)


def apply_rerank(
    ranked: Sequence["BusinessCandidate"], order: Sequence[int]
) -> list["BusinessCandidate"]:
    """``order``에 든 후보를 그 순서로 앞에, 나머지는 원래 순서로 뒤에 둔다.

    후보를 더하지도 빼지도 않는다. 이 집합 불변이 깨지면 사용자가 고를 수 있는
    회사가 조용히 사라지므로 시험으로 못 박는다.
    """

    picked = tuple(order)
    chosen = [ranked[index] for index in picked]
    used = set(picked)
    rest = [
        candidate
        for index, candidate in enumerate(ranked)
        if index not in used
    ]
    return chosen + rest


def rerank_candidates(
    ranked: Sequence["BusinessCandidate"],
    *,
    query: str,
    address_hint: str,
    ask: RerankAsk | None,
) -> tuple[list["BusinessCandidate"], str]:
    """공식 후보 순위가 불분명하면 AI에 묻고, 실패하면 원래 순서를 쓴다.

    Args:
        ranked: 결정적 규칙으로 이미 정렬된 후보들.
        query: 사용자가 적은 회사 이름(정제된 값).
        address_hint: 사용자가 적은 주소 힌트(정제된 값).
        ask: 프롬프트 하나를 보내고 문자열 응답을 받는 함수. None이면 부르지 않는다.

    Returns:
        (후보 목록, 상태 문자열). 상태는 이 모듈의 ``RERANK_STATUS_*`` 중 하나다.
    """

    original = list(ranked)
    if ask is None or not should_rerank(original, address_hint=address_hint):
        return original, RERANK_STATUS_NO_TIE

    limited = original[:AI_RERANK_MAX_CANDIDATES]
    tail = original[AI_RERANK_MAX_CANDIDATES:]
    try:
        prompt = build_rerank_prompt(
            query=query, address_hint=address_hint, candidates=limited
        )
        response = ask(prompt)
    except Exception as error:  # noqa: BLE001 — 응답 본문·예외 메시지는 남기지 않는다
        # 취소·중단(BaseException)은 삼키지 않는다. 그건 상위가 알아야 할 신호다.
        logger.warning("회사 후보 AI 재정렬 실패 kind=%s", type(error).__name__)
        return original, RERANK_STATUS_FAILED

    order = parse_rerank_order(response, len(limited))
    if order is None:
        return original, RERANK_STATUS_INVALID
    return apply_rerank(limited, order) + tail, RERANK_STATUS_APPLIED
