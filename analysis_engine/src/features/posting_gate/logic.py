"""공고 판별 2층 — 코드 검사 (확정적 안전망). 1층 AI 뒤에 겹친다.

정본: 확정/기획서/01_식별/04_공고판별규칙.md §2층
  ① 공고 전용 항목 2개 이상 (9종 사전 — 이력서에도 나오는 말은 세지 않는다)
  ② 개인 신상 「값」 조합 없음 (생년월일 값 AND 휴대전화 값 → 차단)
하나라도 걸리면 즉시 폐기. 2층이 못 막는 유형(공고 인용 자소서)은 1층 AI가 유일한 방어선이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.personal_patterns import BIRTH_VALUE_RE, PHONE_VALUE_RE, normalize_for_scan

# 공고 전용 항목 9종 — 한 묶음에서 하나라도 나오면 그 항목 1개로 센다
SECTION_LEXICON: dict[str, tuple[str, ...]] = {
    "자격요건": ("자격요건", "지원자격", "필수요건", "자격조건", "필요역량", "필수사항"),
    "고용형태": ("고용형태", "근무형태", "정규직", "계약직", "인턴", "파견직"),
    "전형절차": ("전형절차", "채용절차", "서류전형", "면접전형", "채용과정"),
    "복리후생": ("복리후생", "복지", "혜택"),
    "접수기간": ("접수기간", "마감일", "상시채용", "모집기간", "채용시"),
    "우대사항": ("우대사항", "우대조건", "우대요건"),
    "접수방법": ("접수방법", "지원방법", "입사지원", "제출서류"),
    "모집분야": ("모집분야", "모집부문", "채용분야", "모집직무", "직군"),
    "모집인원": ("모집인원", "채용인원"),
}
MIN_SECTIONS = 2  # 공고 86건 실측 최소 2개 — 잘린 캡처를 고려해 낮게 잡은 문턱

# P-09 보강: OCR·표기 습관으로 「필수 사항」처럼 글자 사이가 벌어진 항목명을 놓치던 문제.
# 같은 줄 안의 공백 1칸까지만 허용한다 — 줄바꿈까지 허용하면 「마감\n일정」처럼
# 서로 다른 단어가 붙어 오검출되므로 불허 (보강 직후 비공고 100건 재측정으로 부작용 확인).
# 공백 허용은 4글자 이상 항목명만 — 「채용시」·「마감일」 같은 짧은 키워드에 허용하면
# 「채용 시장」·「마감 일정」 등 평범한 비공고 문장이 오통과한다 (독립 검증 발견 · 2026-08-14).
_KW_GAP = r"[ \t]?"
_MIN_GAPPED_KW_LEN = 4


def _kw_pattern(word: str) -> str:
    if len(word) >= _MIN_GAPPED_KW_LEN:
        return _KW_GAP.join(map(re.escape, word))
    return re.escape(word)


_SECTION_RES: dict[str, re.Pattern[str]] = {
    name: re.compile("|".join(_kw_pattern(w) for w in words))
    for name, words in SECTION_LEXICON.items()
}


@dataclass(frozen=True)
class Layer2Result:
    sections_found: tuple[str, ...]
    has_personal_combo: bool
    passed: bool
    reason: str


def find_sections(text: str) -> tuple[str, ...]:
    """정규화본(전각·제로폭 정리)에서 항목명을 찾는다 — 공백 낀 표기(「필수 사항」)도 검출."""
    t = normalize_for_scan(text)
    return tuple(name for name, rx in _SECTION_RES.items() if rx.search(t))


def has_personal_combo(text: str) -> bool:
    """생년월일 「값」과 휴대전화 「값」이 동시에 있으면 개인 문서로 본다.

    전각 숫자·제로폭 문자로 값을 흘려 넣는 표기를 막기 위해 정규화본에서 찾는다.
    """
    t = normalize_for_scan(text)
    return BIRTH_VALUE_RE.search(t) is not None and PHONE_VALUE_RE.search(t) is not None


def layer2(text: str) -> Layer2Result:
    sections = find_sections(text)
    combo = has_personal_combo(text)
    if combo:
        return Layer2Result(sections, True, False, "개인 신상 값 조합(생년월일+전화) 검출")
    if len(sections) < MIN_SECTIONS:
        return Layer2Result(sections, False, False,
                            f"공고 전용 항목 {len(sections)}개 (< {MIN_SECTIONS})")
    return Layer2Result(sections, False, True, "2층 통과")
