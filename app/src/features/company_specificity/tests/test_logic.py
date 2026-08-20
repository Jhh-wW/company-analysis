from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.features.company_specificity.logic import (
    assess_claim,
    filter_items,
    filter_prose_lines,
    source_kind_matches_sentence,
    verified_latin_names,
)


@dataclass(frozen=True)
class Item:
    block: str
    sentence: str
    fragment_id: int


def test_다른_엔터사에도_그대로_적용되는_글로벌_일반론은_최근동향이_아니다():
    sentence = "회사는 엔터테인먼트 업계의 글로벌화 트렌드를 인지하고 시스템을 강화하고 있다."

    assert not assess_claim("4-2", sentence, source_kind="사업내용", company="JYP").passed
    assert not assess_claim("4-2", sentence.replace("회사", "SM"), source_kind="사업내용", company="SM").passed


@pytest.mark.parametrize("cell", ["2", "4-2", "9"])
def test_영문_일반명사를_회사_고유명으로_오인하지_않는다(cell: str):
    sentence = "회사는 global music platform과 파트너십을 확대해 경쟁력을 강화했다."

    assert not assess_claim(cell, sentence, source_kind="사업내용", company="JYP").passed


@pytest.mark.parametrize("cell", ["2", "9"])
def test_소문자_영문_일반_유통망을_실명_근거로_오인하지_않는다(cell: str):
    sentence = "회사는 worldwide distribution network와 partnership 계약을 운영합니다."

    assert not assess_claim(cell, sentence, source_kind="사업내용", company="JYP").passed


@pytest.mark.parametrize("cell", ["2", "9"])
def test_제목형_영문_일반_협의체를_실명_근거로_오인하지_않는다(cell: str):
    sentence = "회사는 International Distribution Alliance와 partnership 계약을 운영합니다."

    assert not assess_claim(cell, sentence, source_kind="사업내용", company="JYP").passed


def test_일반_신인개발센터를_고유_경쟁력으로_오인하지_않는다():
    sentence = "회사는 신인개발센터를 운영해 연습생을 교육하고 있습니다."

    assert not assess_claim("2", sentence, source_kind="사업내용", company="JYP").passed


@pytest.mark.parametrize("org", ["아티스트개발본부", "글로벌사업팀", "콘텐츠운영실"])
def test_일반_조직명은_고유_경쟁력으로_오인하지_않는다(org: str):
    sentence = f"회사는 {org}을 운영해 사업 시스템을 강화하고 있습니다."

    assert not assess_claim("2", sentence, source_kind="사업내용", company="JYP").passed


@pytest.mark.parametrize("org", ["글로벌콘텐츠스튜디오", "아티스트레이블"])
@pytest.mark.parametrize("cell", ["2", "9"])
def test_일반_브랜드형_조직명도_고유_근거로_오인하지_않는다(org: str, cell: str):
    sentence = f"회사는 {org}와 유통 partnership 계약을 운영하고 있습니다."

    assert not assess_claim(cell, sentence, source_kind="사업내용", company="JYP").passed


@pytest.mark.parametrize("cell", ["4-2", "4-3"])
def test_최근_동향과_계획은_명시된_연도가_3년보다_오래되면_탈락한다(cell: str):
    sentence = "2018년 Live Nation과 유통 계약을 체결하고 글로벌 공연망을 확대할 계획이다."

    decision = assess_claim(
        cell, sentence, source_kind="사업내용", company="JYP", as_of_year=2026
    )

    assert not decision.passed
    assert "3년" in decision.reason


@pytest.mark.parametrize("cell", ["4-2", "4-3"])
def test_최근_3년_경계의_JYP_공식_파트너십은_통과한다(cell: str):
    sentence = "2023년 Live Nation과 전략적 파트너십을 체결하고 투어 협력을 확대할 계획이다."

    assert assess_claim(
        cell,
        sentence,
        source_kind="사업내용",
        company="JYP",
        as_of_year=2026,
        verified_names={"Live Nation"},
    ).passed


def test_오래된_계약과_최근_무관한_숫자를_섞어도_최근동향이_되지_않는다():
    sentence = "2018년 Live Nation과 계약을 체결했고, 2025년 매출이 증가했습니다."

    assert not assess_claim(
        "4-2", sentence, source_kind="사업내용", company="JYP", as_of_year=2026
    ).passed


def test_과거_파트너명과_최근_일반_확대를_섞어도_최근동향이_되지_않는다():
    sentence = "2018년 Live Nation과 계약을 체결했고, 2025년 매출을 확대했습니다."

    assert not assess_claim(
        "4-2", sentence, source_kind="사업내용", company="JYP", as_of_year=2026
    ).passed


@pytest.mark.parametrize("connector", ["했고 ", "했으며 "])
def test_쉼표_없이_과거계약과_최근숫자를_이어도_최근동향이_되지_않는다(connector: str):
    sentence = f"2018년 Live Nation 계약을 체결{connector}2025년 매출이 증가했습니다."

    assert not assess_claim(
        "4-2", sentence, source_kind="사업내용", company="JYP", as_of_year=2026
    ).passed


def test_최근이라는_말과_일반_확대만으로_최근동향이_되지_않는다():
    sentence = "Live Nation과 과거 계약을 체결했고, 최근 매출을 확대했습니다."

    assert not assess_claim(
        "4-2", sentence, source_kind="사업내용", company="JYP", as_of_year=2026
    ).passed


def test_최근_계획이_별도_절에_있으면_성장전략은_통과한다():
    sentence = "2018년 Live Nation과 계약했고, 2025년 Sony Music 유통망을 확대할 계획입니다."

    assert assess_claim(
        "4-3",
        sentence,
        source_kind="사업내용",
        company="JYP",
        as_of_year=2026,
        verified_names={"Live Nation", "Sony Music"},
    ).passed


def test_바로_앞_실명_파트너를_양사로_이어간_공식계획은_통과한다():
    sentence = (
        "현재 Live Nation과 파트너십을 체결했습니다. "
        "파트너십을 통해 양사의 공연 사업을 확대해갈 계획입니다."
    )

    assert assess_claim(
        "4-3",
        sentence,
        source_kind="사업내용",
        company="JYP",
        as_of_year=2026,
        verified_names={"Live Nation"},
    ).passed


def test_오래된_파트너를_양사로_이어도_최근계획_근거는_아니다():
    sentence = (
        "2018년 Live Nation과 파트너십을 체결했습니다. "
        "파트너십을 통해 양사의 공연 사업을 확대해갈 계획입니다."
    )

    assert not assess_claim(
        "4-3",
        sentence,
        source_kind="사업내용",
        company="JYP",
        as_of_year=2026,
        verified_names={"Live Nation"},
    ).passed


def test_미래_확대계획을_이미_실행한_최근동향으로_중복분류하지_않는다():
    sentence = "2025년 Sony Music 유통망을 확대할 계획입니다."

    assert not assess_claim(
        "4-2",
        sentence,
        source_kind="사업내용",
        company="JYP",
        as_of_year=2026,
        verified_names={"Sony Music"},
    ).passed
    assert assess_claim(
        "4-3",
        sentence,
        source_kind="사업내용",
        company="JYP",
        as_of_year=2026,
        verified_names={"Sony Music"},
    ).passed


@pytest.mark.parametrize(
    "sentence",
    [
        "2025년 Sony Music 유통망을 확대 예정입니다.",
        "2025년 Sony Music 유통망을 확대한다는 계획입니다.",
        "2025년 Sony Music 유통망을 확대하려고 합니다.",
    ],
)
def test_여러_미래형_표현은_최근동향이_아니고_성장계획이다(sentence: str):
    kwargs = {
        "source_kind": "사업내용",
        "company": "JYP",
        "as_of_year": 2026,
        "verified_names": {"Sony Music"},
    }

    assert not assess_claim("4-2", sentence, **kwargs).passed
    assert assess_claim("4-3", sentence, **kwargs).passed


@pytest.mark.parametrize(
    "sentence",
    [
        "2025년 Sony Music 유통망을 확대하기로 했다.",
        "2025년 Sony Music 유통망 확대를 검토 중이다.",
    ],
)
def test_결정과_검토도_실행이_아니라_계획으로_분류한다(sentence: str):
    kwargs = {
        "source_kind": "사업내용",
        "company": "JYP",
        "as_of_year": 2026,
        "verified_names": {"Sony Music"},
    }

    assert not assess_claim("4-2", sentence, **kwargs).passed
    assert assess_claim("4-3", sentence, **kwargs).passed


def test_외부전망_표현은_실행이나_공식계획으로_분류하지_않는다():
    sentence = "2025년 Sony Music 유통망을 확대할 것으로 보인다."
    kwargs = {
        "source_kind": "뉴스",
        "company": "JYP",
        "as_of_year": 2026,
        "verified_names": {"Sony Music"},
    }

    assert not assess_claim("4-2", sentence, **kwargs).passed
    assert not assess_claim("4-3", sentence, **kwargs).passed


@pytest.mark.parametrize(
    "sentence",
    [
        "2025년 Sony Music 유통망을 확대할 것으로 보입니다.",
        "2025년 Sony Music 유통망을 확대할 것으로 예상됩니다.",
        "2025년 Sony Music 유통망 확대 가능성이 있습니다.",
    ],
)
def test_존댓말_전망형도_실행이나_공식계획으로_분류하지_않는다(sentence: str):
    kwargs = {
        "source_kind": "뉴스",
        "company": "JYP",
        "as_of_year": 2026,
        "verified_names": {"Sony Music"},
    }

    assert not assess_claim("4-2", sentence, **kwargs).passed
    assert not assess_claim("4-3", sentence, **kwargs).passed


def test_검증된_실명은_더_긴_다른_이름의_부분문자열로_통과하지_않는다():
    sentence = "2025년 Live National Network와 유통 계약을 체결했습니다."

    assert not assess_claim(
        "4-2",
        sentence,
        source_kind="사업내용",
        company="JYP",
        as_of_year=2026,
        verified_names={"Live Nation"},
    ).passed


def test_동일한_문장_복제는_두개의_독립근거가_아니다():
    sentence = "Premier Commerce Collective와 유통 계약을 운영합니다."

    assert verified_latin_names([sentence, sentence]) == set()


def test_wrapper와_문장부호만_바꾼_복제는_독립근거가_아니다():
    body = "Premier Commerce Collective와 유통 계약을 운영합니다"

    assert verified_latin_names([f"원문: {body}.", f"사업내용 - {body}!"]) == set()


def test_한_fragment_안의_서로_다른_두_문장은_반복실명_근거다():
    text = (
        "Live Nation은 글로벌 공연 인프라를 보유합니다. "
        "2023년 Live Nation과 전략적 파트너십을 체결했습니다."
    )

    assert verified_latin_names([text]) == {"Live Nation"}


@pytest.mark.parametrize(
    "name",
    ["Worldwide Distribution Network", "Global Music Platform", "International Distribution Alliance"],
)
def test_반복된_영문_일반론도_실명으로_승격되지_않는다(name: str):
    assert verified_latin_names(
        [f"{name}와 계약합니다.", f"{name}를 운영합니다."]
    ) == set()


def test_자회사명_속_파트너는_외부파트너_관계표지가_아니다():
    sentence = "JYP파트너스는 2026년 첫 블라인드 펀드를 결성했다."

    assert assess_claim("4-2", sentence, source_kind="뉴스", company="JYP").passed
    assert not assess_claim("9", sentence, source_kind="뉴스", company="JYP").passed


@pytest.mark.parametrize("name", ["Premier Commerce Collective", "차세대콘텐츠스튜디오"])
@pytest.mark.parametrize("cell", ["2", "9"])
def test_한번만_나온_명칭처럼_보이는_일반어는_실명근거가_아니다(name: str, cell: str):
    sentence = f"회사는 {name}와 유통 계약을 운영하고 있습니다."

    assert not assess_claim(cell, sentence, source_kind="사업내용", company="JYP").passed


def test_JYP의_실제_행동과_고유단서가_있는_최근동향은_통과한다():
    sentence = "JYP파트너스는 2026년 430억원 규모의 첫 블라인드 펀드를 결성했다."

    assert assess_claim("4-2", sentence, source_kind="뉴스", company="JYP").passed
    assert not assess_claim("4-3", sentence, source_kind="뉴스", company="JYP").passed


def test_회사명과_업계공통_조직만_있는_경쟁력은_탈락하고_책임자실명은_단서가_된다():
    generic = "JYP엔터테인먼트는 캐스팅팀과 트레이닝팀으로 구성된 신인개발 조직을 갖추고 있다."
    specific = "선발된 연습생은 박진영 PD가 총괄하는 제작 시스템과 1:1 트레이닝을 거친다."

    identity = "JYP 제이와이피 JYP Entertainment"
    assert not assess_claim("2", generic, source_kind="사업내용", company=identity).passed
    assert assess_claim("2", specific, source_kind="사업내용", company=identity).passed


@pytest.mark.parametrize("cell", ["2", "4-2"])
def test_일대일_훈련_숫자만으로_회사고유_근거가_되지는_않는다(cell: str):
    sentence = "회사는 캐스팅팀과 트레이닝팀을 운영하며 1:1 맞춤형 트레이닝을 제공한다."

    assert not assess_claim(
        cell, sentence, source_kind="사업내용", company="JYP", as_of_year=2026
    ).passed


def test_날짜와_익명_글로벌파트너만으로_파트너구조를_채우지_않는다():
    sentence = "2025년 회사는 글로벌 파트너와 유통 협업을 체결했다."

    assert not assess_claim("9", sentence, source_kind="사업내용", company="JYP").passed


def test_날짜없는_옛계약은_최근동향이_아니지만_파트너구조에는_쓸수있다():
    sentence = "Sony Music과 음원 유통 파트너십을 체결해 해외 유통망을 운영한다."

    assert not assess_claim(
        "4-2",
        sentence,
        source_kind="사업내용",
        company="JYP",
        as_of_year=2026,
        verified_names={"Sony Music"},
    ).passed
    assert assess_claim(
        "9",
        sentence,
        source_kind="사업내용",
        company="JYP",
        verified_names={"Sony Music"},
    ).passed


def test_단순_회원선정은_성장전략으로_오배치하지_않는다():
    sentence = "JYP 소속 20인이 미국 레코딩 아카데미 회원으로 선정되었다."
    assert not assess_claim("4-3", sentence, source_kind="뉴스", company="JYP").passed


def test_공식_파트너_실명과_관계가_함께_있으면_파트너구조다():
    sentence = "미국 음반 유통은 Republic Records와 계약하고 공연은 Live Nation과 협력한다."
    assert assess_claim(
        "9",
        sentence,
        source_kind="사업내용",
        company="JYP",
        verified_names={"Republic Records", "Live Nation"},
    ).passed


def test_재무_라벨이_붙은_훈련문장은_AI_후보에서_제외한다():
    sentence = "캐스팅팀은 연습생에게 보컬과 안무 트레이닝을 제공한다."
    assert not source_kind_matches_sentence("재무", sentence)
    assert source_kind_matches_sentence(
        "재무", "2025년 매출액은 821,850,000,000원이고 영업이익은 155,250,000,000원이다."
    )


def test_AI가_고른_일반론과_출처종류_오배치를_코드가_함께_버린다():
    items = [
        Item("4-2", "회사는 글로벌화 트렌드에 맞춰 시스템을 강화하고 있다.", 1),
        Item("2", "박진영 PD의 제작 시스템과 1:1 트레이닝 조직을 운영한다.", 2),
    ]
    frags = {1: {"종류": "사업내용"}, 2: {"종류": "재무"}}

    kept, rejected, _score = filter_items(items, frags, company="JYP")

    assert kept == []
    assert len(rejected) == 2


def test_소송조각을_경쟁력이나_동향에_오배치하지_않는다():
    item = Item("4-2", "2025년 JYP파트너스가 430억원 규모의 펀드를 결성했다.", 8)
    kept, rejected, _score = filter_items(
        [item],
        {8: {"종류": "소송·분쟁"}},
        company="JYP",
        allowed_sources={"4-2": {"MD&A", "연구개발", "뉴스", "홈페이지"}},
    )
    assert kept == []
    assert rejected[0][1].reason == "이 항목에서 허용하지 않는 출처 종류"


def test_작가가_근거의_고유명사를_지운_일반론은_표시용글에서_버린다():
    evidence = [("TME와 음반 유통 계약을 체결했다.", "조각 2·사업내용")]
    prose = [
        ("회사는 글로벌 유통 경쟁력을 강화했다.", "조각 2·사업내용"),
        ("TME와의 유통 계약으로 해외 유통망을 확보했다.", "조각 2·사업내용"),
    ]
    assert filter_prose_lines("2", prose, evidence, company="JYP") == [prose[1]]
