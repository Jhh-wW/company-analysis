"""사람이 적은 별명으로 공식 목록에서 아무것도 못 찾을 때만 도는 보조 단계.

«배민»·«토스»처럼 널리 쓰이는 브랜드명은 공시 등록 상호와 글자가 하나도 겹치지
않는다. 결정적 규칙(정확·공백·법인접미사·약어·토큰·오타)은 글자가 겹쳐야 걸리므로
이런 이름은 후보를 한 건도 만들지 못한다. 이 모듈은 그 «한 건도 못 찾은» 경우에만
AI에 정식 법인명 후보를 묻고, 받은 이름으로 **로컬 색인을 다시 찾는다**.

지키는 경계
- AI는 이름을 «번역»만 한다. 회사를 확정하지 않으며 후보 목록에 더할 뿐이다.
- 재검색으로 들어온 후보도 사람이 고르는 절차와 `/confirm` 계약을 그대로 거친다.
- AI가 실패하거나 이상한 답을 주면 결정적 결과를 그대로 쓴다(fail-open).
- 프롬프트에 실리는 것은 사용자가 적은 회사 이름과 주소 힌트뿐이다. 개인정보도,
  회사별 사전도 담지 않는다.
- 회사 이름을 코드·프롬프트에 박지 않는다. 형식 예시만 보여 준다.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence

from src.features.business_candidate.constants import (
    AI_ALIAS_MAX_NAMES,
    ALIAS_RESPONSE_KEY,
    ALIAS_STRONG_MATCH_KINDS,
    CANDIDATE_AI_ALIAS_ENV_NAME,
    CANDIDATE_AI_ALIAS_ENV_ON,
    MAX_ADDRESS_CHARS,
    MAX_NAME_CHARS,
)


AliasAsk = Callable[[str], str]

#: AI가 준 정식명으로 후보를 실제로 더했다.
ALIAS_STATUS_APPLIED = "applied"
#: 결정적 규칙이 이미 강한 후보를 찾아 AI를 부르지 않았다.
ALIAS_STATUS_NOT_NEEDED = "not_needed"
#: 응답을 엄격 규칙으로 읽지 못했다. 결정적 결과를 그대로 쓴다.
ALIAS_STATUS_INVALID_RESPONSE = "invalid_response"
#: 예외·시간초과. 결정적 결과를 그대로 쓴다.
ALIAS_STATUS_FAILED = "failed"
#: 비용 승인을 못 받아 AI를 아예 부르지 않았다. 후보 검색 자체는 정상이다.
ALIAS_STATUS_SKIPPED_BUDGET = "skipped_budget"
#: AI가 이름을 주지 않았거나(빈 목록), 준 이름이 공식 목록에 없었다.
ALIAS_STATUS_NO_MATCH = "no_match"

#: 관측 칸에 실릴 수 있는 값의 전부. 어댑터가 다른 문자열을 보고해도 받지 않는다.
ALIAS_STATUS_VALUES: frozenset[str] = frozenset(
    {
        ALIAS_STATUS_APPLIED,
        ALIAS_STATUS_NOT_NEEDED,
        ALIAS_STATUS_INVALID_RESPONSE,
        ALIAS_STATUS_FAILED,
        ALIAS_STATUS_SKIPPED_BUDGET,
        ALIAS_STATUS_NO_MATCH,
    }
)

_HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
# 스킴이 붙은 주소, 스킴 없이 도메인만 적은 주소, 사용자·비밀번호가 낀 주소를 모두 막는다.
_URL_LIKE_RE = re.compile(
    r"(?:[a-z][a-z0-9+.\-]*://)|(?:\bwww\.)|(?:\b[a-z0-9\-]+\.(?:com|net|org|kr|co\.kr|io|ai)\b)",
    re.IGNORECASE,
)


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


def ai_alias_enabled() -> bool:
    """이 프로세스에서 AI 정식명 번역을 써도 되는가.

    기본은 켜짐이다. 변수가 없으면 켜고, 정확히 ``"1"``도 켠다. 그 밖의 값은 설정
    실수로 보고 모두 끈다 — 돈이 드는 경로라 애매한 값에 열지 않는다. AI 보조
    재정렬 스위치(`ai_rerank.ai_rerank_enabled`)와 «같은» 판정 규칙이다.
    """

    raw = os.environ.get(CANDIDATE_AI_ALIAS_ENV_NAME)
    if raw is None:
        return True
    return raw.strip() == CANDIDATE_AI_ALIAS_ENV_ON


def should_ask_alias(match_kinds: Iterable[object], *, query: object) -> bool:
    """결정적 검색이 «이름이 실제로 겹치는» 후보를 못 찾았을 때만 참이다.

    Args:
        match_kinds: 결정적 검색이 만든 후보들의 `match_kind` 값.
        query: 사용자가 적은 회사 이름.

    Returns:
        AI에 정식명을 물어볼지 여부.

    강한 종류(정확·공백·법인접미사)가 하나라도 있으면 이름이 이미 겹친 것이라
    번역이 필요 없다. 그 밖에는 한글이 든 질의이거나 후보가 아예 0건일 때만 묻는다.
    영문만 적은 질의(약어)는 약어 경로가 맡으므로, 후보가 나왔다면 부르지 않는다.
    """

    kinds = tuple(str(kind or "") for kind in match_kinds)
    if any(kind in ALIAS_STRONG_MATCH_KINDS for kind in kinds):
        return False
    if not kinds:
        return True
    return bool(_HANGUL_RE.search(str(query or "")))


def build_alias_prompt(*, query: object, address_hint: object = "") -> str:
    """사용자가 적은 이름·주소 힌트만 담은 strict JSON 번역 프롬프트를 만든다.

    회사 이름 예시는 넣지 않는다. 예시를 박으면 그 회사에만 맞는 사전이 되고,
    프롬프트가 특정 기업에 치우친다.
    """

    safe_query = str(query or "")[:MAX_NAME_CHARS]
    safe_hint = str(address_hint or "")[:MAX_ADDRESS_CHARS]
    return (
        "아래는 사용자가 적은 회사 이름입니다. 별명·약칭·브랜드명일 수 있습니다.\n"
        "이 회사가 한국 전자공시(DART)에 등록했을 «정식 법인명» 후보를 확신이 높은 "
        f"순서로 최대 {AI_ALIAS_MAX_NAMES}개 적으세요.\n"
        "모르면 빈 목록을 주세요. 지어내지 마세요.\n"
        "응답은 설명이나 마크다운 없이 다음 꼴의 JSON 객체 하나만 반환하세요: "
        f'{{"{ALIAS_RESPONSE_KEY}":["<정식 법인명>","<정식 법인명>"]}}\n'
        "주소·URL·설명·회사 소개를 쓰지 마세요.\n"
        "사용자가 적은 회사 이름: "
        + json.dumps(safe_query, ensure_ascii=False)
        + "\n사용자가 적은 주소 힌트: "
        + json.dumps(safe_hint, ensure_ascii=False)
    )


def _clean_alias_name(value: object) -> str | None:
    """이름 하나를 정리한다. 규칙을 어기면 ``None``으로 응답 전체를 버리게 한다."""

    if isinstance(value, bool) or not isinstance(value, str):
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        # 제어문자는 로그·화면·재검색 질의를 조용히 망가뜨린다.
        return None
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in normalized):
        return None
    if _URL_LIKE_RE.search(normalized):
        # 번역 결과는 상호여야 한다. 주소를 받으면 그대로 재검색 질의가 된다.
        return None
    cleaned = re.sub(r"\s+", " ", normalized).strip()
    if not cleaned or len(cleaned) > MAX_NAME_CHARS:
        return None
    return cleaned


def parse_alias_names(text: object) -> tuple[str, ...] | None:
    """``{"정식명":[...]}`` 하나만 받아들이고 나머지는 전부 거절한다.

    Args:
        text: 모델 응답 문자열.

    Returns:
        정리된 이름 튜플(빈 튜플 포함). 규칙을 하나라도 어기면 ``None``.

    부분 수용을 하지 않는다. 이름 하나가 규칙을 어기면 응답 전체를 버린다 — 남은
    이름만 골라 쓰면 «어디까지 검증됐는지»를 아무도 말할 수 없게 된다.
    """

    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict) or set(payload) != {ALIAS_RESPONSE_KEY}:
        return None
    raw_names = payload.get(ALIAS_RESPONSE_KEY)
    if not isinstance(raw_names, list) or len(raw_names) > AI_ALIAS_MAX_NAMES:
        return None
    names: list[str] = []
    seen: set[str] = set()
    for item in raw_names:
        cleaned = _clean_alias_name(item)
        if cleaned is None:
            return None
        key = cleaned.casefold()
        if key in seen:
            # 같은 이름을 두 번 받으면 색인을 두 번 뒤질 이유가 없다.
            continue
        seen.add(key)
        names.append(cleaned)
    return tuple(names)


def alias_names_from_ask(
    *,
    query: object,
    address_hint: object,
    ask: AliasAsk | None,
    match_kinds: Sequence[object] = (),
) -> tuple[tuple[str, ...], str]:
    """발동 조건을 확인하고, 필요할 때만 «한 번» 물어 이름 목록을 돌려준다.

    Args:
        query: 사용자가 적은 회사 이름.
        address_hint: 사용자가 적은 주소 힌트.
        ask: 프롬프트 하나를 보내고 문자열 응답을 받는 함수. None이면 부르지 않는다.
        match_kinds: 결정적 검색이 만든 후보들의 `match_kind` 값.

    Returns:
        (정식명 후보들, 상태 문자열). 상태는 이 모듈의 ``ALIAS_STATUS_*`` 중 하나다.
        이름이 비어 있으면 호출부는 결정적 결과를 그대로 쓴다.
    """

    if ask is None or not should_ask_alias(match_kinds, query=query):
        return (), ALIAS_STATUS_NOT_NEEDED
    try:
        response = ask(build_alias_prompt(query=query, address_hint=address_hint))
    except Exception:  # noqa: BLE001 — 응답 본문·예외 메시지는 남기지 않는다
        # 취소·중단(BaseException)은 삼키지 않는다. 그건 상위가 알아야 할 신호다.
        return (), ALIAS_STATUS_FAILED
    names = parse_alias_names(response)
    if names is None:
        return (), ALIAS_STATUS_INVALID_RESPONSE
    if not names:
        return (), ALIAS_STATUS_NO_MATCH
    return names, ALIAS_STATUS_APPLIED
