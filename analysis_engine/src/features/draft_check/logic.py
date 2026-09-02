"""생성 검사 W1~W4 — 코드가 AI 출력을 대조한다 (AI 없음, 0원).

  W1 조각 번호 없음 → 삭제 · W2 없는 번호 → 삭제 · W3 원문에서 못 찾음 → 삭제
  W4 공고 블록(5·6·7·8)이 요구역량 목록과 다름 → 원문(목록)으로 되돌림
W3 재는 법: 문장에서 조사·어미를 뗀 뒤 남은 단어가 원문에 같은 순서로 있는가.
⚠️ 조사·어미 판별은 접미사 목록 방식(1차) — 형태소 분석기 채택 여부는 아직 정하지 않았다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 어미·조사 접미사 (긴 것부터 뗀다). 실측으로 계속 늘린다 — 「없음」 표현 사전과 같은 방식.
SUFFIXES = (
    "하였습니다", "했습니다", "됐습니다", "입니다", "합니다", "됩니다", "있습니다",
    "하고자", "으로써", "로써", "에서는", "에게", "에서", "으로", "이며", "하며",
    "하고", "이다", "한다", "된다", "라고", "까지", "부터", "이나", "거나",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로", "다", "원",
)
MIN_TOKEN_LEN = 2  # 접미사 제거 후 이 길이 미만이면 대조에서 제외 (조사 잔여물 방지)

POSTING_BLOCKS = frozenset({"5", "6", "7", "8"})


def strip_suffix(token: str) -> str:
    for suffix in SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)]
    return token


def content_tokens(sentence: str) -> list[str]:
    tokens = []
    for raw in sentence.split():
        t = strip_suffix(raw.strip("「」『』()[].,·\"'"))
        if len(t) >= MIN_TOKEN_LEN or t.isdigit():
            tokens.append(t)
    return tokens


def sentence_in_source(sentence: str, source: str) -> bool:
    """조사·어미를 뗀 단어들이 원문에 같은 순서로 있으면 참 — 숫자·이름을 지어내면 걸린다."""
    pos = 0
    for token in content_tokens(sentence):
        idx = source.find(token, pos)
        if idx < 0:
            return False
        pos = idx + len(token)
    return True


@dataclass(frozen=True)
class DraftItem:
    sentence: str
    fragment_id: Optional[int]
    block: str


@dataclass
class DraftCheckResult:
    kept: list[DraftItem] = field(default_factory=list)
    deleted: list[tuple[DraftItem, str]] = field(default_factory=list)  # (항목, W코드+사유)


def check_draft(items: list[DraftItem], fragments: dict[int, str],
                requirement_lines: list[str]) -> DraftCheckResult:
    """W1~W4를 한 번에 돈다. 공고 블록의 대조 기준은 요구역량 목록(원문 문장 그대로)이다."""
    result = DraftCheckResult()
    normalized_reqs = {" ".join(ln.split()) for ln in requirement_lines}
    for item in items:
        if item.block in POSTING_BLOCKS:
            if " ".join(item.sentence.split()) in normalized_reqs:
                result.kept.append(item)
            else:
                result.deleted.append((item, "W4: 공고 블록이 요구역량 목록과 다름 → 목록 문장으로 되돌림"))
            continue
        if item.fragment_id is None:
            result.deleted.append((item, "W1: 조각 번호 없음"))
            continue
        if item.fragment_id not in fragments:
            result.deleted.append((item, "W2: 실재하지 않는 조각 번호"))
            continue
        if not sentence_in_source(item.sentence, fragments[item.fragment_id]):
            result.deleted.append((item, "W3: 조각 원문에서 문장을 못 찾음"))
            continue
        result.kept.append(item)
    return result
