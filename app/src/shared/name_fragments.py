"""공시 이름 표 조각의 «종류 라벨»과 원문위치 표기 — 생산자·소비자 공용 정본.

생산자는 이름 표 파서(`features/product_names`)이고, 소비자는 3장 카드가 그
이름을 실제로 썼는지 보는 판정(`features/composer`)이다. 기능끼리 직접
import 하지 않는 것이 이 저장소의 경계 규칙이라, 두 곳이 같은 글자를 손으로
베껴 쓰면 한쪽이 바뀔 때 «조용히» 어긋난다 — 그러면 3장 판정이 아무것도
못 찾고도 초록불이 된다. 그래서 정본을 여기 한 곳에 두고 둘 다 읽는다.

이 모듈은 `src.core`·`src.features`를 import 하지 않는다(공용 계약 규칙).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


# ── 이름 종류 ────────────────────────────────────────────────────────
NAME_KIND_PRODUCT: Final[str] = "product"
NAME_KIND_BRAND: Final[str] = "brand"
NAME_KIND_SEGMENT: Final[str] = "segment"
NAME_KIND_SUBSIDIARY: Final[str] = "subsidiary"
NAME_KIND_CONTRACT: Final[str] = "contract"
NAME_KIND_IP: Final[str] = "ip"

#: 종류 → 사람이 읽는 라벨. 조각의 ``원문위치``에 이 글자가 실린다.
NAME_KIND_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        NAME_KIND_PRODUCT: "제품",
        NAME_KIND_BRAND: "브랜드",
        NAME_KIND_SEGMENT: "사업부문",
        NAME_KIND_SUBSIDIARY: "종속회사",
        NAME_KIND_CONTRACT: "주요 계약",
        NAME_KIND_IP: "대표 IP",
    }
)

NAME_KINDS: Final[frozenset[str]] = frozenset(NAME_KIND_LABELS)

#: 회사를 «대표하는 이름»으로 보는 종류.
#:
#: ★ 왜 사업부문이 없나 — 부문명은 지금도 카드 제목으로 잘 오른다. 그것만
#:   있는 카드는 「무엇을 파는 회사인가」를 말해 주지 않으므로 3장 이름
#:   요구의 «충족 근거»로 세지 않는다.
#: ★ 왜 종속회사·주요 계약이 없나 — 회사 이름과 계약 이름은 제품·서비스의
#:   이름이 아니라서 3장 카드의 「제품·서비스명」 자리에 맞지 않는다.
REPRESENTATIVE_NAME_KINDS: Final[tuple[str, ...]] = (
    NAME_KIND_IP,
    NAME_KIND_PRODUCT,
    NAME_KIND_BRAND,
)

REPRESENTATIVE_NAME_LABELS: Final[tuple[str, ...]] = tuple(
    NAME_KIND_LABELS[kind] for kind in REPRESENTATIVE_NAME_KINDS
)


# ── 원문위치 표기 ────────────────────────────────────────────────────
#: 이름 조각의 ``원문위치``는 `"{원문 안 위치} · {종류 라벨}: {이름}"`이다.
#:
#: ★ 왜 이름까지 싣나 — 조각의 ``원문``은 표 «한 행»이라 어느 칸이 우리가
#:   고른 이름인지 알 수 없다. 한 행이 「부문 | 품목 여럿 | 금액…」이고 고른
#:   이름이 그 안의 품목 하나인 경우가 실측에서 흔했다. 이름을 안 실으면
#:   작가도 후속 판정도 그 행에서 이름을 되찾을 수 없어 «추측»하게 된다.
NAME_LABEL_SEPARATOR: Final[str] = " · "
NAME_VALUE_SEPARATOR: Final[str] = ": "


def name_fragment_location(location: str, *, kind: str, name: str) -> str:
    """이름 조각의 ``원문위치`` 값을 만든다.

    Args:
        location: 원문 안에서의 위치(표 제목·행 번호 등).
        kind: 이름 종류. `NAME_KIND_LABELS`의 키여야 한다.
        name: 파서가 고른 이름.

    Returns:
        `"{location} · {라벨}: {name}"`.

    Raises:
        KeyError: 등록되지 않은 종류일 때.
    """

    label = NAME_KIND_LABELS[kind]
    return f"{location}{NAME_LABEL_SEPARATOR}{label}{NAME_VALUE_SEPARATOR}{name}"


def _label_and_name(location: str, labels: tuple[str, ...]) -> tuple[str, str]:
    text = str(location or "")
    for label in labels:
        marker = f"{NAME_LABEL_SEPARATOR}{label}{NAME_VALUE_SEPARATOR}"
        # 이름 안에 같은 표기가 또 있을 수 있으니 «마지막»을 기준으로 자른다.
        index = text.rfind(marker)
        if index != -1:
            return label, text[index + len(marker):].strip()
    return "", ""


def name_fragment_label_and_name(location: str) -> tuple[str, str]:
    """이름 조각의 ``원문위치``에서 종류 라벨과 이름을 되읽는다.

    이름 조각이 아니면 두 값 모두 빈 문자열이다.
    """

    return _label_and_name(location, tuple(NAME_KIND_LABELS.values()))


def representative_label_and_name(location: str) -> tuple[str, str]:
    """«대표 이름» 종류일 때만 라벨과 이름을 돌려준다.

    사업부문·종속회사·주요 계약은 3장 이름 요구의 충족 근거가 아니므로 여기서
    빈 값이 된다.
    """

    return _label_and_name(location, REPRESENTATIVE_NAME_LABELS)


__all__ = [
    "NAME_KINDS",
    "NAME_KIND_BRAND",
    "NAME_KIND_CONTRACT",
    "NAME_KIND_IP",
    "NAME_KIND_LABELS",
    "NAME_KIND_PRODUCT",
    "NAME_KIND_SEGMENT",
    "NAME_KIND_SUBSIDIARY",
    "NAME_LABEL_SEPARATOR",
    "NAME_VALUE_SEPARATOR",
    "REPRESENTATIVE_NAME_KINDS",
    "REPRESENTATIVE_NAME_LABELS",
    "name_fragment_label_and_name",
    "name_fragment_location",
    "representative_label_and_name",
]
