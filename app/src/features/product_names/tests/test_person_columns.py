"""사람 이름 열은 이름 후보로도, 발췌로도 새지 않는다.

★ 왜 생겼나 (독립 검토 재현) — 사람 열 판별이 머리글 «정확 일치»만 봐서,
  「제품명 | 대표이사 성명」 같은 표에서 그 칸의 실명이 후보 발췌·작가
  프롬프트·부록까지 그대로 갔다.

★ 시험 자료의 사람 이름은 «가공 이름»이다. 실명을 시험에 박으면 지워도
  되돌아온다. 그래서 「금지 이름이 없다」가 아니라 «허용 칸만 남았다»로
  뒤집어 확인한다 — 새 열이 늘어도 이 방향이면 자동으로 걸린다.
"""

from __future__ import annotations

import pytest

from src.features.composer.logic import build_section_prompt
from src.features.product_names.logic import (
    _header_key,
    _is_person_header_key,
    collect_name_candidates,
    collect_name_candidates_from_tables,
)
from src.features.product_names.tables import FilingTable


#: 표에 넣을 «가공» 사람 이름. 어느 산출물에도 남으면 안 된다.
_PERSON_VALUES = ("가온한", "너울봄", "다래솔")

#: 남아도 되는 값 — 이름 칸과 그 설명 칸.
_ALLOWED_VALUES = ("범용서비스가", "범용서비스나", "범용서비스다", "설명 문구")

#: 사람 열로 봐야 하는 머리글. 정확 일치 + 복합 + 역할 어휘.
_PERSON_HEADERS = (
    "성명",
    "이름",
    "대표이사 성명",
    "대표자 성명",
    "등기임원 성명",
    "담당자 이름",
    "아티스트 본명",
    "실명",
    "대표이사",
    "담당자",
    "책임자명",
    "구성원명",
)

#: 사람 열이 «아닌» 이름 열. 여기까지 삼키면 이름이 통째로 사라진다.
_NAME_HEADERS = (
    "상품명",
    "회사명",
    "브랜드명",
    "제품명",
    "서비스명",
    "법인명",
    "계약명",
    "모델명",
    "펀드명",
    "카드명",
    "품목명",
    "부문명",
    "게임명",
    "타이틀명",
    "파이프라인명",
    "그룹명",
)


def _table(person_header: str) -> FilingTable:
    return FilingTable(
        title="가. 주요 제품 및 서비스",
        rows=(
            ("제품명", person_header),
            (_ALLOWED_VALUES[0], _PERSON_VALUES[0]),
            (_ALLOWED_VALUES[1], _PERSON_VALUES[1]),
        ),
    )


def _plain_text(person_header: str) -> str:
    return "\n".join(
        (
            "가. 주요 제품 및 서비스의 현황",
            f"제품명 | {person_header}",
            f"{_ALLOWED_VALUES[0]} | {_PERSON_VALUES[0]}",
            f"{_ALLOWED_VALUES[1]} | {_PERSON_VALUES[1]}",
            "",
            "나. 다른 절",
        )
    )


def _assert_no_person_value(candidates, *, where: str) -> None:
    """후보 → 조각 원문 → 작가 프롬프트 세 단계 모두에서 확인한다."""

    assert candidates, f"{where}: 후보가 아예 안 나왔다 — 이름 열까지 삼켰다"
    for candidate in candidates:
        # ① 후보 이름
        assert candidate.name in _ALLOWED_VALUES, (where, candidate.name)
        # ② 조각에 그대로 실리는 발췌
        for cell in candidate.excerpt.split("|"):
            assert cell.strip() in _ALLOWED_VALUES, (where, candidate.excerpt)

    # ③ 작가 프롬프트 — 조각 발췌가 여기까지 간다.
    from src.features.composer.port import CollectedFragment

    prompt = build_section_prompt(
        "가나다회사",
        "portfolio",
        tuple(
            CollectedFragment(
                fragment_id=str(index + 1),
                kind="dart_business_report",
                text=candidate.excerpt,
                location=candidate.location,
            )
            for index, candidate in enumerate(candidates)
        ),
        None,
    )
    for value in _PERSON_VALUES:
        assert value not in prompt, (where, value)


@pytest.mark.parametrize("person_header", _PERSON_HEADERS)
def test_표_경로에서_사람_열_값은_후보에도_발췌에도_프롬프트에도_없다(
    person_header: str,
) -> None:
    candidates = collect_name_candidates_from_tables(
        (_table(person_header),), source_kind="사업보고서"
    )

    _assert_no_person_value(candidates, where=f"표/{person_header}")


@pytest.mark.parametrize("person_header", _PERSON_HEADERS)
def test_평문_경로에서도_사람_열_값이_새지_않는다(person_header: str) -> None:
    """표 파서가 못 읽어 되돌아가는 경로다 — 여기도 같은 규칙을 지나야 한다."""

    candidates = collect_name_candidates(
        _plain_text(person_header), source_kind="사업보고서"
    )

    _assert_no_person_value(candidates, where=f"평문/{person_header}")


@pytest.mark.parametrize("person_header", _PERSON_HEADERS)
def test_사람_머리글은_규칙이_사람_열로_본다(person_header: str) -> None:
    assert _is_person_header_key(_header_key(person_header)), person_header


@pytest.mark.parametrize("name_header", _NAME_HEADERS)
def test_이름_열은_사람_열로_오해하지_않는다(name_header: str) -> None:
    """「상품명」처럼 ``명``으로 끝나는 진짜 이름 열까지 삼키면 안 된다.

    삼키면 그 열의 값이 후보에서도 발췌에서도 통째로 사라진다 — 이름 표를
    읽으려고 만든 파서가 이름을 지우는 셈이 된다.
    """

    assert not _is_person_header_key(_header_key(name_header)), name_header


@pytest.mark.parametrize("name_header", ("제품명", "모델명", "품목"))
def test_이름_열_값은_발췌에_그대로_남는다(name_header: str) -> None:
    """규칙 판정만이 아니라 실제 파서 결과에서도 이름이 살아 있어야 한다."""

    table = FilingTable(
        title="가. 주요 제품 및 서비스",
        rows=(
            (name_header, "주요내용"),
            (_ALLOWED_VALUES[0], _ALLOWED_VALUES[3]),
            (_ALLOWED_VALUES[1], _ALLOWED_VALUES[3]),
        ),
    )

    candidates = collect_name_candidates_from_tables(
        (table,), source_kind="사업보고서"
    )
    excerpts = " ".join(candidate.excerpt for candidate in candidates)

    assert _ALLOWED_VALUES[0] in excerpts, name_header
    assert _ALLOWED_VALUES[1] in excerpts, name_header


def test_사람_열이_없으면_발췌는_원문_줄_그대로다() -> None:
    """평문 경로의 기존 동작을 바꾸지 않았음을 못 박는다."""

    text = "\n".join(
        (
            "가. 주요 제품 및 서비스의 현황",
            "제품명 | 주요내용",
            f"{_ALLOWED_VALUES[0]} | {_ALLOWED_VALUES[3]}",
            "",
            "나. 다른 절",
        )
    )

    candidates = collect_name_candidates(text, source_kind="사업보고서")

    assert candidates
    assert candidates[0].excerpt == f"{_ALLOWED_VALUES[0]} | {_ALLOWED_VALUES[3]}"
