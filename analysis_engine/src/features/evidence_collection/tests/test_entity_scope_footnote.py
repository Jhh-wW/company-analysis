"""관계법인 회계범위 각주 운반 — 같은 문서·같은 주석·같은 법인만 원문 그대로 싣는다.

모든 원문은 legacy 평문화(태그→공백)를 흉내 낸 익명 합성 문자열이다.
"""

from __future__ import annotations

import re

from features.evidence_collection import constants as c
from features.evidence_collection.entity_scope_footnote import (
    add_entity_scope_footnotes,
    find_entity_scope_footnotes,
)

_KIND = "관계법인 회계범위 주석"
_ANCHOR_KIND = "특수관계자"

_RELATED_PARTY = (
    "18. 특수관계자 등 거래 (1) 당기말 현재 회사의 특수관계자 내역은 다음과 같습니다. "
    "구분 특수관계자명 종속기업 Alpha Global Holdings (2) 당기 중 주요 거래내역은 다음과 "
    "같습니다. (단위 : 천원) 구분 특수관계자 당기 전기 금융수익 영업비용 영업비용 종속기업 "
    "Alpha Global Holdings 1,000 2,000 300 (3) 자금거래 대여 대여 종속기업 "
    "Alpha Global Holdings 5,000 4,000"
)

_INVESTMENT_NOTE = (
    "5. 매도가능증권 당기말 및 전기말 현재 매도가능증권의 내역은 다음과 같습니다. "
    "(단위 : 천원) 구분 당기말 전기말 지분률 취득원가 장부금액 지분률 취득원가 장부금액 "
    "Alpha Global Holdings(*) 100% 40,000 40,000 100% 40,000 40,000 "
    "(*) 일반기업회계기준 경과규정에 따라 종속기업에서 제외 되었습니다. 또한, 중소기업 "
    "회계처리 특례를 적용하여 지분법을 적용하지 아니하고 취득원가를 장부금액으로 계상하고 "
    "있습니다. "
)


def _filing(*notes: str) -> str:
    return (
        "감사보고서 본문 앞부분입니다. "
        + " ".join(notes)
        + " 6. 유형자산 당기 중 유형자산의 증감내역은 다음과 같습니다. "
        + _RELATED_PARTY
        + " 19. 우발사항 없음."
    )


def _frags() -> dict[int, dict[str, str]]:
    return {
        1: {"종류": "사업내용", "원문": "회사는 소프트웨어를 개발합니다." * 10},
        4: {"종류": _ANCHOR_KIND, "원문": _RELATED_PARTY},
    }


def _add(filing_text: str, frags: dict[int, dict[str, str]] | None = None):
    return add_entity_scope_footnotes(
        frags if frags is not None else _frags(),
        filing_text,
        kind=_KIND,
        anchor_kinds=(_ANCHOR_KIND,),
        max_chars=1_200,
    )


# ── 살릴 것 ────────────────────────────────────────────────


def test_같은_법인의_제외_각주를_주석_구간_원문_그대로_운반한다() -> None:
    filing_text = _filing(_INVESTMENT_NOTE)
    original = _frags()

    result, added = _add(filing_text, original)

    assert added == 1
    new = result[5]
    assert new["종류"] == _KIND
    start, end = (int(value) for value in re.findall(r"\d+", new["원문위치"]))
    assert filing_text[start:end] == new["원문"]
    assert new["원문"].startswith("5. 매도가능증권")
    assert "Alpha Global Holdings(*)" in new["원문"]
    assert new["원문"].endswith("계상하고 있습니다.")
    # 거래 사실(관계자 조각)은 한 글자도 바꾸지 않는다 — 각주를 이어 붙이지 않는다.
    assert result[4] == original[4]
    assert result[1] == original[1]


def test_같은_구간은_두_번_운반하지_않는다() -> None:
    filing_text = _filing(_INVESTMENT_NOTE)
    once, first = _add(filing_text)
    twice, second = _add(filing_text, once)

    assert (first, second) == (1, 0)
    assert twice == once


def test_한_표에_다른_법인_각주가_섞여도_원문_구간을_합성하지_않는다() -> None:
    note = (
        "5. 매도가능증권 (단위 : 천원) 구분 지분률 장부금액 Alpha Global Holdings(*1) 100% "
        "40,000 Beta Trading Limited(*2) 30% 9,000 (*1) 경과규정에 따라 종속기업에서 제외 "
        "되었습니다. (*2) 지분법을 적용하지 아니하고 취득원가로 계상합니다. "
    )
    filing_text = _filing(note)

    footnotes = find_entity_scope_footnotes(filing_text, [_RELATED_PARTY])

    assert len(footnotes) == 1
    footnote = footnotes[0]
    assert footnote.entity_label == "Alpha Global Holdings"
    assert filing_text[footnote.start:footnote.end] == footnote.text
    # 관계자 조각에 없는 법인(Beta)의 각주 정의에서는 구간을 따로 만들지 않는다.
    assert footnote.text.endswith("종속기업에서 제외 되었습니다.")


def test_같은_주석_같은_표식_단일표에서_정상_운반한다() -> None:
    """같은 표 안에서 한 번만 쓰인 (*)는 기존 양성과 같이 정상 운반한다."""

    note = (
        "5. 매도가능증권 당기말 현재 내역입니다. "
        "(단위 : 천원) 구분 장부금액 Alpha Global Holdings(*) 40,000 "
        "(*) 일반기업회계기준 경과규정에 따라 종속기업에서 제외 되었습니다. "
    )
    filing_text = _filing(note)

    footnotes = find_entity_scope_footnotes(filing_text, [_RELATED_PARTY])

    assert len(footnotes) == 1
    assert footnotes[0].entity_label == "Alpha Global Holdings"
    assert "종속기업에서 제외" in footnotes[0].text


# ── 막을 것 ────────────────────────────────────────────────


def test_같은_주석_다른_소표에서_표식이_재사용되면_뒤_정의가_앞_행을_끌지_않는다() -> None:
    """같은 5번 주석 안에서 (1)당기말 Alpha(*)와 (2)전기말 Beta(*)가 각각 다른
    (*) 정의를 갖는다. Beta 정의(제외)가 Alpha 행까지 순회해 Alpha 앵커로
    운반하면 안 된다."""

    note = (
        "5. 매도가능증권 (1) 당기말 현재 매도가능증권의 내역은 다음과 같습니다. "
        "(단위 : 천원) 구분 장부금액 Alpha Global Holdings(*) 40,000 "
        "(*) 당기 중 신규 취득하였습니다. "
        "(2) 전기말 현재 매도가능증권의 내역은 다음과 같습니다. "
        "(단위 : 천원) 구분 장부금액 Beta Trading Limited(*) 30,000 "
        "(*) 일반기업회계기준 경과규정에 따라 종속기업에서 제외 되었습니다. "
    )
    filing_text = _filing(note)

    footnotes = find_entity_scope_footnotes(filing_text, [_RELATED_PARTY])

    alpha_found = [f for f in footnotes if f.entity_label == "Alpha Global Holdings"]
    assert alpha_found == [], "뒤 소표 정의가 앞 소표 Alpha 행을 끌면 안 된다"


def test_같은_주석_재사용_표식에서_직접_귀속_행만_운반한다() -> None:
    """같은 5번 주석의 (2)전기말 Beta(*)에 직접 귀속된 제외 각주만 운반한다.
    Beta가 관계자 조각에 있을 때만 운반 가능."""

    beta_anchor = _RELATED_PARTY.replace("Alpha Global Holdings", "Beta Trading Limited")
    note = (
        "5. 매도가능증권 (1) 당기말 현재 매도가능증권의 내역은 다음과 같습니다. "
        "(단위 : 천원) 구분 장부금액 Alpha Global Holdings(*) 40,000 "
        "(*) 당기 중 신규 취득하였습니다. "
        "(2) 전기말 현재 매도가능증권의 내역은 다음과 같습니다. "
        "(단위 : 천원) 구분 장부금액 Beta Trading Limited(*) 30,000 "
        "(*) 일반기업회계기준 경과규정에 따라 종속기업에서 제외 되었습니다. "
    )
    filing_text = _filing(note)

    footnotes = find_entity_scope_footnotes(filing_text, [beta_anchor])

    assert len(footnotes) == 1
    footnote = footnotes[0]
    assert footnote.entity_label == "Beta Trading Limited"
    assert "종속기업에서 제외" in footnote.text
    assert filing_text[footnote.start:footnote.end] == footnote.text
    # 당기 거래 앵커로 결속해도 운반 원문은 전기말 표제를 잃지 않는다(현재로 승격 금지).
    period_at = footnote.text.find("(2) 전기말")
    assert 0 <= period_at < footnote.text.index("Beta Trading Limited(*)")


# ── 긴 주석: 기간 표제 보존·상한 초과 보류 ─────────────────────

#: 주석 전체를 상한(1,200자) 밖으로 미는 익명 표 행 — 기간 낱말·소표 머리·표식이 없다.
_FILLER_ROWS = " ".join(f"기타투자{index:03d} 1,000" for index in range(120))
_EXCLUSION = "(*) 일반기업회계기준 경과규정에 따라 종속기업에서 제외되었습니다. "
_ALPHA_ROW = "(단위 : 천원) 구분 장부금액 Alpha Global Holdings(*) 40,000 "


def test_긴_주석은_행이_속한_소표의_기간_머리부터_운반한다() -> None:
    """주석이 상한을 넘어도 행 이름부터 자르지 않고 「(2) 전기말 내역」을 함께 싣는다."""

    note = (
        "5. 매도가능증권 (1) 당기말 내역 (단위 : 천원) 구분 장부금액 "
        + _FILLER_ROWS
        + " (2) 전기말 내역 "
        + _ALPHA_ROW
        + _EXCLUSION
    )
    filing_text = _filing(note)
    original = _frags()

    result, added = _add(filing_text, original)

    assert added == 1
    new = result[5]
    start, end = (int(value) for value in re.findall(r"\d+", new["원문위치"]))
    assert filing_text[start:end] == new["원문"]
    assert len(new["원문"]) <= 1_200
    assert new["원문"].startswith("(2) 전기말 내역")
    assert new["원문"].endswith("종속기업에서 제외되었습니다.")
    # 당기 거래 사실(관계자 조각)은 그대로 남는다.
    assert result[4] == original[4]


def test_기간_표제가_없는_긴_주석은_행이_속한_소표_머리부터_운반한다() -> None:
    note = (
        "5. 매도가능증권 내역은 다음과 같습니다. (1) 국내 (단위 : 천원) 구분 장부금액 "
        + _FILLER_ROWS
        + " (2) 해외 "
        + _ALPHA_ROW
        + _EXCLUSION
    )

    footnotes = find_entity_scope_footnotes(_filing(note), [_RELATED_PARTY])

    assert len(footnotes) == 1
    assert footnotes[0].text.startswith("(2) 해외")


def test_기간_표제가_소표_밖에만_있으면_소표부터_운반하지_않는다() -> None:
    """「전기말 현재」가 주석 머리에만 있으면 (2) 소표만으로는 기간을 알 수 없다."""

    note = (
        "5. 매도가능증권 전기말 현재 내역은 다음과 같습니다. (1) 국내 (단위 : 천원) 구분 장부금액 "
        + _FILLER_ROWS
        + " (2) 해외 "
        + _ALPHA_ROW
        + _EXCLUSION
    )

    assert find_entity_scope_footnotes(_filing(note), [_RELATED_PARTY]) == ()


def test_소표_구간_자체가_상한을_넘으면_행부터_잘라_운반하지_않는다() -> None:
    note = (
        "5. 매도가능증권 (1) 전기말 내역 (단위 : 천원) 구분 장부금액 "
        + _FILLER_ROWS
        + " Alpha Global Holdings(*) 40,000 "
        + _EXCLUSION
    )

    result, added = _add(_filing(note))

    assert added == 0
    assert result == _frags()


def _amount_note(beta_amount: str, filler: str = "") -> str:
    """실제 「(2) 전기말 내역」 소표 안에서 Alpha 바로 앞 Beta 행 금액만 바꿔 끼운다."""

    return (
        "5. 매도가능증권 (1) 당기말 내역 (단위 : 천원) 구분 장부금액 "
        + _FILLER_ROWS
        + " (2) 전기말 내역 (단위 : 천원) 구분 장부금액 "
        + filler
        + f"Beta Trading Limited {beta_amount} Alpha Global Holdings(*) 40,000 "
        + _EXCLUSION
    )


def test_앞_행의_괄호_음수_금액을_소표_머리로_확정하지_않고_기간_소표부터_운반한다() -> None:
    """Beta 금액이 12 → (12)로만 바뀐 대칭. Alpha의 표·기간·각주는 같으므로 결과도 같다."""

    carried: dict[str, str] = {}
    for amount in ("12", "(12)"):
        filing_text = _filing(_amount_note(amount))
        footnotes = find_entity_scope_footnotes(filing_text, [_RELATED_PARTY])
        assert len(footnotes) == 1, amount
        footnote = footnotes[0]
        assert footnote.entity_label == "Alpha Global Holdings"
        assert filing_text[footnote.start:footnote.end] == footnote.text
        assert footnote.text.startswith("(2) 전기말 내역"), amount
        assert footnote.text.endswith("종속기업에서 제외되었습니다.")
        carried[amount] = footnote.text
    assert carried["(12)"] == carried["12"].replace(" 12 ", " (12) ")


def test_괄호_음수_금액_앞의_기간_소표가_상한_밖이면_본문을_넓히지_않고_보류한다() -> None:
    """더 앞 후보 검토는 상한 안에서만 — 기간 소표까지 1,200자를 넘으면 운반하지 않는다."""

    note = _amount_note("(12)", filler=_FILLER_ROWS + " ")
    assert find_entity_scope_footnotes(_filing(note), [_RELATED_PARTY]) == ()


def test_소표_머리가_없는_긴_주석은_행부터_잘라_운반하지_않는다() -> None:
    note = (
        "5. 매도가능증권 전기말 현재 내역입니다. (단위 : 천원) 구분 장부금액 "
        + _FILLER_ROWS
        + " Alpha Global Holdings(*) 40,000 "
        + _EXCLUSION
    )

    assert find_entity_scope_footnotes(_filing(note), [_RELATED_PARTY]) == ()


def test_관계자_조각에_없는_법인의_각주는_운반하지_않는다() -> None:
    note = _INVESTMENT_NOTE.replace("Alpha Global Holdings", "Beta Trading Limited")
    _result, added = _add(_filing(note))
    assert added == 0


def test_이름_꼬리만_같은_다른_법인에는_각주를_붙이지_않는다() -> None:
    anchor = _RELATED_PARTY.replace("Alpha Global Holdings", "Gamma Japan Co., Ltd.")
    note = _INVESTMENT_NOTE.replace("Alpha Global Holdings", "Delta Japan Co., Ltd.")
    assert find_entity_scope_footnotes(_filing(note), [anchor]) == ()


def test_예정된_제외는_현재_범위_제한으로_운반하지_않는다() -> None:
    note = (
        "5. 종속기업투자 (단위 : 천원) 구분 지분율 장부금액 Alpha Global Holdings(*) 100% "
        "40,000 (*) 청산 절차가 진행 중이며 차기에 종속기업에서 제외될 예정입니다. "
    )
    _result, added = _add(_filing(note))
    assert added == 0


def test_범위_제한이_아닌_각주는_운반하지_않는다() -> None:
    note = (
        "5. 종속기업투자 (단위 : 천원) 구분 지분율 장부금액 Alpha Global Holdings(*) 100% "
        "40,000 (*) 당기 중 신규 설립되었습니다. "
    )
    _result, added = _add(_filing(note))
    assert added == 0


def test_다른_주석의_각주_정의는_행과_결속하지_않는다() -> None:
    """행은 5번 주석, 같은 표식의 제외 정의는 7번 주석(다른 표) — 전이 금지."""

    note = (
        "5. 매도가능증권 (단위 : 천원) 구분 장부금액 Alpha Global Holdings(*) 40,000 "
        "7. 기타투자 (단위 : 천원) 구분 장부금액 Omega Partners(*) 1,000 "
        "(*) 경과규정에 따라 종속기업에서 제외 되었습니다. "
    )
    assert find_entity_scope_footnotes(_filing(note), [_RELATED_PARTY]) == ()


def test_표식이_다르면_결속하지_않는다() -> None:
    note = _INVESTMENT_NOTE.replace("Alpha Global Holdings(*)", "Alpha Global Holdings(*1)").replace(
        "(*) 일반", "(*2) 일반"
    )
    assert find_entity_scope_footnotes(_filing(note), [_RELATED_PARTY]) == ()


def test_관계자_조각이_없으면_아무것도_더하지_않는다() -> None:
    frags = {1: {"종류": "사업내용", "원문": _RELATED_PARTY}}
    result, added = _add(_filing(_INVESTMENT_NOTE), frags)
    assert added == 0
    assert result == frags


def test_글자_상한을_넘는_구간은_운반하지_않는다() -> None:
    result, added = add_entity_scope_footnotes(
        _frags(),
        _filing(_INVESTMENT_NOTE),
        kind=_KIND,
        anchor_kinds=(_ANCHOR_KIND,),
        max_chars=40,
    )
    assert added == 0
    assert result == _frags()


def test_종류_이름이_비면_아무것도_더하지_않는다() -> None:
    _result, added = add_entity_scope_footnotes(
        _frags(), _filing(_INVESTMENT_NOTE), kind=" ", anchor_kinds=(_ANCHOR_KIND,)
    )
    assert added == 0


def test_기간_표제_판별은_기간_낱말과_날짜만_잡는다() -> None:
    pattern = c.ENTITY_FOOTNOTE_PERIOD_LABEL_PATTERN
    for text in ("(2) 전기말 내역", "당기말현재", "당기 중", "기초 잔액", "2024년 12월", "2024.12.31"):
        assert pattern.search(text), text
    for text in ("당기손익-공정가치", "전기요금", "기초자산", "40,000 1,000"):
        assert pattern.search(text) is None, text


def test_원문위치는_legacy_평문_문자_표기를_쓴다() -> None:
    assert c.ENTITY_FOOTNOTE_LOCATION_TEMPLATE.format(start=1, end=2) == "평문 문자 1-2"
