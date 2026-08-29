# -*- coding: utf-8 -*-
"""③ 「이미 통과한 검사를 인정한다」를 못 박는다.

★ 왜 이 파일이 생겼나 (2026-08-29 실측 + 사용자 결정)
  ─────────────────────────────────────────────────────────
  v2-98 이 넣은 수치 안전 필터가 「숫자가 든 문장은 구조화 사실이 없으면 삭제」했다.
  그런데 그 구조화 사실을 만드는 곳은 «프로그램이 4장에 붙이는 누적 증감률 문장»
  하나뿐이고, 작가 AI 에게 그걸 만들라고 요구하는 프롬프트가 «하나도 없었다».

  → 작가가 쓴 숫자 문장은 «구조적으로 전부» 삭제됐다.
    실측(현대카드): 45문장 → 25문장, 4장은 0문장, 점수 33/100 (기준 100·88).
    「1995년에 설립됐다」처럼 «연도»만 든 문장까지 삭제 대상이었다.

★ 그런데 그 문장들은 «이미 두 번» 검사를 통과한 상태였다:
  ① 숫자를 인용 조각·실적표와 대조 (`verify.py::_numeric_disposal`)
  ② 검수 AI 가 근거와 맞는지 판정 (`verify.py::_semantic_review`, 참일 때만)
  `verification_state == "verified"` 는 그 둘을 «모두» 통과했다는 표식이다.

  세 번째 관문이 요구한 것은 «아무도 만들어 주지 않는» 증명서였다.

★ 이 시험이 지키는 것
  ① 두 검사를 통과한 문장은 통과한다 (그래야 보고서가 두꺼워진다)
  ② **검사를 «빼지» 않았다** — 넷 중 하나라도 빠지면 여전히 삭제된다  ← 안전선
  ③ 스위치를 끄면 v2-98 원래 동작으로 돌아간다

★ 2026-08-29 «구멍 하나를 막고» 추가한 네 번째 조건 — 증명서가 없을 것
  처음 판에는 조건이 셋뿐이라, 프로그램이 4장에 직접 붙이는 누적 증감률 문장처럼
  «증명서가 이미 발급된» 문장까지 ③ 통로로 빠져나갔다. 그 문장은 표시 숫자만
  25%로 바꿔치기해도 검수 표식을 그대로 달고 있어 위조가 통과했다
  (`test_structured_claims.py::test_결속값과_다르게_공개문장만_25퍼센트로_바꾸면_제외한다`).
  → 맞춰 볼 수 있으면 «반드시» 맞춰 본다. ③ 은 증명서를 발급할 길이
    애초에 없던 문장(작가가 쓴 문장)에만 적용한다.
"""

from __future__ import annotations

import pytest

from src.features.composer import structured_claims as sc
from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.port import ComposedSentence, StructuredClaim
from src.shared.report_quality.models import VerificationState

_숫자문장 = "2025년 매출액은 4조 78억 원이다."
_확인됨 = VerificationState.VERIFIED.value


def _문장(**바꿀것) -> ComposedSentence:
    기본 = dict(
        text=_숫자문장,
        citations=("3",),
        grade=GRADE_CONFIRMED,
        verification_state=_확인됨,
    )
    기본.update(바꿀것)
    return ComposedSentence(**기본)


# ══════════════════════════════════════════════════════════
# ① 두 검사를 통과한 문장은 나간다
# ══════════════════════════════════════════════════════════


def test_두_검사를_통과한_숫자문장은_나간다() -> None:
    """★ 이게 2026-08-29 수정의 «이유»다. 되돌리면 보고서가 다시 얇아진다."""
    assert sc.is_release_ready_numeric_sentence(_문장(), section_id="identity") is True


def test_숫자가_없는_문장은_원래대로_통과한다() -> None:
    """숫자가 없으면 이 관문 자체가 대상이 아니다 — 동작을 안 바꿨다."""
    assert (
        sc.is_release_ready_numeric_sentence(
            _문장(text="현대카드는 신용카드 회사다.", verification_state="unverified"),
            section_id="identity",
        )
        is True
    )


def test_연도만_있어도_숫자로_본다() -> None:
    """★ 「1995년에 설립됐다」도 삭제 대상이었다 — 검출망이 그만큼 넓다.

    그러니 «통과 조건»이 현실적이지 않으면 보고서가 통째로 비는 것이다.
    """
    assert sc.has_public_numeric_token("1995년에 설립됐다.") is True


# ══════════════════════════════════════════════════════════
# ② 검사를 «빼지» 않았다  ← 안전선. 되돌리지 마라
# ══════════════════════════════════════════════════════════


def test_검수를_통과못한_숫자문장은_여전히_막힌다() -> None:
    """★ verified 표식이 없으면 두 검사를 안 거친 것이다. 통과시키면 안 된다."""
    assert (
        sc.is_release_ready_numeric_sentence(
            _문장(verification_state="unverified"), section_id="identity"
        )
        is False
    )


def test_해석_등급은_숫자를_실을_수_없다() -> None:
    """★ 「해석」은 사실 주장이 아니다. 숫자를 실을 자격이 없다."""
    assert (
        sc.is_release_ready_numeric_sentence(
            _문장(grade="해석"), section_id="identity"
        )
        is False
    )


def test_인용_없는_숫자문장은_여전히_막힌다() -> None:
    """★ 어느 근거에서 온 숫자인지 되짚을 수 없으면 내보내지 않는다."""
    assert (
        sc.is_release_ready_numeric_sentence(
            _문장(citations=()), section_id="identity"
        )
        is False
    )


def _증명서(**바꿀것) -> StructuredClaim:
    """프로그램이 4장에 붙이는 «발급된 증명서»를 흉내 낸다."""
    기본 = dict(
        fact_id="past_changes:cumulative_rate",
        claim_slot="cumulative_rate",
        section_owner="past_changes",
        source_fragment_id="3",
        source_identity="사업보고서",
        verification_state=_확인됨,
        state_evidence="실적표 대조",
    )
    기본.update(바꿀것)
    return StructuredClaim(**기본)


def test_증명서가_붙은_문장은_표시값을_바꿔치기하면_막힌다() -> None:
    """★ 안전선 — 맞춰 볼 수 있는 문장은 «반드시» 맞춰 본다.

    네 조건 중 「증명서 없음」을 빼면 이 시험이 깨진다. 깨진 채로 두면
    계산값과 다른 숫자가 «검수 통과» 표식을 달고 보고서에 실린다.
    """
    위조 = _문장(
        text="연결 매출액의 2023년부터 2025년까지 누적 증감률은 25.00%입니다.",
        structured_claim=_증명서(),
    )

    assert (
        sc.is_release_ready_numeric_sentence(위조, section_id="past_changes") is False
    ), "★ 증명서와 다른 표시값이 통과하면 숫자 위조를 막을 방법이 없다"


# ══════════════════════════════════════════════════════════
# ③ 되돌릴 수 있는가
# ══════════════════════════════════════════════════════════


def test_스위치를_끄면_원래_동작으로_돌아간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 마음에 안 들면 «한 줄»로 되돌린다 — 다른 코드는 안 건드린다."""
    monkeypatch.setattr(sc, "ALLOW_VERIFIED_NUMERIC_SENTENCES", False)

    assert (
        sc.is_release_ready_numeric_sentence(_문장(), section_id="identity") is False
    ), "★ 스위치를 꺼도 통과하면 되돌릴 방법이 없다는 뜻이다"


def test_스위치가_켜져_있다() -> None:
    """지금 계약은 «켬»이다 (2026-08-29 사용자 결정). 끄려면 근거를 남겨라."""
    assert sc.ALLOW_VERIFIED_NUMERIC_SENTENCES is True
