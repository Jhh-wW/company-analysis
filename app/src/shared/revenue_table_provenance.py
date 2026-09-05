"""매출 구성표의 원문 행과 공개 행을 잇는 정본 근거 계약.

``revenuemix`` 생산자와 ``composer`` 출고 검증기가 같은 계약을 쓰도록 이
작은 모듈에 모았다. 공개 숫자가 JSON 안에 한 번 더 적혀 있다는 사실만으로는
근거가 되지 않는다. 반드시 인용 조각 안의 실제 원문 행·범위·해시까지 다시
맞아야 한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Final, Literal, Optional, TypeAlias


RevenueAxis: TypeAlias = Literal["product", "region"]

REVENUE_AXIS_PRODUCT: Final[RevenueAxis] = "product"
REVENUE_AXIS_REGION: Final[RevenueAxis] = "region"
REVENUE_AXES: Final[frozenset[str]] = frozenset(
    {REVENUE_AXIS_PRODUCT, REVENUE_AXIS_REGION}
)

# 생산자와 AI 전 결속기가 표제·캡션에서 같은 축을 읽는다. 이 목록이 둘로
# 갈라지면 새 표제 하나를 추가할 때 추출기는 제품표로 읽고 검증기는 지역표로
# 읽는 식의 또 다른 배선 오류가 생긴다.
REVENUE_HEADS_BY_AXIS: Final[dict[RevenueAxis, tuple[str, ...]]] = {
    REVENUE_AXIS_PRODUCT: (
        "제품별 매출액",
        "품목별 매출액",
        "제품별 매출 실적",
        "사업부문별 매출액",
        "주요 제품 및 서비스의 현황",
    ),
    REVENUE_AXIS_REGION: (
        "지역별 매출액",
        "지역별 매출 실적",
        "매출지역별",
    ),
}
REVENUE_CAPTION_BY_AXIS: Final[dict[RevenueAxis, str]] = {
    REVENUE_AXIS_PRODUCT: "무엇을 팔아 번 돈인가 — 제품·서비스별 매출 비중",
    REVENUE_AXIS_REGION: "어디서 번 돈인가 — 지역별 매출 비중",
}
REVENUE_TABLE_SECTION_BY_AXIS: Final[dict[RevenueAxis, str]] = {
    REVENUE_AXIS_PRODUCT: "portfolio",
    REVENUE_AXIS_REGION: "business_model",
}

REVENUE_ROW_PROVENANCE_SCHEMA: Final[str] = "revenue-table-row-provenance-v2"
REVENUE_EXTRACTOR_NAME: Final[str] = "revenuemix.regex"
REVENUE_EXTRACTOR_VERSION: Final[str] = "3"
REVENUE_MAX_ROWS: Final[int] = 12
REVENUE_HEADERS: Final[tuple[str, str, str]] = (
    "구분",
    "매출액 (백만원)",
    "비중",
)
#: 금액 열이 쓸 수 있는 단위. **닫힌 목록**이다 — 여기 없는 단위가 나오면
#: 표를 만들지 않는다. 긴 것부터 적어야 「백만원」 안의 「원」을 먼저 물지 않는다.
#: ⚠️ 왜 닫혀 있나 — 열 이름이 「매출액 (백만원)」으로 굳어 있던 동안 삼성전자
#:   (억원)·진영 주석(천원) 표가 백만원으로 붙었다. 숫자는 원문 그대로라 맞지만
#:   독자는 100배로 읽는다. 환산은 «하지 않는다» — 단위를 못 읽으면 표를 뺀다.
REVENUE_UNIT_WORDS: Final[tuple[str, ...]] = ("백만원", "억원", "천원", "원")


def revenue_amount_header(unit: str) -> str:
    """단위 하나에 맞는 금액 열 이름. 「매출액 (억원)」처럼 만든다."""

    return f"매출액 ({unit})"


REVENUE_NAME_NOISE: Final[tuple[str, ...]] = (
    "매 출 액",
    "매출액",
    "비 중",
    "비중",
    "구 분",
    "구분",
    "품 목",
    "품목",
    "(단위 : 백만원)",
    "(단위: 백만원)",
    "단위 : 백만원",
    "연결재무제표 기준",
    "매 출 지 역",
    "매출지역",
    "고객과의 계약에서 생기는 수익",
)
REVENUE_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"([^\d%]{2,40}?)\s+(\d{1,3}(?:,\d{3})+|\d{4,})\s+"
    r"(\d{1,3}\.\d{1,2})\s*%"
)

#: 금액 열 이름으로 받아 주는 값 전부. 닫힌 목록에서 기계로 만든다.
REVENUE_AMOUNT_HEADERS: Final[frozenset[str]] = frozenset(
    revenue_amount_header(unit) for unit in REVENUE_UNIT_WORDS
)
_UNIT_BY_AMOUNT_HEADER: Final[dict[str, str]] = {
    revenue_amount_header(unit): unit for unit in REVENUE_UNIT_WORDS
}
#: 옛 경로(v1)가 쓰는 기본 열 이름은 백만원판이다. 이 등식이 깨지면 스위치를
#: 꺼도 열 이름이 달라진다.
assert REVENUE_HEADERS[1] == revenue_amount_header("백만원")

#: 「(단위 : 억원, %)」처럼 «단위»라고 적어 둔 자리.
_UNIT_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"단\s*위[^)\]]{0,14}")
#: 「(백만원)」처럼 머리말 칸 안에 괄호로만 적힌 자리.
_UNIT_PARENS_RE: Final[re.Pattern[str]] = re.compile(
    r"[(\[]\s*(백만원|억원|천원|원)\s*[)\]]"
)
_UNIT_WORD_RE: Final[re.Pattern[str]] = re.compile("|".join(REVENUE_UNIT_WORDS))


def revenue_units_in(text: str) -> tuple[str, ...]:
    """머리말에서 금액 단위를 읽는다. 나온 순서대로, 중복 없이.

    ★ 아무 데서나 「원」을 줍지 않는다 — 「단위」라고 적힌 자리 뒤 14자 안이나
      괄호로만 묶인 자리에서만 찾는다. 그러지 않으면 「원재료」·「지원」에서
      단위를 읽어낸다.
    ⚠️ 둘 이상이 서로 다르게 나오면 호출자가 표를 «버려야» 한다. 어느 쪽이
      맞는지 우리가 고르면 그 순간 지어내는 것이다.
    """

    found: list[str] = []
    for label in _UNIT_LABEL_RE.finditer(str(text)):
        # 한 자리에 「백만원, 천원」처럼 둘이 적혀 있으면 «둘 다» 주워야
        # 호출자가 엇갈림을 알아차리고 표를 버릴 수 있다.
        found.extend(word.group(0) for word in _UNIT_WORD_RE.finditer(label.group(0)))
    for parens in _UNIT_PARENS_RE.finditer(str(text)):
        found.append(parens.group(1))
    return tuple(dict.fromkeys(found))


def revenue_table_headers(unit: str) -> tuple[str, str, str]:
    """단위에 맞춘 표 열 이름 세 개. 구분·비중 열 이름은 바뀌지 않는다."""

    if unit not in REVENUE_UNIT_WORDS:
        raise ValueError("닫힌 목록에 없는 금액 단위입니다")
    return (REVENUE_HEADERS[0], revenue_amount_header(unit), REVENUE_HEADERS[2])

#: 매출표 v2 — 「이름 + 금액 + 비중」 한 행의 모양. v1(``REVENUE_ROW_RE``)과
#: **따로** 둔다. v1을 넓히면 스위치를 꺼도 옛 경로가 무는 행이 달라져
#: 「스위치 OFF는 지금과 같다」를 더는 증명할 수 없기 때문이다.
#:
#: v1과 다른 점은 세 가지뿐이다 (0단계 실측 ``stage0_data_map.md`` D-7).
#:   ① 이름 상한 40자 → 80자 — 카카오 둘째 행 이름이 실측 71자다. 40자면
#:      앞부분이 잘린 이름(「…모바일 및 PC 게임」)이 나온다.
#:   ② 음수 표기 ``△``·``▲``·``−``·``-`` 를 금액·비중 앞에 허용 — 삼성전자
#:      「기타 부문간 내부거래 제거 등 △301,146 △8.9%」.
#:   ③ ``%`` 기호를 선택으로 — 현대카드는 열 이름이 「구성비」이고 값에
#:      ``%``가 없다(「카드수익 17,936 44.8」).
#: 괄호 음수 ``(9,953)``는 «일부러» 받지 않는다. 검사판에서 이 표기를 쓰는
#: 표는 은행 자금조달표뿐인데, 그건 매출표가 아니라서 받으면 오탐이 된다.
REVENUE_ROW_SIGN_CHARS: Final[str] = "△▲▽▼-−"
_V2_SIGN: Final[str] = r"[△▲▽▼\-−]?"
REVENUE_ROW_RE_V2: Final[re.Pattern[str]] = re.compile(
    r"([^\d%]{2,80}?)\s+"
    rf"({_V2_SIGN}(?:\d{{1,3}}(?:,\d{{3}})+|\d+))\s+"
    rf"({_V2_SIGN}\d{{1,3}}\.\d{{1,2}})\s*%?"
)

#: 검증이 받아 주는 행 모양 후보. 생산자가 «어느 경로로 만들었든» 이 중
#: 하나로 정확히 다시 잘려야 근거로 인정한다. 순서가 곧 우선순위다.
REVENUE_ROW_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    REVENUE_ROW_RE,
    REVENUE_ROW_RE_V2,
)

#: 비중 열의 이름. 회사마다 「비 중」·「비율」·「구성비」로 갈린다(실측).
REVENUE_RATIO_HEAD_RE: Final[re.Pattern[str]] = re.compile(
    r"비\s*중|비\s*율|구\s*성\s*비"
)

#: 표제가 없어도 머리말·행 이름에서 축을 다시 읽기 위한 어휘.
#: ★ 왜 필요한가 — v2는 표를 «모양»으로 찾으므로 머리말이 알려진 표제로
#:   시작하지 않는다. 그래도 축(제품/지역)은 근거 안에서 독립으로 한 번 더
#:   확인돼야 캡션만 바꿔치기한 표가 통과하지 못한다.
REVENUE_AXIS_HINTS_BY_AXIS: Final[dict[RevenueAxis, tuple[str, ...]]] = {
    REVENUE_AXIS_PRODUCT: (
        "제품",
        "품목",
        "상품",
        "사업부문",
        "사업영역",
        "매출유형",
        "영업종류",
        "영업실적",
        "서비스",
        "부문",
    ),
    REVENUE_AXIS_REGION: (
        "매출지역",
        "지역",
        "국가",
        "내수",
        "수출",
        "국내",
    ),
}

#: 축 어휘를 찾을 범위(글자). 머리말과 첫 행까지만 본다 — 표 전체를 훑으면
#: 제품표 뒤쪽의 「국내」 한 단어에 지역표로 뒤집힌다.
REVENUE_AXIS_HINT_WINDOW: Final[int] = 300

_HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_TOTAL_NAMES: Final[frozenset[str]] = frozenset({"합계", "총계", "계"})
#: 「…합계」 꼴 합계 표지로 인정할 최대 길이. 「영업수익합계」(6자)가 실측
#: 상한이고, 이보다 길면 문장이 이름으로 들어온 것으로 본다.
_TOTAL_SUFFIX_MAX: Final[int] = 12
#: 비중 합이 100에서 벗어나도 되는 폭(%p). 사용자 결정 2026-09-05.
REVENUE_PERCENT_TOLERANCE: Final[Decimal] = Decimal("0.5")


def canonical_json(value: object) -> str:
    """근거 행의 바이트 정본(JSON)을 만든다."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_revenue_name(raw: str) -> str:
    """생산자와 검증기가 공유하는 매출 행 이름 정규화."""

    name = str(raw)
    # HTML 원문은 태그만 지우면 ``&nbsp;``가 글자 그대로 남는다(실측: 카카오
    # 합계 행이 「합 &nbsp; &nbsp; &nbsp; 계」). 공백으로 펴 두지 않으면
    # 합계 행을 합계로 알아보지 못한다.
    name = re.sub(r"&nbsp;|&#160;| ", " ", name)
    for noise in REVENUE_NAME_NOISE:
        name = name.replace(noise, " ")
    name = re.sub(r"\(주\s*\d+\)", " ", name)
    name = re.sub(
        r"20\d{2}\s*년|제\s*\d+\s*기|\(\s*(?:당|전|전 전)\s*기\s*\)",
        " ",
        name,
    )
    name = re.sub(r"[·\-–—]+\s*$", "", name)
    # 값이 없는 칸을 「-」로 채우는 표가 많다(전 사 실측). 그 「-」가 다음 행
    # 이름 앞에 붙어 「- - 기타영업수익」처럼 나오므로 앞쪽에서도 뗀다.
    name = re.sub(r"^[\s·\-–—−]+", "", name)
    return re.sub(r"\s+", " ", name).strip(" ,.()")


def is_revenue_total_name(value: object) -> bool:
    """합계 표지인지 *전체 이름*으로 판정한다.

    부분문자열 ``계``를 쓰면 기계·회계·설계를 합계로 오인하므로 공백을
    접은 뒤 닫힌 목록과 정확히 비교한다.
    """

    return re.sub(r"\s+", "", str(value)) in _TOTAL_NAMES


def is_revenue_total_name_v2(value: object) -> bool:
    """v2가 받는 합계 표지. v1 목록에 「…합계」·「…총계」 꼴을 더한다.

    ★ 왜 넓히나 — 현대카드 매출표의 합계 행 이름이 「영업수익합계」다(실측).
      닫힌 목록만 보면 이 표는 합계가 없는 표로 보여 통째로 버려진다.
    ⚠️ 「소계」는 여전히 합계가 아니다. ``계``로 끝나기만 하는 이름(회계·설계)도
      받지 않는다 — ``합계``·``총계`` 두 꼬리만 인정한다.
    """

    compact = re.sub(r"\s+", "", str(value))
    return bool(
        compact in _TOTAL_NAMES
        or (compact.endswith(("합계", "총계")) and len(compact) <= _TOTAL_SUFFIX_MAX)
    )


def revenue_signed_decimal(value: object) -> Decimal | None:
    """``△301,146``·``-8.9``처럼 부호가 글자인 값을 부호 있는 수로 읽는다.

    ★ 원문 표기는 «그대로» 셀에 남긴다. 이 함수는 검산(행 합 = 합계)에만 쓴다.
    """

    raw = str(value).strip().replace(",", "").removesuffix("%").strip()
    negative = bool(raw) and raw[0] in REVENUE_ROW_SIGN_CHARS
    if negative:
        raw = raw[1:].strip()
    if not raw:
        return None
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return -number if negative else number


def revenue_amounts_sum_to_total(
    amounts: Iterable[object], total: object
) -> bool:
    """행 금액의 합이 «반올림 없이» 합계 행과 같은지 본다.

    ★ 이것이 v2의 진짜 관문이다. 표제 목록 대신 이 검산이 「이 표가 매출
      구성표인가」를 판정한다. 한 행이라도 놓치면 합이 어긋나 표가 버려진다.
    """

    total_value = revenue_signed_decimal(total)
    if total_value is None:
        return False
    parsed: list[Decimal] = []
    for amount in amounts:
        value = revenue_signed_decimal(amount)
        if value is None:
            return False
        parsed.append(value)
    return bool(parsed) and sum(parsed, Decimal(0)) == total_value


def revenue_percent_total_is_complete_v2(values: Iterable[object]) -> bool:
    """음수 비중을 포함해 비중 합이 100에 닿는지 본다.

    ``displayed_percent_total_is_complete``는 음수를 아예 거절한다(v1 계약).
    삼성전자 「△8.9%」 같은 «부(-)의 비중»이 실재하므로 v2는 부호를 읽고
    표시 자릿수 반올림 여유와 ``REVENUE_PERCENT_TOLERANCE`` 중 큰 쪽을 쓴다.
    """

    parsed: list[Decimal] = []
    rounding_slack = Decimal(0)
    for value in values:
        number = revenue_signed_decimal(value)
        if number is None or abs(number) > 100:
            return False
        parsed.append(number)
        raw = str(value).strip().removesuffix("%")
        places = len(raw.partition(".")[2]) if "." in raw else 0
        rounding_slack += Decimal(1).scaleb(-places) / Decimal(2)
    if not parsed:
        return False
    difference = abs(sum(parsed, Decimal(0)) - Decimal(100))
    return difference <= max(rounding_slack, REVENUE_PERCENT_TOLERANCE)


def displayed_percent_total_is_complete(values: Iterable[object]) -> bool:
    """표시 자릿수의 반올림만으로 합계 100이 가능한지 판정한다."""

    parsed: list[Decimal] = []
    tolerance = Decimal(0)
    for value in values:
        raw = str(value).strip().replace(",", "").removesuffix("%")
        try:
            number = Decimal(raw)
        except (InvalidOperation, ValueError):
            return False
        if not number.is_finite() or number < 0 or number > 100:
            return False
        parsed.append(number)
        places = len(raw.partition(".")[2]) if "." in raw else 0
        tolerance += Decimal(1).scaleb(-places) / Decimal(2)
    if not parsed:
        return False
    difference = abs(sum(parsed, Decimal(0)) - Decimal(100))
    return difference == 0 or difference < tolerance


def revenue_table_evidence_identity(evidence: str) -> str:
    """한 표의 모든 행이 공유해야 하는 source+total 정본 신원."""

    try:
        payload = json.loads(evidence)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(payload, Mapping) or evidence != canonical_json(payload):
        return ""
    if not all(key in payload for key in ("schema", "extractor", "source", "table")):
        return ""
    return sha256_text(
        canonical_json(
            {
                "schema": payload["schema"],
                "extractor": payload["extractor"],
                "source": payload["source"],
                "table": payload["table"],
            }
        )
    )


def revenue_table_source_excerpt(evidence_rows: Sequence[str]) -> str:
    """동일한 한 표의 근거 행들에서 전용 cite용 최소 원문을 꺼낸다."""

    if not evidence_rows:
        return ""
    identities = {revenue_table_evidence_identity(value) for value in evidence_rows}
    if "" in identities or len(identities) != 1:
        return ""
    excerpts: set[str] = set()
    for evidence in evidence_rows:
        try:
            payload = json.loads(evidence)
            excerpt = payload["source"]["excerpt"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return ""
        if not isinstance(excerpt, str) or not excerpt:
            return ""
        excerpts.add(excerpt)
    return excerpts.pop() if len(excerpts) == 1 else ""


def _caption_matches_axis(axis: RevenueAxis, caption: str) -> bool:
    expected = REVENUE_CAPTION_BY_AXIS[axis]
    return re.fullmatch(
        rf"{re.escape(expected)}(?: \(20\d{{2}}년\))?",
        str(caption).strip(),
    ) is not None


def _text_axis(value: str) -> RevenueAxis | None:
    """원문 시작의 알려진 표제 하나에서 축을 읽는다.

    부분문자열 전체를 훑어 축을 정하면 목차나 다음 표제가 뒤에 있는 경우가
    다시 섞인다. 생산자가 보존한 header/excerpt는 현재 표제에서 정확히
    시작해야 하므로, 여기서도 오직 시작 위치의 표제만 인정한다.
    """

    matches = tuple(
        axis
        for axis, heads in REVENUE_HEADS_BY_AXIS.items()
        if any(value.startswith(head) for head in heads)
    )
    if len(matches) == 1:
        return matches[0]
    return _text_axis_by_hint(value) if not matches else None


def _text_axis_by_hint(value: str) -> RevenueAxis | None:
    """알려진 표제로 시작하지 않는 v2 머리말에서 축 어휘로 축을 읽는다.

    ★ 왜 필요한가 — v2는 표를 «모양»으로 찾으므로 머리말이 「제품별 매출액」
      같은 표제로 시작하지 않는다(실측: 삼성전자는 「(단위 : 억원, %) 부 문
      주요 제품 매출액 비중」). 그래도 축은 근거 안에서 한 번 더 확인돼야
      캡션만 바꿔치기한 표를 잡아낼 수 있다.
    ⚠️ 표 전체를 훑지 않는다. 머리말과 첫 행까지(``REVENUE_AXIS_HINT_WINDOW``)만
      본다 — 제품표 뒤쪽에 한 번 나오는 「국내」에 축이 뒤집히면 안 된다.
    ⚠️ 표기가 「매 출 지 역」처럼 글자마다 벌어져 있으므로 공백을 지우고 찾는다.
    """

    compact = re.sub(r"\s+", "", value[:REVENUE_AXIS_HINT_WINDOW])
    found: list[tuple[int, RevenueAxis]] = []
    for axis, hints in REVENUE_AXIS_HINTS_BY_AXIS.items():
        positions = [compact.find(hint) for hint in hints]
        hit = [position for position in positions if position >= 0]
        if hit:
            found.append((min(hit), axis))
    if not found:
        return None
    found.sort()
    if len(found) > 1 and found[0][0] == found[1][0]:
        return None                      # 같은 자리에서 두 축이 겹치면 포기한다
    return found[0][1]


def _header_axis_agrees(header: str, axis: RevenueAxis) -> bool:
    """머리말이 축을 «반박하지 않는지» 본다.

    ★ 왜 「같다」가 아니라 「반박하지 않는다」인가 — v2는 표를 모양으로
      찾으므로 머리말이 축을 말하지 않는 표가 있다(실측: 카카오 매출 실적
      머리말은 「구분 제31 (당)기 … 매출액 비중」뿐이고 축 어휘가 없다.
      「플랫폼 부문」·「콘텐츠 부문」은 첫 행에 있다).
      말하지 않는 것은 통과시키되, **다르게 말하면 거절한다.** 축은 인용
      조각(``excerpt``)에서 한 번 더 확인되므로 검증이 비지 않는다.
    """

    header_axis = _text_axis(header)
    return header_axis is None or header_axis == axis


def revenue_text_axis(value: str) -> Optional[RevenueAxis]:
    """머리말·인용 조각 한 덩어리에서 축(제품/지역)을 읽는다.

    생산자(``revenuemix``)와 검증기가 «같은 함수»로 축을 읽어야 한쪽만
    통과하는 표가 생기지 않는다.
    """

    return _text_axis(value)


def revenue_table_section_id(axis: object) -> str:
    """제품·지역 매출표 축이 단독 소유하는 장 id를 돌려준다."""

    if type(axis) is not str or axis not in REVENUE_TABLE_SECTION_BY_AXIS:
        raise ValueError("매출 구성표의 제품·지역 축을 확인할 수 없습니다")
    return REVENUE_TABLE_SECTION_BY_AXIS[axis]  # type: ignore[index]


def revenue_table_section_id_from_caption(caption: object) -> str:
    """검증된 공개 캡션에서 축을 읽어 같은 장 소유권 규칙을 적용한다."""

    if type(caption) is not str:
        raise ValueError("매출 구성표의 캡션이 문자열이 아닙니다")
    return revenue_table_section_id(revenue_text_axis(caption))


def revenue_table_axis_matches(
    *,
    axis: object,
    caption: object,
    evidence_rows: Sequence[str],
    cited_source_text: str = "",
) -> bool:
    """typed 축·캡션·머리말·인용 원문의 축이 모두 같은지 확인한다.

    ``revenuemix``가 만든 ``axis`` 문자열만 믿으면 누군가 캡션만 제품으로
    바꾼 지역 표가 통과한다. 반대로 캡션만 읽으면 지금 잡은 것처럼 추출 행이
    다른 축이어도 알 수 없다. 따라서 서로 독립인 네 자리를 AI 호출 전에
    맞춰 본다: raw transport의 축, 공개 캡션, evidence의 봉인 축·머리말,
    그리고 실제 cite에 들어갈 excerpt.
    """

    if type(axis) is not str or axis not in REVENUE_AXES:
        return False
    typed_axis: RevenueAxis = axis  # type: ignore[assignment]
    if type(caption) is not str or not _caption_matches_axis(typed_axis, caption):
        return False
    excerpt = revenue_table_source_excerpt(evidence_rows)
    if not excerpt or _text_axis(excerpt) != typed_axis:
        return False
    if cited_source_text and cited_source_text != excerpt:
        return False

    for evidence in evidence_rows:
        try:
            payload = json.loads(evidence)
            table = payload["table"]
            header = table["header"]["text"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return False
        if (
            not isinstance(table, Mapping)
            or table.get("axis") != typed_axis
            or not isinstance(header, str)
            or not _header_axis_agrees(header, typed_axis)
        ):
            return False
    return True


def _span_payload(raw_match: str, match_start: int, match_end: int) -> dict[str, object]:
    return {
        "value": raw_match[match_start:match_end],
        "start": match_start,
        "end": match_end,
    }


def build_revenue_row_evidence(
    *,
    filing_text: str,
    header_start: int,
    header_end: int,
    excerpt_start: int,
    excerpt_end: int,
    row_raw_match: str,
    row_start: int,
    row_end: int,
    row_field_spans: Mapping[str, tuple[int, int]],
    source_index: int,
    selected_index: int,
    public_row: Sequence[str],
    row_count: int,
    total_raw_match: str,
    total_start: int,
    total_end: int,
    total_field_spans: Mapping[str, tuple[int, int]],
    selection: str,
    axis: RevenueAxis,
    headers: Sequence[str] = REVENUE_HEADERS,
) -> str:
    """한 공개 행에 붙일 손실 없는 원문 근거 JSON을 만든다.

    ``headers``는 이 표가 실제로 쓰는 열 이름이다. 금액 열은 단위에 따라
    「매출액 (억원)」처럼 달라진다. 기본값은 옛 경로가 쓰던 백만원판이라
    스위치를 끈 결과는 글자 하나 달라지지 않는다.
    """

    excerpt = filing_text[excerpt_start:excerpt_end]
    row_fields = {
        name: _span_payload(row_raw_match, *row_field_spans[name])
        for name in ("name", "amount", "ratio")
    }
    total_fields = {
        name: _span_payload(total_raw_match, *total_field_spans[name])
        for name in ("name", "amount", "ratio")
    }
    payload = {
        "schema": REVENUE_ROW_PROVENANCE_SCHEMA,
        "extractor": {
            "name": REVENUE_EXTRACTOR_NAME,
            "version": REVENUE_EXTRACTOR_VERSION,
        },
        "source": {
            "filing_sha256": sha256_text(filing_text),
            "excerpt": excerpt,
            "start": excerpt_start,
            "end": excerpt_end,
            "sha256": sha256_text(excerpt),
        },
        "table": {
            "axis": axis,
            "complete": True,
            "row_count": row_count,
            "max_rows": REVENUE_MAX_ROWS,
            "header": {
                "text": filing_text[header_start:header_end],
                "start": header_start,
                "end": header_end,
                "excerpt_start": header_start - excerpt_start,
                "excerpt_end": header_end - excerpt_start,
                "sha256": sha256_text(filing_text[header_start:header_end]),
            },
            "total": {
                "raw_match": total_raw_match,
                "start": total_start,
                "end": total_end,
                "excerpt_start": total_start - excerpt_start,
                "excerpt_end": total_end - excerpt_start,
                "sha256": sha256_text(total_raw_match),
                "raw_fields": total_fields,
            },
        },
        "row": {
            "raw_match": row_raw_match,
            "start": row_start,
            "end": row_end,
            "excerpt_start": row_start - excerpt_start,
            "excerpt_end": row_end - excerpt_start,
            "sha256": sha256_text(row_raw_match),
            "raw_fields": row_fields,
            "selection": selection,
            "source_index": source_index,
            "selected_index": selected_index,
        },
        "public_fields": dict(zip(headers, (str(value) for value in public_row))),
    }
    return canonical_json(payload)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _field_value(fields: object, name: str, raw_match: str) -> str | None:
    if not isinstance(fields, Mapping) or set(fields) != {"name", "amount", "ratio"}:
        return None
    field = fields.get(name)
    if not isinstance(field, Mapping) or set(field) != {"value", "start", "end"}:
        return None
    start, end = _integer(field.get("start")), _integer(field.get("end"))
    value = field.get("value")
    if (
        start is None
        or end is None
        or start < 0
        or end < start
        or end > len(raw_match)
        or not isinstance(value, str)
        or raw_match[start:end] != value
    ):
        return None
    return value


def _raw_span_matches(
    payload: object,
    *,
    excerpt: str,
    excerpt_absolute_start: int,
) -> tuple[str, Mapping[str, object]] | None:
    if not isinstance(payload, Mapping):
        return None
    required = {
        "raw_match",
        "start",
        "end",
        "excerpt_start",
        "excerpt_end",
        "sha256",
        "raw_fields",
    }
    if not required.issubset(payload):
        return None
    raw_match = payload.get("raw_match")
    start, end = _integer(payload.get("start")), _integer(payload.get("end"))
    local_start = _integer(payload.get("excerpt_start"))
    local_end = _integer(payload.get("excerpt_end"))
    digest = payload.get("sha256")
    if (
        not isinstance(raw_match, str)
        or start is None
        or end is None
        or local_start is None
        or local_end is None
        or start != excerpt_absolute_start + local_start
        or end != excerpt_absolute_start + local_end
        or end - start != len(raw_match)
        or local_start < 0
        or local_end > len(excerpt)
        or excerpt[local_start:local_end] != raw_match
        or digest != sha256_text(raw_match)
    ):
        return None
    fields = payload.get("raw_fields")
    if not isinstance(fields, Mapping):
        return None
    if any(_field_value(fields, name, raw_match) is None for name in ("name", "amount", "ratio")):
        return None
    return raw_match, fields


def _header_span_matches(
    payload: object,
    *,
    excerpt: str,
    excerpt_absolute_start: int,
) -> bool:
    if not isinstance(payload, Mapping) or set(payload) != {
        "text",
        "start",
        "end",
        "excerpt_start",
        "excerpt_end",
        "sha256",
    }:
        return False
    text = payload.get("text")
    start, end = _integer(payload.get("start")), _integer(payload.get("end"))
    local_start = _integer(payload.get("excerpt_start"))
    local_end = _integer(payload.get("excerpt_end"))
    return bool(
        isinstance(text, str)
        and text
        and start is not None
        and end is not None
        and local_start is not None
        and local_end is not None
        and start == excerpt_absolute_start + local_start
        and end == excerpt_absolute_start + local_end
        and end - start == len(text)
        and local_start == 0
        and 0 <= local_end <= len(excerpt)
        and excerpt[local_start:local_end] == text
        and payload.get("sha256") == sha256_text(text)
        and REVENUE_RATIO_HEAD_RE.search(text) is not None
    )


def _amount_header_of(public_fields: object) -> Optional[str]:
    """공개 칸 이름에서 금액 열 이름을 꺼내되 «닫힌 목록»만 받는다.

    ⚠️ 여기서 아무 이름이나 받으면 「매출액 (조원)」처럼 없는 단위를 지어낸
      표가 통과한다. 단위는 ``REVENUE_UNIT_WORDS`` 네 가지뿐이다.
    """

    if not isinstance(public_fields, Mapping):
        return None
    names = set(public_fields)
    amount_names = names - {REVENUE_HEADERS[0], REVENUE_HEADERS[2]}
    if len(names) != 3 or len(amount_names) != 1:
        return None
    amount_header = amount_names.pop()
    return amount_header if amount_header in REVENUE_AMOUNT_HEADERS else None


def _unit_agrees(amount_header: str, header_text: str) -> bool:
    """열 이름이 말하는 단위가 «봉인된 머리말»이 말하는 단위와 같은지 본다.

    ★ 왜 기본값(백만원)은 그냥 통과시키나 — 옛 경로(v1)는 단위를 읽지 않고
      늘 백만원판 열 이름을 쓴다. 여기서 옛 표에도 단위 대조를 걸면 스위치를
      꺼도 통과·거절이 달라진다. 그래서 «단위를 스스로 밝힌 표»에만 건다.
    """

    if amount_header == REVENUE_HEADERS[1]:
        return True
    return revenue_units_in(header_text) == (_UNIT_BY_AMOUNT_HEADER[amount_header],)


def _row_fields_match_a_known_shape(
    raw_match: str, fields: Mapping[str, object]
) -> bool:
    """봉인된 칸 좌표가 «알려진 행 모양 하나»로 정확히 다시 잘리는지 본다.

    v1 표와 v2 표는 행 모양이 다르다(이름 상한·음수 부호·``%`` 유무).
    어느 쪽이든 «생산자가 적어 둔 칸 좌표»가 그 모양의 캡처 좌표와 글자
    단위로 같아야 한다 — 좌표를 다시 계산해 주지 않는다.
    """

    for pattern in REVENUE_ROW_PATTERNS:
        structural_match = pattern.fullmatch(raw_match)
        if structural_match is None:
            continue
        expected = {
            name: {
                "value": structural_match.group(index),
                "start": structural_match.start(index),
                "end": structural_match.end(index),
            }
            for index, name in enumerate(("name", "amount", "ratio"), start=1)
        }
        if all(fields.get(name) == span for name, span in expected.items()):
            return True
    return False


def revenue_row_evidence_matches(
    evidence: str,
    *,
    cited_source_text: str,
    filing_text: str | None = None,
    headers: Sequence[str],
    public_row: Sequence[str],
    raw_row: Sequence[str] | None = None,
    expected_selected_index: int | None = None,
    expected_row_count: int | None = None,
) -> bool:
    """공개 매출 행이 인용 조각과 실제 공시 원문에서 왔는지 재검증한다.

    ``filing_text``를 받은 생산 경계에서는 evidence 안의 지문만 서로 맞는
    순환 검증을 허용하지 않는다. 원문 전체 지문과 절대 범위를 다시 대조한다.
    저장본 검증처럼 원문 전체를 더는 갖고 있지 않은 호출자는 생략할 수 있다.
    """

    try:
        payload = json.loads(evidence)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(payload, Mapping) or evidence != canonical_json(payload):
        return False
    if set(payload) != {"schema", "extractor", "source", "table", "row", "public_fields"}:
        return False
    if payload.get("schema") != REVENUE_ROW_PROVENANCE_SCHEMA:
        return False
    extractor = payload.get("extractor")
    if extractor != {
        "name": REVENUE_EXTRACTOR_NAME,
        "version": REVENUE_EXTRACTOR_VERSION,
    }:
        return False
    source = payload.get("source")
    if not isinstance(source, Mapping) or set(source) != {
        "filing_sha256",
        "excerpt",
        "start",
        "end",
        "sha256",
    }:
        return False
    excerpt = source.get("excerpt")
    source_start, source_end = _integer(source.get("start")), _integer(source.get("end"))
    if (
        not isinstance(excerpt, str)
        or not excerpt
        or source_start is None
        or source_end is None
        or source_start < 0
        or source_end - source_start != len(excerpt)
        or source.get("sha256") != sha256_text(excerpt)
        or not isinstance(source.get("filing_sha256"), str)
        or _HEX_64_RE.fullmatch(str(source.get("filing_sha256"))) is None
        or excerpt not in str(cited_source_text)
    ):
        return False
    if filing_text is not None and (
        type(filing_text) is not str
        or source.get("filing_sha256") != sha256_text(filing_text)
        or source_end > len(filing_text)
        or filing_text[source_start:source_end] != excerpt
    ):
        return False
    row = payload.get("row")
    row_match = _raw_span_matches(
        row,
        excerpt=excerpt,
        excerpt_absolute_start=source_start,
    )
    if row_match is None or not isinstance(row, Mapping):
        return False
    raw_match, fields = row_match
    if not _row_fields_match_a_known_shape(raw_match, fields):
        return False
    source_index = _integer(row.get("source_index"))
    selected_index = _integer(row.get("selected_index"))
    selection = row.get("selection")
    if (
        source_index is None
        or selected_index is None
        or source_index < 0
        or selected_index < 0
        or selection not in {"first-current-period-pair", "explicit-total-row"}
        or (
            expected_selected_index is not None
            and selected_index != expected_selected_index
        )
    ):
        return False
    table = payload.get("table")
    if not isinstance(table, Mapping) or set(table) != {
        "axis",
        "complete",
        "row_count",
        "max_rows",
        "header",
        "total",
    }:
        return False
    row_count = _integer(table.get("row_count"))
    axis_header = table.get("header")
    axis_header_text = (
        axis_header.get("text") if isinstance(axis_header, Mapping) else ""
    )
    table_axis = table.get("axis")
    header_agrees = table_axis in REVENUE_AXES and _header_axis_agrees(
        str(axis_header_text), table_axis  # type: ignore[arg-type]
    )
    if (
        not header_agrees
        or _text_axis(excerpt) != table_axis
        or table.get("complete") is not True
        or table.get("max_rows") != REVENUE_MAX_ROWS
        or row_count is None
        or not 2 <= row_count <= REVENUE_MAX_ROWS
        or (expected_row_count is not None and row_count != expected_row_count)
        or selected_index > row_count
        or (selection == "explicit-total-row" and selected_index != row_count)
        or (selection != "explicit-total-row" and selected_index >= row_count)
    ):
        return False
    if not _header_span_matches(
        table.get("header"),
        excerpt=excerpt,
        excerpt_absolute_start=source_start,
    ):
        return False
    total_match = _raw_span_matches(
        table.get("total"),
        excerpt=excerpt,
        excerpt_absolute_start=source_start,
    )
    if total_match is None:
        return False
    header_payload = table.get("header")
    total_payload = table.get("total")
    if (
        not isinstance(header_payload, Mapping)
        or not isinstance(total_payload, Mapping)
        or _integer(header_payload.get("end")) is None
        or _integer(row.get("start")) is None
        or _integer(row.get("end")) is None
        or _integer(total_payload.get("start")) is None
        or _integer(total_payload.get("excerpt_end")) != len(excerpt)
        or int(header_payload["end"]) > int(row["start"])
        or (
            selection == "explicit-total-row"
            and (
                row.get("start") != total_payload.get("start")
                or row.get("end") != total_payload.get("end")
            )
        )
        or (
            selection != "explicit-total-row"
            and int(row["end"]) > int(total_payload["start"])
        )
    ):
        return False
    _, total_fields = total_match
    total_raw_match = str(table["total"]["raw_match"])
    if not _row_fields_match_a_known_shape(total_raw_match, total_fields):
        return False
    total_name = _field_value(total_fields, "name", total_raw_match)
    total_ratio = _field_value(total_fields, "ratio", total_raw_match)
    total_value = revenue_signed_decimal(total_ratio)
    if (
        total_value is None
        or not is_revenue_total_name_v2(normalize_revenue_name(str(total_name)))
        or total_value != Decimal(100)
    ):
        return False
    name = _field_value(fields, "name", raw_match)
    amount = _field_value(fields, "amount", raw_match)
    ratio = _field_value(fields, "ratio", raw_match)
    if name is None or amount is None or ratio is None:
        return False
    amount_header = _amount_header_of(payload.get("public_fields"))
    if amount_header is None or not _unit_agrees(amount_header, str(axis_header_text)):
        return False
    expected_fields = {
        REVENUE_HEADERS[0]: normalize_revenue_name(name),
        amount_header: amount,
        REVENUE_HEADERS[2]: f"{ratio}%",
    }
    if payload.get("public_fields") != expected_fields:
        return False
    normalized_headers = tuple(" ".join(str(value).split()) for value in headers)
    if (
        not normalized_headers
        or len(normalized_headers) != len(public_row)
        or any(header not in expected_fields for header in normalized_headers)
        or tuple(str(value) for value in public_row)
        != tuple(expected_fields[header] for header in normalized_headers)
    ):
        return False
    if raw_row is not None and tuple(str(value) for value in raw_row) != tuple(
        str(value) for value in public_row
    ):
        return False
    return True
