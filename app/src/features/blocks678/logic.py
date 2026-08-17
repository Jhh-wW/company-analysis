"""6·7·8번을 만드는 순수 함수.

정본: 확정/05_생성/2_규칙/01_출력틀.md · 확정/90_운영기록/01_문제로그.md P-31
     · 확정/90_운영기록/03_결정기록_03_구현중.md D14-3

★ 전부 코드다. AI 호출 0회 (확정/05_생성/2_규칙/03_프로그램이붙이는것.md 원칙 — "AI에게
  시키면 지어낸다. 그러므로 프로그램이 붙인다"). 1판은 이 세 칸을 만들지 않고 생성 프롬프트가
  요구역량을 5·6·7·8에 임의로 흩뿌렸다 (P-31). 여기서 그 자리를 처음부터 채운다.

★ 재료가 없으면 지어내지 말고 빈 칸 + 사유를 돌려준다 (W5·S6). 사유는 이 모듈이 붙인다.
★ 규칙⑤ — 5번뿐 아니라 6·7·8번도 원문을 그대로 살린다. 다듬지 않는다.
"""

from __future__ import annotations

import re

from src.core.constants import CELL_LABELS
from src.features.blocks678.constants import (
    BLOCK6_EMPTY_NO_CELL1,
    BLOCK6_EMPTY_NO_JOB,
    BLOCK7_EMPTY_REASON,
    BLOCK7_MAX_ITEMS,
    BLOCK7_PRODUCT_NAME_PATTERN,
    BLOCK8_EMPTY_NO_OVERLAP,
    BLOCK8_EMPTY_NO_REQUIREMENTS,
    BLOCK8_EMPTY_NO_SITUATION,
    BLOCK8_MAX_ROWS,
    BLOCK8_PARTICLE_SUFFIXES,
    BLOCK8_REQ_SEPARATOR,
    BLOCK8_STOPWORDS,
    BLOCK8_TABLE_CAPTION,
    BLOCK8_TABLE_CITE,
    BLOCK8_TABLE_HEADERS,
    BLOCK8_TOKEN_MIN_LEN,
    BLOCK8_TRUNCATED_NOTE,
)
from src.features.pipeline.port import ReportSection, ReportTable

_PRODUCT_NAME = re.compile(BLOCK7_PRODUCT_NAME_PATTERN)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{" + str(BLOCK8_TOKEN_MIN_LEN) + r",}")

#: 4번의 세 세부 칸. 이 순서로 8번 표의 행을 만든다 (출력 틀 정본 순서와 동일).
_SITUATION_SUBCELLS: tuple[str, str, str] = ("4-1", "4-2", "4-3")


def build_block6(cell1_lines: list[tuple[str, str]], job: str) -> ReportSection:
    """6번 「내 자리가 회사 어디에 붙나」 — 1번(사업구조)에서 이 직무의 위치.

    ★ 「이 직무는 이 구조 위에 있다」 같은 연결 문장을 새로 쓰지 않는다.
      그건 검증할 길이 없는 주장이 된다 (AI 없이 코드가 판단할 근거가 없다).
      대신 1번 문장과 직무명을 그대로 나란히 준다 — 연결은 지원자가 자소서에서
      직접 쓴다 (00_공통/2_규칙/01_도구정의.md §2 「지원동기 대필은 하지 않는다」).

    Args:
        cell1_lines: 1번 칸에 이미 채워진 (문장, 출처) 목록. 그대로 재사용한다.
        job: 지원 직무명. 공고/입력 원문 그대로 (규칙⑤).

    Returns:
        6번 섹션. 재료가 없으면 문장 없이 empty_reason만 채운 섹션.
    """
    cell = "6"
    title = CELL_LABELS[cell]
    if not job.strip():
        return ReportSection(cell=cell, title=title, empty_reason=BLOCK6_EMPTY_NO_JOB)
    if not cell1_lines:
        return ReportSection(cell=cell, title=title, empty_reason=BLOCK6_EMPTY_NO_CELL1)

    lines = [(f"지원 직무: {job}", "공고"), *cell1_lines]
    return ReportSection(cell=cell, title=title, lines=lines)


def build_block7(requirements: list[str]) -> ReportSection:
    """7번 「이 일 하면 뭐가 힘든가」 — 실제 제품·시스템 이름이 들어간 상황, 최대 2개.

    공고 요구역량(5번, 원문 그대로) 중 구체적 이름이 들어간 문장만 고른다.
    「일에 맞나」 축이라 5번에서만 재료를 찾는다 — 4번(「회사에 맞나」 축)을 섞으면
    두 축이 흐려진다 (00_공통/4_근거/02_설계원칙.md §3).

    Args:
        requirements: 공고에서 뽑은 요구역량 문장 목록. 원문 그대로.

    Returns:
        7번 섹션. 문장은 다듬지 않고 그대로 담는다(규칙⑤).
    """
    cell = "7"
    title = CELL_LABELS[cell]
    picked = [r for r in requirements if _PRODUCT_NAME.search(r)][:BLOCK7_MAX_ITEMS]
    if not picked:
        return ReportSection(cell=cell, title=title, empty_reason=BLOCK7_EMPTY_REASON)
    return ReportSection(cell=cell, title=title, lines=[(r, "공고") for r in picked])


def _is_stopword(token: str) -> bool:
    """제외 목록에 걸리는 낱말인가. 조사·어미가 붙은 꼴(「업무를」)도 걸러낸다.

    ★ 낱말을 «고치지 않는다.» 「제외 대상인가」를 물을 때만 조사를 떼어 본다.
      토큰 자체를 어간으로 바꿔 통일하면 겹침이 늘어 표가 폭주한다고 별도 실측에서
      보고됐다 (루트로닉·파마리서치에서 행이 2~3배). 여기서는 «빼기만» 하므로
      행이 늘어날 수 없다 — 안전한 방향(미달 방향)이다.

    ★ 어간 길이를 BLOCK8_TOKEN_MIN_LEN 이상으로 막는다. 이게 없으면
      「인증」이 「인」+「증」처럼 엉뚱하게 잘려 멀쩡한 낱말이 죽는다.

    Args:
        token: 낱말 하나. 원문에서 뽑은 그대로.

    Returns:
        제외해야 하면 True.
    """
    if token in BLOCK8_STOPWORDS:
        return True
    for suffix in BLOCK8_PARTICLE_SUFFIXES:
        if not token.endswith(suffix):
            continue
        stem = token[: -len(suffix)]
        if len(stem) >= BLOCK8_TOKEN_MIN_LEN and stem in BLOCK8_STOPWORDS:
            return True
    return False


def _overlap_tokens(text: str) -> set[str]:
    """겹침 판정용 낱말 집합을 만든다. 조사·흔한 업무 낱말(BLOCK8_STOPWORDS)은 뺀다."""
    return {t for t in _TOKEN.findall(text) if not _is_stopword(t)}


#: (원래 순서, (겹친 낱말, 표 한 행)) — 정렬 중에도 원래 순서를 잃지 않으려고 묶어 다닌다.
_IndexedRow = tuple[int, tuple[set[str], list[str]]]


def _row_sort_key(indexed_row: _IndexedRow) -> tuple[int, int, int]:
    """상한에 걸릴 때 «어느 행을 남길지» 정하는 순서.

    겹친 낱말이 많을수록, 동률이면 가장 긴 낱말이 길수록 앞에 둔다.
    긴 낱말을 우대하는 이유 — 「해외송금」·「전력전자」처럼 긴 낱말일수록
    그 회사에서만 나오는 말이라 근거로서 값이 크다.
    마지막 열쇠는 원래 순서(4-1→4-2→4-3)라 결과가 매번 같다.
    """
    order, (words, _row) = indexed_row
    return (-len(words), -max(len(w) for w in words), order)


def _crossed_rows(
    cell4_lines: dict[str, list[tuple[str, str]]],
    requirements: list[str],
) -> list[tuple[set[str], list[str]]]:
    """4번 문장마다 겹치는 요구역량을 모아 (겹친 낱말, 표 한 행)로 만든다.

    Args:
        cell4_lines: "4-1"/"4-2"/"4-3" → 그 칸의 (문장, 출처) 목록.
        requirements: 5번 요구역량 문장 목록.

    Returns:
        겹침이 있는 상황 문장만큼의 목록. 순서는 4-1→4-2→4-3.
    """
    req_tokens = [(req, _overlap_tokens(req)) for req in requirements]
    out: list[tuple[set[str], list[str]]] = []
    for sub in _SITUATION_SUBCELLS:
        for text, _cite in cell4_lines.get(sub, []):
            situation_tokens = _overlap_tokens(text)
            if not situation_tokens:
                continue
            # ★ 한 상황 문장에 여러 요구역량이 걸리면 «한 행으로 묶는다».
            #   짝마다 행을 만들면 같은 긴 문장이 3~4번 반복돼 표를 읽을 수 없다
            #   (실측 — 넥스트증권 3행이 전부 같은 4-3 문장이었다).
            matched: list[str] = []
            words: set[str] = set()
            for req, tokens in req_tokens:
                overlap = situation_tokens & tokens
                if overlap:
                    matched.append(req)
                    words |= overlap
            if matched:
                row = [
                    f"[{sub}] {text}",
                    "· " + BLOCK8_REQ_SEPARATOR.join(matched),
                    ", ".join(sorted(words)),
                ]
                out.append((words, row))
    return out


def build_block8(
    cell4_lines: dict[str, list[tuple[str, str]]],
    requirements: list[str],
) -> ReportSection:
    """8번 「그래서 뭘 어필하나」 — 4번×5번 교차표. ★ 문장을 쓰지 않는다 (규칙⑥).

    4-1/4-2/4-3(회사 상황) 문장과 5번(공고 요구역량) 문장을 낱말 겹침으로 대조해,
    겹치는 짝만 표의 행으로 낸다. 겹치는 낱말 자체가 「근거」이고, 그 근거로 무슨
    문장을 쓸지는 지원자의 몫이다 — "근거는 도구가, 문장은 본인이" (규칙⑥ 근거).

    ★ 행이 BLOCK8_MAX_ROWS를 넘으면 «값어치 순»으로 추린다. 잘린 개수는 표 설명에
      적는다 — 조용히 버리지 않는다.

    Args:
        cell4_lines: "4-1"/"4-2"/"4-3" → 그 칸의 (문장, 출처) 목록. 각 칸의
            `ReportSection.lines`를 그대로 넘기면 된다.
        requirements: 5번 요구역량 문장 목록. 원문 그대로.

    Returns:
        8번 섹션. 채워졌으면 `lines`는 비어 있고 `tables`에 표 하나만 있다.
        행은 최대 BLOCK8_MAX_ROWS개. 겹치는 짝이 하나도 없으면(재료는 있어도
        겹침이 없으면) 빈 칸 + 사유.
    """
    cell = "8"
    title = CELL_LABELS[cell]
    if not any(cell4_lines.get(c) for c in _SITUATION_SUBCELLS):
        return ReportSection(cell=cell, title=title, empty_reason=BLOCK8_EMPTY_NO_SITUATION)
    if not requirements:
        return ReportSection(cell=cell, title=title, empty_reason=BLOCK8_EMPTY_NO_REQUIREMENTS)

    matched_rows = _crossed_rows(cell4_lines, requirements)
    if not matched_rows:
        return ReportSection(cell=cell, title=title, empty_reason=BLOCK8_EMPTY_NO_OVERLAP)

    # ★ 상한을 걸기 전에 «남길 값어치» 순으로 세운다. 앞에서부터 자르면
    #   겹친 낱말 1개짜리가 남고 2~3개짜리가 잘리는 일이 생긴다.
    ordered = sorted(enumerate(matched_rows), key=_row_sort_key)
    rows = [row for _order, (_words, row) in ordered[:BLOCK8_MAX_ROWS]]

    caption = BLOCK8_TABLE_CAPTION
    hidden = len(matched_rows) - len(rows)
    if hidden > 0:
        # 조용히 자르지 않는다 — 몇 개를 감췄는지 사용자에게 그대로 말한다.
        caption += BLOCK8_TRUNCATED_NOTE.format(
            shown=len(rows), total=len(matched_rows), hidden=hidden
        )

    table = ReportTable(
        caption=caption,
        headers=list(BLOCK8_TABLE_HEADERS),
        rows=rows,
        cite=BLOCK8_TABLE_CITE,
    )
    if not table.is_valid:
        # 방어적 분기 — rows를 위에서 항상 3열로 만들므로 이론상 여기 오지 않는다.
        # 화면을 깨뜨리는 표를 내보내느니 안전한 빈 칸으로 내려간다.
        return ReportSection(cell=cell, title=title, empty_reason=BLOCK8_EMPTY_NO_OVERLAP)
    return ReportSection(cell=cell, title=title, tables=[table])
