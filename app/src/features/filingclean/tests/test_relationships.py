from __future__ import annotations

import pytest

from src.features.filingclean.relationships import (
    add_to,
    business_scope,
    collect_relationship_fragments,
)


def _filing(body: str, note: str = "") -> str:
    padding = "회사는 음반과 공연 사업을 운영하고 매출을 관리합니다. " * 8
    return f"사업의 내용 사업의 개요 {padding}{body} III. 재무에 관한 사항 {note}"


def test_실명_파트너와_관계가_함께_있는_원문만_보충한다():
    text = (
        "회사는 글로벌 경쟁력을 강화하고 다양한 플랫폼 기업과 파트너십을 확대하고 있습니다. "
        "회사는 유수한 글로벌 파트너들과 전략적 협업 관계를 확대하고 있습니다. "
        "당사는 Sony Music, TME (Tencent Music Entertainment), Republic Records 등 "
        "글로벌 음반·음원 유통 전문 회사들과 파트너십을 체결하였습니다. "
        "Live Nation은 글로벌 공연 인프라를 보유합니다. "
        "2023년에는 Live Nation과 전략적 파트너십을 체결하여 글로벌 투어 협력 체계를 구축했습니다. "
        "이번 파트너십을 통해 양사의 사업 시너지를 확대해갈 계획입니다. "
        "회사는 고객 만족을 위해 협업을 강화하고 있습니다."
    )

    result = collect_relationship_fragments(text)

    assert len(result) == 2
    assert "Sony Music" in result[0]
    assert "Live Nation" in result[1]
    assert "확대해갈 계획" in result[1]
    assert all("다양한 플랫폼 기업" not in item for item in result)
    assert all("글로벌 파트너들" not in item for item in result)


def test_기존_조각에_이미_포함된_문장은_중복하지_않는다():
    sentence = "당사는 Republic Records와 음원 유통 계약을 체결하여 북미 유통망을 운영합니다."
    frags = {1: {"종류": "사업내용", "원문": "사업의 개요 " + sentence}}

    result, added = add_to(frags, _filing(sentence))

    assert added == 0
    assert result == frags


def test_추가_조각은_요약하거나_새_사실을_보태지_않는다():
    sentence = "2023년 TME와 전략적 파트너십을 체결해 글로벌 음원 유통망을 운영합니다."

    result, added = add_to({}, _filing(sentence))

    assert added == 1
    assert result[1] == {"종류": "사업내용", "원문": sentence}


def test_영문_일반명사뿐인_파트너십은_실명_관계로_오인하지_않는다():
    sentence = "회사는 global music platform과 strategic partnership을 확대하고 있습니다."

    assert collect_relationship_fragments(sentence) == []


def test_소문자_영문_일반_유통망은_실명_관계로_오인하지_않는다():
    sentence = "회사는 worldwide distribution network와 partnership 계약을 확대하고 있습니다."

    assert collect_relationship_fragments(sentence) == []


def test_제목형_영문_일반_유통망은_실명_관계로_오인하지_않는다():
    sentence = "회사는 Worldwide Distribution Network와 Partnership 계약을 확대하고 있습니다."

    assert collect_relationship_fragments(sentence) == []


def test_제목형_영문_일반_협의체는_실명_관계로_오인하지_않는다():
    sentence = "회사는 International Distribution Alliance와 Partnership 계약을 확대하고 있습니다."

    assert collect_relationship_fragments(sentence) == []


def test_한글_일반_스튜디오는_실명_관계로_오인하지_않는다():
    sentence = "회사는 글로벌콘텐츠스튜디오와 전략적 파트너십 계약을 체결했습니다."

    assert collect_relationship_fragments(sentence) == []


@pytest.mark.parametrize("name", ["Premier Commerce Collective", "차세대콘텐츠스튜디오"])
def test_한번만_나온_명칭처럼_보이는_일반어는_관계조각이_아니다(name: str):
    sentence = f"회사는 {name}와 전략적 파트너십 계약을 체결했습니다."

    assert collect_relationship_fragments(sentence) == []


def test_같은_일반문장을_복제해도_독립된_실명근거가_되지_않는다():
    sentence = "회사는 Premier Commerce Collective와 전략적 파트너십 계약을 체결했습니다."

    assert collect_relationship_fragments(f"{sentence} {sentence}") == []


def test_wrapper와_문장부호만_다른_복제도_독립된_실명근거가_아니다():
    body = "Premier Commerce Collective와 전략적 파트너십 계약을 체결했습니다"

    assert collect_relationship_fragments(f"원문: {body}. 사업내용 - {body}!") == []


def test_한_fragment_안의_서로_다른_Live_Nation_문장은_관계근거다():
    text = (
        "Live Nation은 글로벌 공연 인프라를 보유합니다. "
        "2023년 Live Nation과 전략적 파트너십을 체결했습니다."
    )

    result = collect_relationship_fragments(text)

    assert len(result) == 1
    assert "2023년 Live Nation" in result[0]


def test_재무주석의_관계문장은_사업내용으로_재라벨링하지_않는다():
    business = "2023년 TME와 전략적 파트너십을 체결해 글로벌 음원 유통망을 운영합니다."
    note = "주석에서 Onecead Co.와 대출 계약을 체결했습니다."

    scoped = business_scope(_filing(business, note))
    result, added = add_to({}, _filing(business, note))

    assert "TME" in scoped
    assert "Onecead" not in scoped
    assert added == 1
    assert "TME" in result[1]["원문"]
