"""명시 기한만 만료 판정하고 새 계획·혼합 구간은 보존하는 회귀."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.features.homepage.expired_plan import only_expired_plan


_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/expired_official_plans.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("item", _FIXTURE["items"], ids=("실제84", "실제94"))
def test_실제_작가에게_전달된_이미_지난_계획만_판정한다(item):
    text = item["text"]
    assert hashlib.sha256(text.encode()).hexdigest() == item["text_sha256"]
    assert only_expired_plan(text, collected_at=_FIXTURE["collected_at"])


@pytest.mark.parametrize("text", (
    "해외 결제: 24년 1분기 중 지원 예정",
    "해외 결제: 2024년 1분기 중 지원 예정",
    "서비스는 2024년 12월 내 제공 예정입니다.",
    "제품은 2025년 말 출시할 계획입니다.",
    "행사는 2026년 10월 14일에 개최할 예정입니다.",
    "해외 결제는 2026년 3분기 중 지원 예정입니다.",
))
def test_닫힌_일정_문법의_기간_종료일이_지나면_만료다(text):
    assert only_expired_plan(text, collected_at="2026-10-21")


@pytest.mark.parametrize("text", (
    "행사는 2026년 10월 21일 개최 예정입니다.",
    "해외 결제는 2026년 10월 중 지원 예정입니다.",
    "해외 결제는 2026년 4분기 중 지원 예정입니다.",
    "해외 결제는 27년 1분기 중 지원 예정입니다.",
    "회사는 2030년 달성 목표를 제시했습니다.",
    "2024년 발표한 2030년 달성 목표입니다.",
    "2024년에 발표한 장기 전략을 추진할 계획입니다.",
    "2024년 착수한 사업을 계속 추진합니다.",
    "해외 결제는 24년 1분기 중 지원 예정이며 새 상품은 출시할 계획입니다.",
    "해외 결제는 24년 1분기 중 지원 예정입니다. 새 상품을 개발합니다.",
    "해외 결제는 24년 1분기 중 지원 예정입니다. 새 사업을 준비하고 있습니다.",
    "해외 결제는 24년 1분기 중 지원 예정이었으며 2027년 1분기로 일정을 변경했습니다.",
    "해외 결제는 24년 1분기 중 지원 예정이었으며 현재도 그 계획을 재확인했습니다.",
    "24년 1분기 중 지원 예정이며 지금도 유효하다.",
    "24년 1분기 중 지원 예정. 해외 진출 전략을 수립한다.",
    "24년 1분기 중 지원 예정. 향후 신규 시장 진출을 검토 중이다.",
    "24년 1분기 중 지원 예정. 별도의 새로운 활동도 있습니다.",
    "서비스는 2024년 출시 예정이었으나 최근 출시를 다시 확정했습니다.",
    "서비스는 2024년 출시 예정입니다. 내년에는 해외에도 진출합니다.",
    "향후 해외에서 사용할 수 있어요. 24년 1분기 중 지원 예정.",
    "곧 해외에서 사용할 수 있어요. 24년 1분기 중 지원 예정.",
    "해외 결제는 24년 1분기 중 지원 예정이었으며 2030년 확대 목표도 있습니다.",
    "2024년 매출액은 10억원입니다. 새 상품 출시를 준비하고 있습니다.",
    "서비스 출시 일정은 미정입니다.",
    "서비스는 2024년 13월 중 제공 예정입니다.",
    "서비스는 2024년 2월 30일 제공 예정입니다.",
    "서비스는 24년 지원 예정입니다.",
    "서비스는 2024년 5분기 지원 예정입니다.",
))
def test_미래_기한_혼합_갱신_발표일_불명확한_일정은_보존한다(text):
    assert not only_expired_plan(text, collected_at="2026-10-21")


@pytest.mark.parametrize("prefix", (
    "다음 달부터 A를 사용할 수 있어요",
    "다음 분기부터 A를 이용할 수 있습니다",
    "서비스가 출시되면 A를 사용할 수 있어요",
    "조만간 A를 사용할 수 있어요",
    "내일부터 A를 사용할 수 있어요",
    "조건을 충족하면 업체를 통해 한 번에 사용할 수 있어요",
    "내일부터업체를 통해 한 번에 사용할 수 있어요",
))
@pytest.mark.parametrize("separator", (". ", ", "))
def test_현재능력처럼_끝나도_상대미래_조건부_별도절은_보존한다(prefix, separator):
    assert not only_expired_plan(
        prefix + separator + "B는 24년 1분기 중 지원 예정", collected_at="2026-10-21"
    )


@pytest.mark.parametrize("addition", (
    "\n새로운 상품은 출시할 예정입니다.",
    "\n2027년 10월 14일에 후속 행사를 개최할 예정입니다.",
    "\n내년 세미나 참가자에게도 자료를 제공할 계획입니다.",
    "\n새 행사를 준비하고 있습니다.",
    "\n행사를 매년 개최합니다.",
    "\n해외 진출 전략을 수립한다.",
    "\n새 해외 진출 전략을 마련했습니다.",
    "\n새 해외 진출 전략",
    "\n향후 신규 시장 진출을 검토 중이다.",
    "\n참석자 안내는 내년에 다시 드릴 예정입니다.",
    "\n내년 행사 참석자에게도 안내할 예정입니다.",
    "\n일시 : 2026년 11월 14일(수) 오후 1:00",
))
def test_실제_행사에_다른_계획이_섞이면_전체_구간을_보존한다(addition):
    assert not only_expired_plan(
        _FIXTURE["items"][1]["text"] + addition, collected_at="2026-10-21"
    )


@pytest.mark.parametrize("text", (
    "세미나 소개\n일시 : 2026년 10월 14일\n참석자에게 안내할 예정입니다.",
    "행사 신청\n일시: 2026년 10월 14일\n선정된 분들께 링크를 안내드릴 예정입니다.",
))
def test_하나의_행사와_참가_안내는_같은_명시_일정을_따른다(text):
    assert only_expired_plan(text, collected_at="2026-10-21")
    assert not only_expired_plan(text, collected_at="2026-10-14")


@pytest.mark.parametrize("text", (
    "실적 안내\n일시 : 2026년 10월 14일\n새 서비스 지원 예정입니다.",
    "세미나 신청\n공지일 : 2026년 10월 14일\n참석자에게 안내할 예정입니다.",
    "세미나 신청\n일시 : 2026년 10월 14일\n새 상품 출시 예정입니다.",
    "세미나 신청\n일시 : 2026년 10월 14일\n참석자는 새 상품 출시 안내를 드릴 예정입니다.",
))
def test_공지일이나_다른_행동을_행사_일정에_묶지_않는다(text):
    assert not only_expired_plan(text, collected_at="2026-10-21")


@pytest.mark.parametrize("baseline", ("", "미상", "2026-02-30", "2026-10-21T00:00:00"))
def test_기준일을_현재_시각으로_대체하지_않는다(baseline):
    assert not only_expired_plan("24년 1분기 중 지원 예정", collected_at=baseline)


def test_기준일은_수집값의_실제_한국날짜이고_시스템_시각이_아니다():
    text = "2026년 10월 21일 개최 예정"
    assert not only_expired_plan(text, collected_at="20261021")
    assert not only_expired_plan(text, collected_at="2026-10-21T14:59:59+00:00")
    assert only_expired_plan(text, collected_at="2026-10-21T15:00:00+00:00")
    assert not only_expired_plan("24년 1분기 중 지원 예정", collected_at="1999-10-21")
    assert not only_expired_plan("24년 1분기 중 지원 예정", collected_at="2100-10-21")
