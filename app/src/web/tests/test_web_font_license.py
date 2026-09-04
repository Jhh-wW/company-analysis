"""웹으로 내보내는 글꼴의 사용 허가 원문이 배포물과 함께 남는지 본다.

OFL(SIL Open Font License)은 글꼴 파일을 다시 배포할 때 허가 원문과 저작권 줄을
함께 두라고 요구한다. 정적 폴더에서 이 파일이 지워지거나, 허가 원문이 다루지 않는
제3의 글꼴이 몰래 들어오면 배포가 허가 범위를 벗어난다. 화면 시험은 글꼴이 보이는지만
보므로 그 사고를 잡지 못한다. 그래서 파일 자체를 여기서 단정한다.
"""

from __future__ import annotations

from pathlib import Path

from src.core import paths

#: 정적 배포 글꼴과 그 허가 원문이 함께 놓이는 폴더.
WEB_FONT_DIR: Path = paths.STATIC_DIR / "fonts"
LICENSE_PATH: Path = WEB_FONT_DIR / "OFL.txt"

#: 허가 원문이 반드시 담아야 하는 줄. 판본·예약 글꼴 이름·저작권자 한 줄씩이다.
REQUIRED_LICENSE_LINES: tuple[str, ...] = (
    "SIL OPEN FONT LICENSE Version 1.1",
    'Reserved Font Name "Prata"',
    "Copyright (c) 2024 PT& / 피티앤",
)

#: 위 허가 원문이 실제로 다루는 두 글꼴 계열. 파일 이름의 앞부분으로 확인한다.
LICENSED_FONT_FAMILIES: tuple[str, ...] = ("Prata", "Freesentation")

WEB_FONT_SUFFIX = ".woff2"


def test_웹_배포글꼴은_허가받은_두_계열뿐이고_허가원문을_함께_둔다() -> None:
    license_text = LICENSE_PATH.read_text(encoding="utf-8")
    for required_line in REQUIRED_LICENSE_LINES:
        assert required_line in license_text, f"허가 원문에 없음: {required_line}"

    delivered = sorted(
        path.name for path in WEB_FONT_DIR.glob(f"*{WEB_FONT_SUFFIX}")
    )
    # 폴더가 비면 아래 검사가 조용히 통과한다. 실제 배포 글꼴이 있는지 먼저 못박는다.
    assert delivered, "웹으로 내보내는 글꼴 파일이 하나도 없다"

    unlicensed = [
        name
        for name in delivered
        if not name.startswith(LICENSED_FONT_FAMILIES)
    ]
    assert not unlicensed, f"허가 원문이 다루지 않는 글꼴: {unlicensed}"
