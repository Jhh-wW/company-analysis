"""문장 스팬 선택 — 1판 `generate_and_check`를 정본에 맞춰 감싼 것.

# 왜 만들었나

데모 15곳 중 13곳에서 **4번 칸(4-1·4-2·4-3)이 통째로 비었다.** 그 탓에 8번 교차표가
15곳 중 14곳에서 안 나온다 (문제로그 P-43).

자료가 없어서가 아니다. 뉴스에서 뽑힌 **후보 문장 106개가 AI 앞에 올라갔는데 인용은 0회**였다.
1판 프롬프트(`analysis_engine/tools/run_pilot.py:436-439`)가 4축에 대해
「회사가 직접 말한 문장만, 추론 금지」라고 지시하고, 후보 줄에는 `(뉴스)` 종류 표시가
붙어 있어(:422) 모델이 뉴스를 문면 그대로 배제한 것으로 보인다.

그런데 정본은 4-1을 **「회사가 밝혔거나 언론이 지적한 과제」**로 정했다
. 비상장 외감은 MD&A가 감사보고서에 **들어갈 자리
자체가 없어** 4축이 뉴스로 채워지는 것이 기본값이다.
→ **엔진 프롬프트가 정본을 어기고 있다.** 이 모듈이 그 지시문만 바꾼다.

# 무엇을 바꾸고 무엇을 그대로 두나

| | |
|---|---|
| 바꾼다 | AI에게 주는 지시문 · 후보 문장에서 «감사인 의견·시세 기사» 빼기 |
| 바꾼다 | 후보 문장 앞에 딸려온 «목록 순번·기사 말머리» 떼기 (P-74) |
| 그대로 | 문장 번호 매기기 · 요구역량 복원 · **W1~W4 원문 대조 검사(`check_draft`)** |
| 그대로 | AI 호출 **1회** · 단계 기록 이름 · 반환 모양 |

★ 1판 선택·대조 동작은 바꾸지 않는다. `_ask`에는 원문 없는 종료·절단 진단만
  추가하고, 나머지 부품(`split_sentences`·`check_draft`·`DraftItem`·
  `BLOCK_ORDER`·`GEN_MAX_TOKENS`)은 **빌려 쓰기만** 한다.
★ 원문 대조 검사를 새로 구현하지 않는다 — 지어내기를 막는 마지막 안전장치이고,
  1판이 53건을 돌려 검증한 코드다.
"""

from __future__ import annotations

from typing import Any, Optional

from src.core.constants import COMPANY_SOURCE_CELLS
from src.features.spanselect.constants import (
    ARTICLE_HEADER_RE,
    AUDIT_OPINION_MARKERS,
    BLOCK_CONDITIONS,
    DELETED_REASON_PREVIEW,
    GENERATION_STEP,
    LEADING_NOISE_MAX_ROUNDS,
    LIST_ORDINAL_RE,
    MARKET_PRICE_MARKERS,
    MASK_LABEL_PREFIX,
    MIN_CLEANED_SENTENCE_CHARS,
    NEWS_FRAGMENT_KIND,
    NEWS_SOURCE_MARKER_RE,
    NUMBER_UNIT_RE,
    PROMPT_BLOCK9_SOURCE,
    PROMPT_BLOCK_HEAD,
    PROMPT_COMPANY_HEADER,
    PROMPT_EXCLUDE,
    PROMPT_FRAGMENT_HEAD,
    PROMPT_HEADER,
    PROMPT_JOB_LABEL,
    PROMPT_NO_INFERENCE,
    PROMPT_PICK,
    PROMPT_POSTING,
    PROMPT_REQUIREMENT_HEAD,
    PROMPT_SITUATION_SOURCES,
    REQUIREMENT_FALLBACK_BLOCK,
    REQUIREMENT_SID_PREFIX,
    HANGUL_RE,
    NEWS_SOURCE_PREFIX_RE,
    SENTENCE_PREVIEW_CHARS,
    TRUNCATED_TAIL_RE,
    USAGE_MODEL_KEY,
    VERIFY_STEP,
    ZERO_WIDTH_CHARS,
)
from src.features.company_specificity.logic import source_kind_matches_sentence
from src.shared.official_ir import (
    IR_COLLECTED_ON_FIELD,
    verified_official_ir_fragment_is_usable,
)

#: 문장 번호표 한 칸. (조각 번호, 문장 원문) — 요구역량은 조각 번호가 없어 None이다.
SentenceMap = dict[str, tuple[Optional[int], str]]


# ══════════════════════════════════════════════════════════
# 후보 걸러내기 — 코드가 막는다 (프롬프트는 거들 뿐)
# ══════════════════════════════════════════════════════════

def is_audit_opinion(sentence: str) -> bool:
    """감사인 의견·계속기업 문구인가.

    감사인 위험 신호는 1차에 넣지 않고,
    넣더라도 4-1과 섞지 않는다. 실측(재수집-p014)에서 이 문구가 4-3에 그대로 실렸다.

    Args:
        sentence: 후보 문장 하나.

    Returns:
        표시 낱말이 하나라도 있으면 True (= 후보에서 뺀다).
    """
    return any(marker in sentence for marker in AUDIT_OPINION_MARKERS)


def is_market_price_news(kind: str, fragment_text: str) -> bool:
    """시세·증시 기사인가 — **뉴스 조각에만** 건다.

    기사 하나가 조각 하나이므로 «기사 통째로» 본다. 제목이 시세면 본문도 시세다.

    Args:
        kind: 조각 종류 (「뉴스」·「MD&A」 등).
        fragment_text: 조각 원문 전체 (뉴스는 「(날짜 보도 · 도메인) 제목. 본문」 모양).

    Returns:
        뉴스이면서 시세 낱말이 있으면 True.
    """
    if kind != NEWS_FRAGMENT_KIND:
        return False
    return any(marker in fragment_text for marker in MARKET_PRICE_MARKERS)


# ══════════════════════════════════════════════════════════
# 앞머리 껍데기 떼기 — 목록 순번 · 기사 말머리 (문제로그 P-74)
# ══════════════════════════════════════════════════════════

def _strip_invisible_head(text: str) -> Optional[str]:
    """맨 앞의 «보이지 않는 글자»(BOM 등)를 뗀다.

    Args:
        text: 앞머리를 찾을 글자.

    Returns:
        뗀 결과. 뗄 것이 없으면 None.
    """
    stripped = text.lstrip(ZERO_WIDTH_CHARS)
    return stripped if stripped != text else None


def _strip_article_header(text: str) -> Optional[str]:
    """맨 앞의 기사 말머리(「[편집자주]」·「[단독]」)를 한 번 뗀다.

    Args:
        text: 앞머리를 찾을 글자.

    Returns:
        뗀 결과. 말머리가 없거나 개인정보 마스킹 표시면 None(= 안 건드림).
    """
    matched = ARTICLE_HEADER_RE.match(text)
    if matched is None:
        return None
    if matched.group("label").startswith(MASK_LABEL_PREFIX):
        return None  # 「[삭제:이름]」은 지운 자국이다 — 떼면 지웠다는 사실이 사라진다
    return text[matched.end():].lstrip()


def _strip_list_ordinal(text: str) -> Optional[str]:
    """맨 앞의 목록 순번(「11 」·「07 」)을 한 번 뗀다.

    Args:
        text: 앞머리를 찾을 글자.

    Returns:
        뗀 결과. 순번이 아니거나 「3 년」처럼 숫자+단위를 띄어 쓴 것이면 None.
    """
    matched = LIST_ORDINAL_RE.match(text)
    if matched is None:
        return None
    rest = text[matched.end():]
    if NUMBER_UNIT_RE.match(rest):
        return None  # 순번이 아니라 「숫자 + 단위」다 (「3 년 이상 경력」)
    return rest


def strip_leading_noise(sentence: str) -> str:
    """문장 앞에 딸려온 «껍데기»를 뗀다 — 목록 순번과 기사 말머리 (P-74).

    실측으로 확인된 세 가지만 뗀다.
      ① 회사 홈페이지 보도자료 «목록»의 순번 — 「11 파마리서치, 하반기…」
      ② 기사 말머리 — 「[편집자주] 우리엔, …」·「[블루오션 동물 의료기기]②…」
      ③ 맨 앞의 보이지 않는 글자(BOM 등) — 「[주말용] ﹇BOM﹈콘서트·불꽃축제까지…」

    ★ 뉴스 조각 맨 앞의 「(날짜 보도 · 도메인)」 출처 표기는 **건너뛰고 그 뒤부터**
      본다. 그건 껍데기가 아니라 정본이 요구한 언론사 표기다.
    ★ 조각 원문은 손대지 않으므로 W3(원문 대조) 기준이 그대로다. 남은 낱말은 원문
      낱말의 «앞을 잘라낸 부분수열»이라 순서 대조를 반드시 통과한다.
    ★ 뗀 뒤가 너무 짧아지면(`MIN_CLEANED_SENTENCE_CHARS`) 그 표시가 문장 자체였다고
      보고 원문을 그대로 돌려준다.

    Args:
        sentence: 조각에서 쪼갠 문장 하나.

    Returns:
        앞머리를 뗀 문장. 뗄 것이 없으면 받은 문장 그대로(같은 객체).
    """
    marker = NEWS_SOURCE_MARKER_RE.match(sentence)
    head = sentence[: marker.end()] if marker else ""
    body = sentence[len(head):]

    cleaned = body
    for _ in range(LEADING_NOISE_MAX_ROUNDS):
        for strip_one in (
            _strip_invisible_head,
            _strip_article_header,
            _strip_list_ordinal,
        ):
            stripped = strip_one(cleaned)
            if stripped is not None:
                cleaned = stripped
                break
        else:
            break  # 세 가지 모두 뗄 것이 없다 — 끝

    if cleaned == body or len(cleaned) < MIN_CLEANED_SENTENCE_CHARS:
        return sentence
    return f"{head}{cleaned}"


def is_unusable_candidate(
    sentence: str, *, allow_verified_official_ir_english: bool = False
) -> bool:
    """이 문장을 «AI에게 보여주지도 말아야» 하는가 (문제로그 P-81).

    ★ 큰 모델은 준 것 중에서 고른다 — 쓰레기를 주면 쓰레기를 고른다.
      모델을 올린 뒤 실제로 보고서에 실린 것들:
        · 「…18개국에 수출 노선을 확보했다....」   ← 네이버가 자른 토막
        · 「Find a provider News About Us … AMPS」 ← 홈페이지 메뉴 글자

    ★ 문장을 «고치지» 않고 후보에서 뺀다 — 번호표에 없으면 AI가 부를 수 없고,
      혹시 지어내 부르면 W2(실재하지 않는 번호)로 걸린다.

    ⚠️ 좁게 잡는다. 「문장으로 안 끝나면 버린다」 같은 넓은 규칙은 홈페이지에서
      쓸 만한 문장까지 죽일 수 있는데, 1판 자료에 홈페이지 조각이 없어
      **미리 잴 수가 없다.** 재지 못한 규칙은 넣지 않는다.

    Args:
        sentence: 앞머리를 이미 다듬은 문장.

    Returns:
        후보에서 빼야 하면 True.
    """
    if TRUNCATED_TAIL_RE.search(sentence):
        return True                      # 잘린 토막 — 자소서에 그대로 못 쓴다
    # 영어뿐인 홈페이지 메뉴·버튼은 계속 버린다. 다만 날짜·보고기간·첨부·DART
    # host 결속이 모두 검증된 공식 IR PDF는 영문 본문 자체가 원문이므로 보존한다.
    return not (
        HANGUL_RE.search(sentence) or allow_verified_official_ir_english
    )


def number_sentences(
    frags: dict[int, dict[str, str]],
    split_sentences: Any,
) -> tuple[SentenceMap, list[str], int]:
    """조각을 문장으로 쪼개 번호를 붙인다 ([1] — 0원).

    ★ 번호는 «조각 번호 - 문장 순번»이라 걸러낸 문장이 있어도 **번호가 밀리지 않는다.**
      걸러낸 문장은 번호표에 아예 없으므로, AI가 그 번호를 지어내 부르면
      W2(실재하지 않는 번호)로 버려진다.

    Args:
        frags: 조각 목록 {조각번호: {"종류", "원문"}}.
        split_sentences: 1판의 문장 쪼개기 함수 (`run_pilot.split_sentences`).
            ★ 새로 구현하지 않는다 — 절단면의 미완성 꼬리를 떼는 규칙이 여기 들어 있다.

    Returns:
        (번호표, AI에게 보여줄 후보 줄, 걸러낸 문장 수).
    """
    sent_map: SentenceMap = {}
    lines: list[str] = []
    excluded = 0

    for fid, frag in frags.items():
        kind = frag.get("종류", "")
        text = frag.get("원문", "")
        allow_verified_official_ir_english = (
            kind == "공식 IR"
            and str(frag.get("후보출처검증") or "").strip()
            == "https_exact_dart_host"
            and verified_official_ir_fragment_is_usable(
                frag,
                reference_date=str(frag.get(IR_COLLECTED_ON_FIELD) or ""),
            )
        )
        sentences = split_sentences(text)
        # 1판 splitter는 마침표 없는 마지막 조각을 잘린 꼬리로 버린다. PDF의
        # 표 행·슬라이드 제목은 정상 문단이어도 마침표가 없는 경우가 있으므로,
        # DART 법인·공식 도메인·문서 날짜까지 검증된 영문 IR에 한해서만 원문
        # 문단 자체를 후보로 복구한다. 일반 웹·뉴스의 잘린 꼬리는 계속 버린다.
        source_paragraph = str(text or "").strip()
        if (
            not sentences
            and allow_verified_official_ir_english
            and len(source_paragraph) >= MIN_CLEANED_SENTENCE_CHARS
            and HANGUL_RE.search(source_paragraph) is None
            and TRUNCATED_TAIL_RE.search(source_paragraph) is None
        ):
            sentences = [source_paragraph]
        if is_market_price_news(kind, text):
            excluded += len(sentences)
            continue
        for si, sentence in enumerate(sentences, start=1):
            # 앞머리 껍데기는 «번호를 붙이기 전»에 뗀다 — 번호표에 들어간 글자가
            # 그대로 보고서에 실리므로, 여기서 안 떼면 뒤에서는 손댈 자리가 없다.
            cleaned = strip_leading_noise(NEWS_SOURCE_PREFIX_RE.sub("", sentence))
            if (
                is_audit_opinion(cleaned)
                or is_unusable_candidate(
                    cleaned,
                    allow_verified_official_ir_english=(
                        allow_verified_official_ir_english
                    ),
                )
                or not source_kind_matches_sentence(kind, cleaned)
            ):
                excluded += 1
                continue
            sid = f"{fid}-{si}"
            sent_map[sid] = (fid, cleaned)
            lines.append(f"[{sid}] ({kind}) {cleaned[:SENTENCE_PREVIEW_CHARS]}")
    return sent_map, lines, excluded


# ══════════════════════════════════════════════════════════
# 지시문
# ══════════════════════════════════════════════════════════

def build_prompt(
    job: str, candidate_lines: list[str], requirement_lines: list[str]
) -> str:
    """AI에게 줄 지시문을 만든다.

    Args:
        job: 지원 직무.
        candidate_lines: `number_sentences`가 만든 후보 줄.
        requirement_lines: 「[R-1] …」 모양의 요구역량 줄.

    Returns:
        지시문 전문. 문구는 전부 `constants.py`에서 온다 (정본 근거 주석 포함).
    """
    company_only = not job.strip() and not requirement_lines
    blocks = " ".join(
        text
        for cell, text in BLOCK_CONDITIONS.items()
        if not company_only or cell in COMPANY_SOURCE_CELLS
    )
    if company_only:
        return "\n".join(
            [
                PROMPT_COMPANY_HEADER,
                PROMPT_PICK,
                f"{PROMPT_BLOCK_HEAD} {blocks}",
                PROMPT_SITUATION_SOURCES,
                PROMPT_NO_INFERENCE,
                PROMPT_EXCLUDE,
                "",
                PROMPT_FRAGMENT_HEAD,
                "\n".join(candidate_lines),
            ]
        )
    return "\n".join(
        [
            PROMPT_HEADER,
            PROMPT_PICK,
            f"{PROMPT_BLOCK_HEAD} {blocks}",
            PROMPT_SITUATION_SOURCES,
            PROMPT_BLOCK9_SOURCE,
            PROMPT_NO_INFERENCE,
            PROMPT_EXCLUDE,
            PROMPT_POSTING,
            f"{PROMPT_JOB_LABEL}{job}",
            "",
            PROMPT_FRAGMENT_HEAD,
            "\n".join(candidate_lines),
            "",
            PROMPT_REQUIREMENT_HEAD,
            "\n".join(requirement_lines),
        ]
    )


def _answer_schema(block_order: tuple[str, ...]) -> dict[str, Any]:
    """AI 답의 모양 — {block, sid} 목록. 1판(run_pilot.py:426-431)과 같다.

    ★ 블록 목록은 엔진의 `BLOCK_ORDER`를 그대로 쓴다. 여기서 다시 적으면 두 벌이 어긋난다.
    """
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "block": {"type": "string", "enum": list(block_order)},
                        "sid": {"type": "string"},
                    },
                    "required": ["block", "sid"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


# ══════════════════════════════════════════════════════════
# 본체 — 1판 `generate_and_check`와 같은 계약
# ══════════════════════════════════════════════════════════

def select_spans(
    client: Any,
    frags: dict[int, dict[str, str]],
    requirements: list[str],
    job: str,
    steps: list[dict[str, Any]],
    *,
    engine: Any,
    model: str = "",
) -> tuple[list[Any], list[Any]]:
    """8·9 생성 + 10 검증 — AI는 번호만 고르고, 원문 복사·대조는 코드가 한다.

    1판 `run_pilot.generate_and_check(client, frags, requirements, job, steps)`와
    **자리 인자·반환 모양이 같다.** 바뀐 것은 지시문과 후보 걸러내기뿐이다.

    Args:
        client: 1판이 만든 Anthropic 클라이언트 (`engine._client()`).
        frags: 조각 목록 {조각번호: {"종류", "원문"}}.
        requirements: 5.5가 뽑은 요구역량 문장 (원문 그대로).
        job: 지원 직무.
        steps: 단계 기록. 이 함수가 2줄을 덧붙인다 (이름은 1판과 같다).
        engine: 1판 엔진 모듈. ★ 키워드로 «주입»받는다 — 이 파일이 엔진을 직접 불러오면
            돈이 나가는 길이 두 곳이 되어 추적이 안 된다. 부르는 쪽(`pipeline/real.py`)이
            이미 들고 있는 것을 그대로 넘긴다.
        model: 이 호출에만 쓸 모델 이름. 비우면 엔진 기본 모델을 그대로 쓴다.
            ★ 왜 필요한가 — 기본 모델(haiku)은 후보 60~70줄 중 2~3개만 고르고
              지시문의 금지 조항도 어긴다 (문제로그 P-43).

    Returns:
        (유지된 항목, 삭제된 항목). 1판과 같다 — 항목은 엔진의 `DraftItem`이다.
        AI가 답을 못 주면 `([], [])`.
    """
    company_only = not job.strip() and not requirements
    sent_map, lines, excluded = number_sentences(frags, engine.split_sentences)
    for ri, req in enumerate(requirements, start=1):
        sent_map[f"{REQUIREMENT_SID_PREFIX}{ri}"] = (None, req)
    req_lines = [
        f"[{REQUIREMENT_SID_PREFIX}{ri}] {req}"
        for ri, req in enumerate(requirements, start=1)
    ]

    # ★ 이 «한 번의 호출»에만 다른 모델을 쓴다 (문제로그 P-43).
    #   진짜 파이프라인의 계량 껍데기는 MODEL을 요청 로컬로 보관하고 provider
    #   경계에서 덮어쓴다. 껍데기 없는 단위 시험도 있으므로 끝나면 원래 값으로 돌린다.
    #   ⚠️ `finally`로 반드시 되돌린다. 안 되돌리면 뒤따르는 알맹이 검사까지
    #     비싼 모델로 돌아 «돈이 조용히 새는» 사고가 난다.
    engine_model = getattr(engine, "MODEL", "")
    used_model = model or engine_model
    if model:
        engine.MODEL = model
    try:
        payload, usage = engine._ask(
            client,
            build_prompt(job, lines, req_lines),
            _answer_schema(
                tuple(
                    cell
                    for cell in engine.BLOCK_ORDER
                    if not company_only or cell in COMPANY_SOURCE_CELLS
                )
            ),
            max_tokens=engine.GEN_MAX_TOKENS,
        )
    finally:
        if model:
            engine.MODEL = engine_model
    # 어느 모델로 부른 «이» 호출인지 기록에 남긴다 — 비용을 다시 계산하려면
    # 토큰 수만으로는 부족하고 모델 이름이 있어야 한다 (문제로그 P-76).
    if isinstance(usage, dict):
        usage[USAGE_MODEL_KEY] = used_model
    steps.append(
        {
            "step": GENERATION_STEP,
            "usage": usage,
            "문장후보수": len(sent_map),
            "선택수": len((payload or {}).get("items", [])),
            # 1판에 없던 칸. 「왜 후보가 줄었나」를 나중에 셀 수 있어야 한다.
            "제외후보수": excluded,
        }
    )
    if not payload:
        return [], []

    items: list[Any] = []
    bad_sids = 0
    duplicated = 0
    used_sids: set[str] = set()
    for picked in payload["items"]:
        found = sent_map.get(picked["sid"])
        if found is None:      # 실재하지 않는 번호 — W2에 해당, 버린다
            bad_sids += 1
            continue
        # ★ 같은 문장을 두 칸에 넣지 않는다 (문제로그 P-81).
        #   실측 — 「…유럽 시장 확대에 속도를 낸다」가 4-2와 4-3에 똑같이 실렸다.
        #   같은 글이 두 번 보이면 사용자는 «자료가 더 있다»고 착각한다.
        #   먼저 배정된 칸을 남긴다 — AI가 더 확신한 쪽이 앞에 온다.
        if picked["sid"] in used_sids:
            duplicated += 1
            continue
        used_sids.add(picked["sid"])
        fid, sentence = found
        items.append(
            engine.DraftItem(sentence=sentence, fragment_id=fid, block=picked["block"])
        )

    # 요구역량 완전성 — 배치 안 된 것은 5번 블록에 원문 그대로 되살린다.
    # 「조용한 누락 금지」.
    restored = 0
    for ri in range(1, len(requirements) + 1):
        sid = f"{REQUIREMENT_SID_PREFIX}{ri}"
        if sid not in used_sids:
            items.append(
                engine.DraftItem(
                    sentence=sent_map[sid][1],
                    fragment_id=None,
                    block=REQUIREMENT_FALLBACK_BLOCK,
                )
            )
            restored += 1

    # ★ W1~W4 원문 대조 — 1판 것을 그대로 돌린다. 지어낸 문장은 여기서 걸린다.
    result = engine.check_draft(
        items, {i: f["원문"] for i, f in frags.items()}, requirements
    )
    steps.append(
        {
            "step": VERIFY_STEP,
            "유지": len(result.kept),
            "삭제": len(result.deleted),
            "없는번호": bad_sids,
            "중복배정": duplicated,
            "요구역량_복원": restored,
            "삭제사유": [reason for _, reason in result.deleted][:DELETED_REASON_PREVIEW],
        }
    )
    return result.kept, result.deleted
