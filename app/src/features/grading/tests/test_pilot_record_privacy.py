"""시범 실행 기록(`runs.jsonl`)의 공개 저장소 위생 시험.

사용자 원칙 「개인정보는 올리지 않는다」를 기계로 못 박는다.
1판 파일럿 기록에는 실제 회사의 대표자 실명이 `확인카드.대표`에 그대로
남아 있었다. 사람이 눈으로 훑어서는 다시 들어오는 것을 막지 못한다.

이 파일이 채점(grading) 폴더에 있는 이유는, 같은 파일럿 자료를 지키는
`test_grading.py`의 fixture 정합 시험과 한자리에서 같이 읽히게 하기 위함이다.
"""

from __future__ import annotations

import json
import re

import pytest

from src.core import paths


#: 대표자 자리에 들어가야 하는 «단 하나의» 값. 생산 상수를 import 하지 않고
#: 리터럴로 적는다 — 생산 쪽이 바뀌면 이 시험이 먼저 깨져야 하기 때문이다.
_MASKED = "(비공개)"

#: 한국 사람 이름꼴(한글 2~4자). 실명이 다시 들어오면 여기에 걸린다.
_KOREAN_NAME = re.compile(r"^[가-힣]{2,4}$")

#: 기사 본문에서 대표자 이름이 나오는 두 가지 꼴.
#: ★ 실명을 리터럴로 적지 않는다 — 시험 파일에 적으면 지운 이름이 저장소에
#:   그대로 되돌아온다. 대신 «이름이 놓이는 자리»를 본다. 그래서 이 시험은
#:   지운 3명뿐 아니라 앞으로 들어올 어떤 대표자 이름도 잡는다.
#: 「대표이사」는 직함이지 이름이 아니므로 뺀다.
_CEO_NAME_SHAPES = (
    ("「대표 ○○○」꼴", re.compile(r"대표\s+[가-힣]{2,4}")),
    ("「○○○ 대표」꼴", re.compile(r"[가-힣]{2,4}\s+대표(?!이사)")),
)


def _실행기록() -> list[dict]:
    """`runs.jsonl`을 줄마다 읽는다."""
    with paths.PILOT_RUNS_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _대표자_값(record: dict) -> list[str]:
    """한 기록의 모든 단계에서 확인카드의 대표자 값을 모은다."""
    값: list[str] = []
    for step in record.get("steps", []):
        card = step.get("확인카드") if isinstance(step, dict) else None
        if isinstance(card, dict) and "대표" in card:
            값.append(str(card["대표"]))
    return 값


def test_시범_실행기록에_대표자_실명이_없다():
    """★ 공개 저장소에 실제 사람 이름을 올리지 않는다 (사용자 원칙).

    대표자 자리는 고정 문자열 하나여야 하고, 사람 이름꼴이 남아 있으면 안 된다.
    """
    if not paths.PILOT_RUNS_FILE.exists():
        pytest.skip("파일럿 실행 기록이 없습니다 (analysis_engine 미배치)")

    기록들 = _실행기록()

    # ★ 필드가 통째로 사라져도 «조용히 통과»하지 않게, 먼저 찾았는지를 센다.
    대표자_있는_줄 = [i for i, r in enumerate(기록들, start=1) if _대표자_값(r)]
    assert len(대표자_있는_줄) == len(기록들), (
        f"확인카드 대표 칸을 못 찾은 줄이 있습니다: "
        f"{sorted(set(range(1, len(기록들) + 1)) - set(대표자_있는_줄))}"
    )

    가려지지_않은 = [
        (줄, 값)
        for 줄, r in enumerate(기록들, start=1)
        for 값 in _대표자_값(r)
        if 값 != _MASKED
    ]
    assert not 가려지지_않은, (
        f"대표자 칸이 «{_MASKED}»가 아닙니다: {가려지지_않은}"
    )

    이름꼴 = [
        (줄, 값)
        for 줄, r in enumerate(기록들, start=1)
        for 값 in _대표자_값(r)
        if _KOREAN_NAME.match(값)
    ]
    assert not 이름꼴, f"대표자 칸에 사람 이름꼴이 남아 있습니다: {이름꼴}"


def _시범_자료_파일() -> list:
    """조각(JSON)과 보고서(md) 전부."""
    조각 = sorted((paths.PILOT_DIR / "fragments").glob("*.json"))
    보고서 = sorted(paths.PILOT_REPORTS_DIR.glob("*.md"))
    return 조각 + 보고서


def test_시범_조각과_보고서에_대표자_실명이_없다():
    """★ 기사 본문에 실린 대표자 이름도 공개 저장소에 올리지 않는다.

    `runs.jsonl`의 구조화 필드만 가리면 기사 본문에 남은 이름을 놓친다.
    """
    if not paths.demo_data_available():
        pytest.skip("파일럿 자료가 없습니다 (analysis_engine 미배치)")

    파일들 = _시범_자료_파일()
    assert 파일들, "시범 자료를 한 개도 못 찾았습니다 — 시험이 조용히 통과할 뻔했습니다"

    걸린것: list[str] = []
    for path in 파일들:
        본문 = path.read_text(encoding="utf-8")
        for 꼴이름, 꼴 in _CEO_NAME_SHAPES:
            for m in 꼴.finditer(본문):
                걸린것.append(f"{path.name} {꼴이름}: {m.group(0)}")
    assert not 걸린것, (
        "대표자 이름이 남아 있습니다 — 고정 표기로 가리세요: " + str(걸린것[:5])
    )
