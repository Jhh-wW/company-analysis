"""저장된 «실제» 보고서로 문장 정리(dedupe)를 지키는 시험대.

★ 왜 이 파일이 생겼나 (2026-08-25, 같은 날 두 번의 사고)
  ─────────────────────────────────────────────────────────
  ① 중복 제거 규칙을 하나 넣었더니 저장본 25건 중 18건에서 무관한 문장이
     지워졌다(현대차 9장 6문장 → 1문장). 그런데 **시험 290개가 전부 초록불**
     이었다.
  ② 홈페이지 수집 규칙을 넣었더니 남의 회사 사이트가 «공식 웹»으로 들어왔다.
     그때도 **시험 180개가 전부 초록불**이었다.

  두 번 다 원인이 같다. 새로 넣은 시험이 전부 «손으로 지은 짧은 문장»이었다.
  손글 문장은 짧고 깔끔해서, 진짜 보고서에서만 나타나는 모양(한 문장에 사실이
  둘씩 섞이고, 같은 수치가 다른 문맥에 흩어져 있는 모양)을 재현하지 못한다.
  그래서 규칙이 실물에서 무엇을 부수는지 시험이 볼 수 없었다.

  이 파일은 그 구멍을 막는다. **실제로 출고된 보고서의 문장 배열**을 그대로
  픽스처로 굳혀 놓고, 정리 단계를 통과시켜 전/후를 비교한다.

★ 픽스처는 왜 DB가 아니라 파일인가
  ─────────────────────────────────────────────────────────
  `app/data/storage.db`와 `app/.local_deployment_rehearsal_runs/*/storage.db`는
  이 PC에만 있고 지워질 수 있으며 CI에는 아예 없다. 시험이 매번 DB를 읽으면
  DB가 사라지는 순간 시험도 같이 사라진다 — 지켜 주지 못한다. 그래서 대표
  보고서를 골라 JSON으로 굳혔다. 다시 만드는 도구는
  `stored_reports/build_from_storage.py` (시험은 부르지 않는다).

★ 무엇을 골랐고 왜인가 (실측 2026-08-25)
  ─────────────────────────────────────────────────────────
  ⛔ **아래 한 문단은 2026-08-27에 «틀린 것으로 판명»났다. 정정이 그 뒤에 있다.**

  저장본 전체를 훑어 «v2 9개 장 + 문장 단위(prose_lines)» 모양인 보고서는
  **5건**(같은 내용의 중복 저장분 제외)뿐이었고, 회사는 **(주)하이브·(주)진영
  둘**이다. `app/data/storage.db`의 31건은 전부 **v1 12칸 모양**(칸당 문장
  1~2개)이라 v2 장 id가 없고 코퍼스로 쓸 분량도 안 된다 — 억지로 v2 장에
  끼워 넣는 것은 «지어내기»라 하지 않았다. 그래서 회사는 둘뿐이다(더 없다).

  ⛔ **정정 (2026-08-27) — 바로 위의 「저장본 전체」와 「회사는 둘뿐」은 사실이 아니다.**
  그때 훑은 것은 세 갈래 중 «두 갈래»뿐이었다. 픽스처를 만드는 도구
  (`stored_reports/build_from_storage.py`)의 `_storage_paths()` 가
  `app/.local_evaluation_runs/` 를 안 보고 있었기 때문이다.
  그 갈래를 더해 다시 세니 **v2 문장형 보고서가 9건 더** 있었고, 회사는
  둘이 아니라 **다섯**이다 — (주)하이브 · (주)진영 · (주)제이와이피엔터테인먼트 ·
  **삼성전자(주)** · **현대자동차(주)** (2026-08-27 실측).
  ★ 그래도 이 파일의 픽스처 5건은 «그대로 둔다». 코퍼스를 늘리는 것은 기준값이
    바뀌는 별도 작업이고, 지금 고치면 이 시험대가 지켜 주던 것이 흔들린다.
    늘릴 때 쓸 재료가 있다는 사실만 여기 남긴다.
  ★ `app/data/storage.db`의 31건이 v1 모양이라는 것은 그대로 참이다 — 틀린 것은
    「저장본 «전체»를 훑었다」와 그로부터 나온 「둘뿐」이라는 단정뿐이다.

  다만 하이브 4건은 서로 **다른 조사 회차**라 문장이 거의 겹치지 않는다
  (문장 집합 자카드 0.01~0.09 — 실측). 즉 같은 회사라도 서로 다른 코퍼스다.
  그리고 넷 중 셋이 아래 «수치 사라짐» 그물에 실제로 걸리는 재료를 갖고 있어
  (실패 ①의 패치를 얹으면 그 셋에서 유일 수치가 사라진다) 넷 다 남겼다.

    파일                      회사        정리 전 문장   기준 뺀 문장
    jinyoung_aa81160a.json    (주)진영        54           0
    hive_40c8cc92.json        (주)하이브      49           0
    hive_034898b2.json        (주)하이브      50           0
    hive_8d6ae287.json        (주)하이브      53           1
    hive_1b7620d0.json        (주)하이브      50           1

★ 픽스처에 담긴 것과 담지 않은 것
  ─────────────────────────────────────────────────────────
  담은 것: 장 id · 문장 글 · 인용 조각 번호 · 등급(확인/해석).
  담지 않은 것: 사람 이름·전자우편·전화·주소·비밀값. 만드는 도구가 검사해
  걸리면 파일을 아예 안 만들고, 아래 `test_픽스처에_개인정보나_비밀값이_없다`가
  같은 검사를 한 번 더 한다.

★ 되돌리지 못한 것 (정직하게 적어 둔다)
  ─────────────────────────────────────────────────────────
  저장본은 «렌더된 글»이라 «해석» 문장의 인용 번호가 화면에 아예 안 나온다.
  그래서 픽스처의 해석 문장은 인용이 빈 튜플이다 — 실제보다 **인용이 적다**.
  정리 단계의 첫째 규칙은 «근거를 공유할 때만» 묶으므로, 이 픽스처의 기준값은
  실제보다 «덜 지우는» 쪽으로 치우쳐 있다. 그물이 헐거워지는 방향이지
  없는 결함을 만들어 내는 방향은 아니다.

★ 이 시험이 빨간불이면
  ─────────────────────────────────────────────────────────
  «정리 단계가 실물 보고서에서 하는 일이 바뀌었다»는 뜻이다.
  **기준값을 새 결과에 맞춰 고쳐서 통과시키지 마라.** 먼저 아래를 확인한다.
    - 어느 장에서 몇 문장이 더 빠졌나 (실패 메시지가 알려 준다)
    - 그 문장이 다른 장에 정말 남아 있나 (없으면 사실이 통째로 사라진 것이다)
  바꾼 것이 «의도한 개선»이라고 판단되면, 그때 기준값을 갱신하고 **왜 그것이
  개선인지 한 줄을 이 파일에 남긴다.**
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Final

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.dedupe import drop_cross_section_duplicates
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)

_FIXTURE_DIR: Final[Path] = Path(__file__).resolve().parent / "stored_reports"

#: 정리 뒤에 남아야 하는 장별 문장 수 — 2026-08-25 실측으로 굳힌 «지금 동작».
#: (파일이름 → (정리 전 문장 수, {장 id: 정리 후 문장 수}, 뺀 문장 수))
BASELINE: Final[dict[str, tuple[int, dict[str, int], int]]] = {
    "jinyoung_aa81160a.json": (
        54,
        {
            "identity": 10,
            "business_model": 4,
            "portfolio": 6,
            "past_changes": 5,
            "current_challenges": 6,
            "future_strategy": 7,
            "operations_partners": 3,
            "culture": 8,
            "competitive_position": 5,
        },
        0,
    ),
    "hive_40c8cc92.json": (
        49,
        {
            "identity": 6,
            "business_model": 5,
            "portfolio": 1,
            "past_changes": 7,
            "current_challenges": 4,
            "future_strategy": 6,
            "operations_partners": 7,
            "culture": 6,
            "competitive_position": 7,
        },
        0,
    ),
    "hive_034898b2.json": (
        50,
        {
            "identity": 7,
            "business_model": 7,
            "portfolio": 2,
            "past_changes": 8,
            "current_challenges": 5,
            "future_strategy": 7,
            "operations_partners": 6,
            "culture": 4,
            "competitive_position": 4,
        },
        0,
    ),
    "hive_8d6ae287.json": (
        53,
        # portfolio 가 7 → 6. 부문 나열 문장이 다른 장과 겹쳐 «지금» 규칙이
        # 잡는 자리다. 이 1건은 그물이 헛돌지 않는다는 증거이기도 하다.
        {
            "identity": 6,
            "business_model": 7,
            "portfolio": 6,
            "past_changes": 8,
            "current_challenges": 5,
            "future_strategy": 7,
            "operations_partners": 8,
            "culture": 3,
            "competitive_position": 2,
        },
        1,
    ),
    "hive_1b7620d0.json": (
        50,
        # identity 가 8 → 7 (글로벌 확장 문장이 다른 장과 겹친다).
        {
            "identity": 7,
            "business_model": 9,
            "portfolio": 7,
            "past_changes": 5,
            "current_challenges": 3,
            "future_strategy": 8,
            "operations_partners": 3,
            "culture": 3,
            "competitive_position": 4,
        },
        1,
    ),
}

#: 본문에서 «수치»로 셀 글자 덩어리. 자릿점(1,840)과 소수점(10.36)을 허용한다.
#: ★ 단위를 함께 잡지 않는 이유 — 「1,840억원으로」처럼 조사가 붙는 순간
#:   단위 경계가 흔들려 같은 수치가 다른 수치로 갈린다. 「몇이 적혀 있었나」만
#:   보면 그 흔들림이 없고, «한 번뿐인가»라는 조건이 연도·개수 같은 흔한
#:   숫자를 알아서 걸러 준다.
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: 한 번뿐인 수치가 이보다 적으면 「수치 사라짐」 그물이 헛도는 것이다.
#: 5건 중 가장 적은 픽스처가 7개다(실측 2026-08-25).
_MIN_UNIQUE_NUMBERS: Final[int] = 7

#: 손글 문장과 실물 문장을 가르는 선. 실측 평균 문장 길이는 82.7~94.0자였다
#: (5건 전부, 2026-08-25). 오늘 두 번 놓친 시험들의 손글 문장은 20~40자다.
_MIN_AVERAGE_SENTENCE_CHARS: Final[int] = 60

#: 픽스처에 있으면 안 되는 것들. 만드는 도구와 같은 잣대를 여기서 다시 잰다.
_FORBIDDEN_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("전자우편", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("전화번호", re.compile(r"0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}")),
    ("주소", re.compile(r"https?://")),
    ("비밀값", re.compile(r"(?i)api[_-]?key|secret|token|password|sk-")),
    ("주민등록번호", re.compile(r"\d{6}-\d{7}")),
)


def _fixture_names() -> list[str]:
    return sorted(path.name for path in _FIXTURE_DIR.glob("*.json"))


def _load_report(fixture_name: str) -> ComposedReport:
    """픽스처 JSON을 ComposedReport로 되살린다."""
    document = json.loads((_FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section["section_id"],
                sentences=tuple(
                    ComposedSentence(
                        text=sentence["text"],
                        citations=tuple(sentence["citations"]),
                        grade=sentence["grade"],
                    )
                    for sentence in section["sentences"]
                ),
            )
            for section in document["sections"]
        ),
    )


def _all_texts(report: ComposedReport) -> list[str]:
    return [sentence.text for section in report.sections for sentence in section.sentences]


def _numbers_in(text: str) -> set[str]:
    """문장 하나에서 수치를 뽑는다 (자릿점 제거·꼬리 마침표 제거)."""
    flat = unicodedata.normalize("NFKC", text)
    return {
        found.group(0).replace(",", "").rstrip(".") for found in _NUMBER_RE.finditer(flat)
    }


# ══════════════════════════════════════════════════════════
# ① 정리 뒤에도 장별 문장 수가 기준값과 같다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("fixture_name", sorted(BASELINE))
def test_저장본을_정리해도_장별_문장_수가_기준값과_같다(fixture_name: str) -> None:
    """★ 실패 ①(과잉 삭제)을 잡는 그물.

    지금 동작을 그대로 못 박는다. 누가 정리 규칙을 건드려 실물 보고서에서
    문장이 더 빠지거나 덜 빠지면 이 시험이 «바로» 빨간불이 된다.
    """
    total_before, expected_by_section, expected_dropped = BASELINE[fixture_name]
    report = _load_report(fixture_name)
    assert sum(len(section.sentences) for section in report.sections) == total_before, (
        f"{fixture_name} 픽스처 자체가 바뀌었습니다 — 기준값을 고치기 전에 "
        f"픽스처가 왜 바뀌었는지부터 확인하세요"
    )

    cleaned, dropped = drop_cross_section_duplicates(report)

    actual_by_section = {
        section.section_id: len(section.sentences) for section in cleaned.sections
    }
    changed = {
        section_id: (expected_by_section[section_id], actual_by_section.get(section_id))
        for section_id in expected_by_section
        if expected_by_section[section_id] != actual_by_section.get(section_id)
    }
    assert not changed, (
        f"{fixture_name}: 정리 뒤 장별 문장 수가 기준값과 다릅니다 "
        f"(장: 기준 → 지금) {changed}. "
        f"기준값을 새 결과에 맞추기 전에, 빠진 문장이 다른 장에 정말 남아 "
        f"있는지 확인하세요."
    )
    assert dropped == expected_dropped, (
        f"{fixture_name}: 뺀 문장 수가 {expected_dropped} → {dropped} 로 바뀌었습니다"
    )


# ══════════════════════════════════════════════════════════
# ② 본문에 한 번뿐인 수치가 사라지지 않는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("fixture_name", sorted(BASELINE))
def test_한_번뿐인_수치는_정리_뒤에도_본문에_남는다(fixture_name: str) -> None:
    """★ 실패 ①이 정확히 어긴 규칙 — 현대차 순이익 10.36조가 통째로 사라졌다.

    정리는 «중복을 옮기는» 일이지 «사실을 없애는» 일이 아니다. 본문 전체에서
    딱 한 문장에만 나오는 수치는 그 문장이 유일한 자리이므로, 그 문장이 지워지면
    보고서에서 그 사실이 «없어진다». 그래서 한 번뿐인 수치는 정리 뒤에도
    반드시 남아 있어야 한다.

    ★ 이 시험이 빨간불이면 기준값 문제가 아니라 «사실이 사라진» 문제다.
    """
    report = _load_report(fixture_name)
    texts_before = _all_texts(report)

    seen_count: dict[str, int] = {}
    for text in texts_before:
        for number in _numbers_in(text):
            seen_count[number] = seen_count.get(number, 0) + 1
    only_once = {number for number, count in seen_count.items() if count == 1}
    assert len(only_once) >= _MIN_UNIQUE_NUMBERS, (
        f"{fixture_name}: 한 번뿐인 수치가 {len(only_once)}개뿐이라 이 시험이 "
        f"헛돕니다 — 픽스처가 바뀌었는지 확인하세요"
    )

    cleaned, _ = drop_cross_section_duplicates(report)
    texts_after = _all_texts(cleaned)
    numbers_after: set[str] = set()
    for text in texts_after:
        numbers_after |= _numbers_in(text)

    vanished = sorted(only_once - numbers_after)
    vanished_sentences = [
        text
        for text in texts_before
        if _numbers_in(text) & set(vanished) and text not in texts_after
    ]
    assert not vanished, (
        f"{fixture_name}: 본문에 한 번뿐인 수치가 정리에서 사라졌습니다 {vanished}. "
        f"사라진 문장: {[text[:70] for text in vanished_sentences]}"
    )


# ══════════════════════════════════════════════════════════
# ③ 시험대 자체를 지키는 못 — 그물이 헛돌지 않게
# ══════════════════════════════════════════════════════════


def test_기준값_표가_픽스처_파일_전부를_덮는다() -> None:
    """픽스처를 넣고 기준값을 안 적으면 «조용히» 검사에서 빠진다 — 그걸 막는다."""
    assert _fixture_names() == sorted(BASELINE), (
        "픽스처 폴더와 기준값 표가 어긋납니다 — 새 픽스처를 넣었다면 "
        "기준값도 같이 적으세요"
    )


@pytest.mark.parametrize("fixture_name", sorted(BASELINE))
def test_픽스처는_정본_9개_장을_그대로_가진다(fixture_name: str) -> None:
    """장 id가 정본과 다르면 소유 장 판정(정본 목차 순서)이 달라진다."""
    report = _load_report(fixture_name)
    assert tuple(section.section_id for section in report.sections) == SECTION_IDS


@pytest.mark.parametrize("fixture_name", sorted(BASELINE))
def test_픽스처는_손으로_지은_짧은_문장이_아니다(fixture_name: str) -> None:
    """★ 오늘 두 번의 사고가 시험을 통과한 «진짜» 이유를 못으로 박는다.

    손글 문장은 짧고 사실이 하나뿐이라, 실물에서만 나타나는 모양을 못 만든다.
    이 픽스처가 언젠가 손글로 바뀌면 위의 두 그물도 같이 헐거워지므로,
    «실물에서 왔다»는 성질 자체를 시험으로 지킨다.
    """
    texts = _all_texts(_load_report(fixture_name))
    average_chars = sum(len(text) for text in texts) / len(texts)
    assert average_chars >= _MIN_AVERAGE_SENTENCE_CHARS, (
        f"{fixture_name}: 평균 문장 길이가 {average_chars:.0f}자입니다 — "
        f"실물 보고서 문장이 맞는지 확인하세요"
    )


@pytest.mark.parametrize("fixture_name", sorted(BASELINE))
def test_픽스처에_개인정보나_비밀값이_없다(fixture_name: str) -> None:
    """픽스처는 저장소에 남는다 — 만들 때 한 검사를 여기서 다시 한다."""
    joined = "\n".join(_all_texts(_load_report(fixture_name)))
    hits = [name for name, pattern in _FORBIDDEN_PATTERNS if pattern.search(joined)]
    assert not hits, f"{fixture_name}에 {hits}이(가) 들어 있습니다"
