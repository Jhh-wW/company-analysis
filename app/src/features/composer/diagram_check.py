"""도식 검증 — 그림이 근거보다 앞서 나가지 못하게 막는다.

★ 왜 필요한가 (도식 적대 검증 실측) — 도식 10개를 독립 검증했더니 결함이
  **수치 도식 0건 / 관계 도식 7건**으로 완전히 갈렸다. 이유는 분명하다:
  수치 도식은 틀리면 숫자가 안 맞아 걸리는데, **관계 도식은 틀려도 아무도
  안 걸린다.** 실제로 잡힌 것 중 하나는 원문에 없는 관계를 그린 것이었다 —
  제조를 돕는 기술 파트너에서 「고객」으로 화살표를 그었는데, 원문은
  "운영 효율성·제조 경쟁력 강화"라고만 했다.

════════════════════════════════════════════════════════════
★ 첫 판(v2-19)은 «글자 겹침»으로 이 일을 하려다 실패했다 — 실측 기록
════════════════════════════════════════════════════════════

  칸마다 「인용 원문과 글자 3-그램이 절반 이상 겹치는가」를 물었다.
  하이브 실제 실행에서 **작가가 낸 경로 5줄을 전부 버렸다.** 본문에
  「공연 부문은 티켓 판매 또는 제3자 공연·방송 출연을 통해 수익을
  창출하는데, 공연이 실제로 개최되는 시점에」가 있는데도, 경로
  「공연 티켓·출연 기회 → 공연 기획·개최 → 공연 관람객」의 점수는 **0.00**이었다.

  원인: 3-그램을 만들 때 띄어쓰기를 지우므로 "공연티켓"과 "공연부문"이
  앞 두 글자가 같아도 서로 다른 3-그램이 된다. **문장끼리 비교(dedupe)에는
  맞지만, 짧은 딱지를 긴 문장에 대보는 일에는 못 쓰는 도구**였다.

  더 근본적으로 — 흐름도의 첫 칸(무엇으로 시작하나)과 끝 칸(누구에게 닿나)은
  **원래 작가가 요약해 붙이는 이름**이다. 원문에 「음악 소비자」·「데뷔
  아티스트」가 글자 그대로 있을 리 없다. 글자 일치를 요구하는 것은
  흐름도라는 물건의 성질과 어긋난다.

════════════════════════════════════════════════════════════
★ 그래서 지금은 «기계가 확실히 아는 것»과 «AI가 판단할 것»을 나눈다
════════════════════════════════════════════════════════════

  ① 숫자 검사 (기계, AI 0회) — 칸 안의 숫자는 인용 원문에 그대로 있어야
     한다. 지어낸 수치는 이 방법으로 확실히 걸린다("글로벌 고객 414만대").
     문장 검증(`verify._machine_check` ③④)과 같은 원칙이다.

  ② 의미 검수 (AI 1회) — 「이 경로가 인용 원문에 근거하는가」를 묻는다.
     관계가 맞는지는 글자로 알 수 없다. 이 엔진이 문장에 대해 이미
     하고 있는 일(`verify._semantic_review`)을 도식에도 똑같이 한다.

★ 검수를 «못 했을» 때는 줄을 뺀다? — 아니다, 남긴다.
  `verify.py`가 검수 불능일 때 문장을 제거하지 않고 해석으로 강등하는 것과
  같은 원칙이다: **확인 못 한 것과 거짓인 것은 다르다.** 각 줄은 이미
  근거 조각 인용이 필수이고 그 조각의 실존은 확인됐다. 검수기가 죽었다는
  이유로 그림을 지우면, 고장이 곧 「자료 없음」으로 위장된다.

★ 닫힌 목록 게이트가 아니다 (01_원칙과_금지.md).
  - 어휘 목록·업종 목록·관계 종류 목록을 만들지 않는다.
  - 숫자 검사는 자릿수 비교일 뿐 «내용의 좋고 나쁨»을 판단하지 않는다.
  - 문장을 거절하지 않는다. 근거 없는 «줄»만 뺀다. 줄이 다 빠지면 도식을
    안 그릴 뿐, 장은 그대로 남는다.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Callable, Final, Optional

from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    FlowRow,
)

logger = logging.getLogger(__name__)

#: 검수 프롬프트 머리말. 시험이 «글자를 베끼지 않고» 이 상수를 그대로 쓴다.
FLOW_REVIEW_PROMPT_HEADER: Final[str] = "[도식 검수]"

#: 검수 응답에서 읽는 키. 문장 검수(verify.py)와 같은 말을 쓴다 — 두 곳이
#: 다른 낱말을 쓰면 프롬프트를 고칠 때 한쪽만 고치는 사고가 난다.
_VERDICT_KEY: Final[str] = "판정"
_VERDICT_NUMBER_KEY: Final[str] = "번호"
_VERDICT_RESULT_KEY: Final[str] = "결과"
VERDICT_TRUE: Final[str] = "참"
VERDICT_FALSE: Final[str] = "거짓"

#: 숫자로 볼 글자. 천 단위 쉼표는 표기 차이라 지우지만 «소수점은 남긴다».
#:
#: ★ 적대 검토가 잡은 결함 — 처음엔 소수점도 지웠다. 그러면 「1.5조원」과
#:   「15개국」이 같은 열쇠 «15»가 되어, 원문에 15가 있으면 지어낸 1.5가
#:   통과한다. 자릿수를 세는 검사에서 소수점은 표기가 아니라 «값»이다.
_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"\d[\d,.]*")
_THOUSANDS_RE: Final[re.Pattern[str]] = re.compile(r",")


def _digit_keys(text: str) -> list[str]:
    """글에서 수를 뽑아 비교용 열쇠로 바꾼다 («1,389» → «1389», «1.5» → «1.5»)."""
    keys: list[str] = []
    for chunk in _DIGITS_RE.findall(text or ""):
        cleaned = _THOUSANDS_RE.sub("", chunk).rstrip(".")
        if not cleaned:
            continue
        # 앞의 0은 표기 차이다 («007» == «7»). 소수점 아래는 그대로 둔다.
        whole, _dot, frac = cleaned.partition(".")
        whole = whole.lstrip("0") or "0"
        keys.append(f"{whole}.{frac}" if frac else whole)
    return keys


def _numbers_are_grounded(cell: str, source_text: str) -> Optional[str]:
    """칸 안의 숫자가 인용 원문에 있는가. 없으면 그 숫자를 돌려준다.

    ★ 자릿수만 본다 — 단위(억원/원)나 표기(1,389 / 1389)는 보지 않는다.
      단위 환산까지 여기서 다시 하면 문장 검증과 «두 벌»이 되어 서로
      어긋난다. 도식에서 위험한 것은 「원문에 없는 수를 지어낸 것」이므로
      존재 여부만으로 충분하다.
    """
    if not (source_text or "").strip():
        # ★ 적대 검토가 잡은 결함 — 근거 원문을 못 찾았을 때(인용 id가 조각과
        #   안 맞는 등) «전부 지어낸 수»로 취급해 줄을 지우고 있었다.
        #   문장 쪽(verify._machine_check)은 같은 상황에서 제거가 아니라
        #   강등에 그친다. 대조할 것이 없는 것과 틀린 것은 다르다.
        return None
    source_keys = set(_digit_keys(source_text))
    for key in _digit_keys(cell):
        if key not in source_keys:
            return key
    return None


def _fragment_texts(fragments: Sequence[CollectedFragment]) -> dict[str, str]:
    return {str(fragment.fragment_id): fragment.text for fragment in fragments}


def _source_text(row: FlowRow, texts: Mapping[str, str]) -> str:
    return " ".join(
        texts.get(str(citation).strip(), "") for citation in row.citations
    )


# ══════════════════════════════════════════════════════════
# ① 숫자 검사 (기계, AI 0회)
# ══════════════════════════════════════════════════════════


def _drop_invented_numbers(
    rows: Sequence[FlowRow], texts: Mapping[str, str]
) -> tuple[tuple[FlowRow, ...], list[str]]:
    kept: list[FlowRow] = []
    dropped: list[str] = []
    for row in rows:
        source_text = _source_text(row, texts)
        invented: list[str] = []
        for cell in row.cells:
            missing = _numbers_are_grounded(cell, source_text)
            if missing is not None:
                invented.append(f"「{cell}」의 수 {missing}")
        if invented:
            dropped.append(
                "경로 «"
                + " → ".join(row.cells)
                + "»: 인용 원문에 없는 수 — "
                + ", ".join(invented)
            )
            continue
        kept.append(row)
    return tuple(kept), dropped


# ══════════════════════════════════════════════════════════
# ② 의미 검수 (AI 1회 — 보고서 전체 경로를 한 묶음으로)
# ══════════════════════════════════════════════════════════


def _review_prompt(items: Sequence[tuple[int, FlowRow, str]]) -> str:
    lines = [
        FLOW_REVIEW_PROMPT_HEADER,
        "아래는 보고서에 실릴 «사업 경로 도식»의 각 줄이다.",
        "한 줄은 «무엇으로 시작하나 → 회사가 하는 일 → 누구에게 닿나»를 뜻한다.",
        "",
        "줄마다 그 줄이 인용한 근거 원문을 함께 준다.",
        "판정 기준은 하나다 — **근거 원문이 이 경로를 실제로 뒷받침하는가.**",
        "",
        "★ 낱말이 원문과 «글자 그대로» 같을 필요는 없다. 첫 칸과 끝 칸은",
        "  원래 요약해 붙이는 이름이다(원문 「음반 유통은 A사와 협력」 →",
        "  칸 「음악 소비자」는 «참»이다). 글자가 아니라 «관계»를 보라.",
        "★ 원문이 말하지 않은 상대에게 화살표를 그은 줄만 «거짓»이다",
        "  (원문은 제조를 돕는 기술 협력이라고만 했는데 「고객」에게 닿는다고",
        "  그린 경우).",
        "",
        "형식: 설명 없이 아래 JSON만 출력한다.",
        '{"' + _VERDICT_KEY + '": [{"' + _VERDICT_NUMBER_KEY + '": 1, "'
        + _VERDICT_RESULT_KEY + '": "' + VERDICT_TRUE + '"}]}',
        "",
    ]
    for number, row, source_text in items:
        lines.append(f"[{number}] 경로: " + " → ".join(row.cells))
        lines.append(f"    근거 원문: {source_text}")
    return "\n".join(lines)


def _parse_verdicts(raw: str) -> dict[int, str]:
    """검수 응답을 «번호 → 결과»로 읽는다. 못 읽으면 빈 사전(=검수 불능)."""
    if not raw:
        return {}
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    items = payload.get(_VERDICT_KEY)
    if not isinstance(items, list):
        return {}
    verdicts: dict[int, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        number = item.get(_VERDICT_NUMBER_KEY)
        result = item.get(_VERDICT_RESULT_KEY)
        # ★ 파이썬에서 True는 int다 — 막지 않으면 «"번호": true»가 1번 줄로
        #   읽혀 엉뚱한 경로가 지워진다. verify.py도 같은 함정을 막는다.
        if (
            isinstance(number, int)
            and not isinstance(number, bool)
            and isinstance(result, str)
        ):
            verdicts[number] = result.strip()
    return verdicts


def _review_rows(
    by_section: Sequence[tuple[str, tuple[FlowRow, ...]]],
    texts: Mapping[str, str],
    ask: Callable[[str], str],
) -> tuple[dict[str, tuple[FlowRow, ...]], list[str]]:
    """모든 장의 경로를 «한 묶음»으로 검수한다 (AI 1회)."""
    items: list[tuple[int, FlowRow, str]] = []
    owner: dict[int, str] = {}
    number = 0
    for section_id, rows in by_section:
        for row in rows:
            number += 1
            items.append((number, row, _source_text(row, texts)))
            owner[number] = section_id
    if not items:
        return {section_id: rows for section_id, rows in by_section}, []

    try:
        raw = ask(_review_prompt(items))
    except Exception:  # noqa: BLE001 — 검수 실패가 보고서를 죽이면 안 된다
        logger.exception("도식 의미 검수가 실패했습니다 — 경로를 그대로 둡니다")
        raw = ""

    verdicts = _parse_verdicts(raw)
    if not verdicts:
        # ★ 검수 불능 = 거짓이 아니다. 문장 쪽 규칙과 같게 «남긴다».
        logger.warning(
            "도식 의미 검수를 못 했습니다 — 경로 %d줄을 그대로 둡니다 "
            "(확인 못 한 것과 거짓인 것은 다르다)",
            len(items),
        )
        return {section_id: rows for section_id, rows in by_section}, []

    kept: dict[str, list[FlowRow]] = {section_id: [] for section_id, _ in by_section}
    dropped: list[str] = []
    for index, (number, row, _source) in enumerate(items):
        result = verdicts.get(number)
        section_id = owner[number]
        if result == VERDICT_FALSE:
            dropped.append(
                f"[{section_id}] 경로 «"
                + " → ".join(row.cells)
                + "»: 검수 결과 근거가 이 경로를 뒷받침하지 않음"
            )
            continue
        # 판정이 «참»이거나, 응답에 그 번호가 빠졌으면 남긴다.
        kept[section_id].append(row)
    return {section_id: tuple(rows) for section_id, rows in kept.items()}, dropped


# ══════════════════════════════════════════════════════════
# 진입 함수
# ══════════════════════════════════════════════════════════


def check_diagrams(
    report: ComposedReport,
    fragments: Sequence[CollectedFragment],
    ask: Optional[Callable[[str], str]] = None,
) -> tuple[ComposedReport, tuple[str, ...]]:
    """관계 도식의 각 줄이 근거에 맞는지 보고, 맞지 않는 줄을 뺀다.

    Args:
        report: 검증까지 끝난 보고서.
        fragments: 수집 조각 — 칸을 대조할 원문.
        ask: 검수 AI. 생략하면 숫자 검사만 한다(오프라인 시험·무과금 경로).

    Returns:
        (근거 없는 줄이 빠진 보고서, 뺀 사유 목록).
        사유 목록은 운영 기록용이다 — 원문을 담지 않는다.

    ★ 장을 지우지 않는다. 도식이 사라져도 본문 문장은 그대로다.
    """
    texts = _fragment_texts(fragments)
    problems: list[str] = []

    # ① 숫자 검사 — 지어낸 수를 먼저 걷어낸다 (AI에 보낼 것도 줄어든다)
    after_numbers: list[tuple[str, tuple[FlowRow, ...]]] = []
    for section in report.sections:
        if not section.flow_rows:
            continue
        kept, dropped = _drop_invented_numbers(section.flow_rows, texts)
        problems.extend(f"[{section.section_id}] {reason}" for reason in dropped)
        after_numbers.append((section.section_id, kept))

    # ② 의미 검수 — 관계는 글자로 알 수 없다
    if ask is not None and any(rows for _sid, rows in after_numbers):
        reviewed, dropped = _review_rows(after_numbers, texts, ask)
        problems.extend(dropped)
    else:
        reviewed = {section_id: rows for section_id, rows in after_numbers}

    if not problems:
        return report, ()

    rebuilt = tuple(
        ComposedSection(
            section_id=section.section_id,
            sentences=section.sentences,
            notice=section.notice,
            flow_rows=reviewed.get(section.section_id, section.flow_rows),
        )
        for section in report.sections
    )
    logger.info("도식 검증: 근거에 맞지 않는 경로 %d줄을 뺐습니다", len(problems))
    return (
        ComposedReport(sections=rebuilt, summary=report.summary),
        tuple(problems),
    )
