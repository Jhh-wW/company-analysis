"""층1 — 코드 이름 대조 (파이프라인 2번, 0원 구간).

정본: 확정/기획서/01_식별/01_흐름_01_층1_코드대조.md
변형 규칙: 숫자→한글(11번가→십일번가) · 법인격 표기 제거 · 공백/점/하이픈 제거 · 전각→반각.
걸린 후보는 **전부** 반환한다 — 동명 11.3%는 층3(주소)이 좁힌다. 여기서 고르지 않는다.
"""
from __future__ import annotations

import re
import unicodedata

# 법인격 표기 — 대조 전에 지운다 (명단·입력 양쪽 모두)
CORP_SUFFIXES = ("주식회사", "(주)", "㈜", "(유)", "유한회사", "유한책임회사", "(사)", "(재)")
STRIP_CHARS_RE = re.compile(r"[\s.\-·,]")

SINO_DIGITS = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
SINO_UNITS = ["", "십", "백", "천"]


def digits_to_sino(number: int) -> str:
    """1~9999를 한자어 읽기로 바꾼다 (11→십일, 20→이십, 111→백십일)."""
    if number == 0:
        return "영"
    parts: list[str] = []
    digits = str(number)
    length = len(digits)
    for i, ch in enumerate(digits):
        d = int(ch)
        if d == 0:
            continue
        unit = SINO_UNITS[length - 1 - i]
        # 십·백·천 앞의 1은 읽지 않는다 (11 = 십일, 일십일 아님)
        head = "" if (d == 1 and unit) else SINO_DIGITS[d]
        parts.append(head + unit)
    return "".join(parts)


def normalize_name(raw: str) -> str:
    """법인격 표기·공백류 제거 + 전각→반각 + 영문 소문자화."""
    text = unicodedata.normalize("NFKC", raw)
    for suffix in CORP_SUFFIXES:
        text = text.replace(suffix, "")
    text = STRIP_CHARS_RE.sub("", text)
    return text.lower()


def convert_digit_runs(name: str) -> str:
    """이름 속 숫자 구간을 한자어 읽기로 바꾼다 — 11번가 → 십일번가."""
    return re.sub(r"\d+", lambda m: digits_to_sino(int(m.group())), name)


def variants(user_input: str) -> list[str]:
    """대조에 쓸 변형 목록 — [1] 그대로(정규화) → [2] 숫자 변환 순."""
    base = normalize_name(user_input)
    out = [base]
    converted = convert_digit_runs(base)
    if converted != base:
        out.append(converted)
    return out


def build_index(corps: list[tuple[str, str]]) -> dict[str, list[str]]:
    """{정규화된 법인명: [고유번호,...]} 색인 — 동명은 전부 담는다."""
    index: dict[str, list[str]] = {}
    for corp_id, name in corps:
        index.setdefault(normalize_name(name), []).append(corp_id)
    return index


def match_layer1(user_input: str, index: dict[str, list[str]]) -> tuple[str | None, list[str]]:
    """층1 대조. 반환 = (걸린 변형 단계, 후보 고유번호 전부).

    못 걸리면 (None, []) — 층2(AI)로 넘어갈 신호다. 여기서 「대상 아님」을 말하지 않는다.
    """
    for stage, candidate in zip(("그대로", "숫자변환"), variants(user_input)):
        hits = index.get(candidate)
        if hits:
            return stage, list(hits)
    return None, []
