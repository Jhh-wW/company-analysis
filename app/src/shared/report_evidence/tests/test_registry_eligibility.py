# -*- coding: utf-8 -*-
"""등록부 색인 자격 술어 — «자격 없음»과 «검증 실패»를 가르는 유일한 경계.

이 술어가 넓어지면 도장 깨짐·중복 ID 같은 진짜 실패까지 조용히 건너뛰게 되고,
좁아지면 legacy 줄이 늘 섞이는 보완조사 경로가 통째로 막힌다. 그래서 실측한
두 legacy 모양과, «자격 없음이 아닌» 모양을 함께 못 박는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.shared.report_evidence.registry_eligibility import (
    registry_indexing_ineligible,
)


@dataclass(frozen=True)
class _Row:
    """등록부 줄의 필드만 흉내 낸다 — 공용 계약은 기능 자료형을 모른다."""

    source_type: str = "공식 공시"
    fact_status: str = "공시 실제값"
    published_at: str = "2026-03-31"
    disclosed_at: str = ""
    collected_at: str = ""


def test_필수필드가_다_있으면_자격_없음이_아니다() -> None:
    assert registry_indexing_ineligible(_Row()) is False


def test_공시_원문조각_모양은_자격이_없다() -> None:
    """실측 legacy ① — ``source_type``·``fact_status``가 빈 문자열이다."""

    assert registry_indexing_ineligible(
        _Row(source_type="", fact_status="", published_at="", disclosed_at="2026-03-31")
    ) is True
    assert registry_indexing_ineligible(_Row(source_type="   ")) is True
    assert registry_indexing_ineligible(_Row(fact_status="")) is True


def test_재무API_응답_모양은_자격이_없다() -> None:
    """실측 legacy ② — 필드는 있는데 세 날짜가 모두 빈 문자열이다."""

    assert registry_indexing_ineligible(
        _Row(
            source_type="공식 재무 API",
            fact_status="공시 실제값",
            published_at="",
            disclosed_at="",
            collected_at="",
        )
    ) is True


def test_날짜가_하나라도_있으면_자격_없음이_아니다() -> None:
    for field in ("published_at", "disclosed_at", "collected_at"):
        row = _Row(published_at="", disclosed_at="", collected_at="")
        row = type(row)(**{**row.__dict__, field: "2026-03-31"})
        assert registry_indexing_ineligible(row) is False, field


def test_달력에_없는_날짜나_다른_꼴은_날짜로_세지_않는다() -> None:
    """정본 ``is_canonical_valid``와 같은 엄격 ISO 판정을 쓴다."""

    for bad in ("2026-02-30", "20260331", "2026/03/31", "2026-3-31", "오늘"):
        assert registry_indexing_ineligible(
            _Row(published_at=bad, disclosed_at="", collected_at="")
        ) is True, bad


def test_필드가_없거나_문자열이_아니면_자격_없음으로_보지_않는다() -> None:
    """확신할 수 없는 줄까지 건너뛰면 fail-closed 구멍이 다시 생긴다."""

    class _NoFields:
        pass

    @dataclass(frozen=True)
    class _NonString:
        source_type: object = 3
        fact_status: str = "공시 실제값"
        published_at: str = "2026-03-31"
        disclosed_at: str = ""
        collected_at: str = ""

    @dataclass(frozen=True)
    class _NonStringDate:
        source_type: str = "공식 공시"
        fact_status: str = "공시 실제값"
        published_at: object = None
        disclosed_at: str = ""
        collected_at: str = ""

    assert registry_indexing_ineligible(_NoFields()) is False
    assert registry_indexing_ineligible(object()) is False
    assert registry_indexing_ineligible(_NonString()) is False
    assert registry_indexing_ineligible(_NonStringDate()) is False


def test_도장이나_중복은_이_술어의_판정_재료가_아니다() -> None:
    """도장이 깨져도 필수 필드가 있으면 «자격 없음»이 아니다 — 그래서 닫힌다."""

    @dataclass(frozen=True)
    class _BrokenSeal(_Row):
        provenance_seal: str = "a" * 64
        source_id: str = "같은-아이디"

    assert registry_indexing_ineligible(_BrokenSeal()) is False
