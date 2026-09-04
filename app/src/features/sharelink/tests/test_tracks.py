"""손님을 네 갈래로 나누는 판단을 못 박는다.

★ 이 시험이 막는 것 — **인터넷의 아무나 구글 로그인만 하면 돈을 쓰는 것.**
  로그인은 「이 사람이 누구인가」만 알려준다. 「써도 되는가」는 **초대 명단**이 정한다.
  사용자가 직접 지적해 잡힌 구멍이다.

★ 갈래별 상한
  관리자 5,000 / 초대된 친구 3,000원+성공 3건 / 열쇠 링크 3,000 / 모르는 손님 **0**
"""

from __future__ import annotations

import pytest

from src.features.sharelink.constants import (
    ADMIN_DAILY_BUDGET_KRW,
    PER_LINK_DAILY_BUDGET_KRW,
    PER_USER_DAILY_BUDGET_KRW,
    PUBLIC_BUCKET,
    PUBLIC_DAILY_BUDGET_KRW,
)
from src.features.sharelink.tracks import Track, bucket_of, budget_of, decide_track

_열쇠 = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
_나 = "admin@example.com"
_친구 = "friend@gmail.com"
_모르는사람 = "stranger@gmail.com"


def _갈래(**바꿀것) -> Track:
    기본 = dict(email="", is_admin=False, is_member=False, share_key="")
    기본.update(바꿀것)
    return decide_track(**기본)


# ══════════════════════════════════════════════════════════
# ① ★ 핵심 — 로그인만으로는 아무것도 못 한다
# ══════════════════════════════════════════════════════════


def test_로그인만_했고_초대_명단에_없으면_모르는_손님이다():
    """★ 로그인만 했고 초대 명단에 없으면 모르는 손님이다. 이게 없으면 인터넷의 아무나 MEMBER 성공 보고서를 쓴다."""
    assert _갈래(email=_모르는사람, is_member=False) is Track.PUBLIC


def test_모르는_손님은_상한이_0원이다():
    """★ 0원 = 진짜 조사를 «아예» 안 준다. 데모는 그대로 볼 수 있다."""
    assert budget_of(Track.PUBLIC) == PUBLIC_DAILY_BUDGET_KRW == 0.0


def test_초대_명단에_있어야_친구다():
    assert _갈래(email=_친구, is_member=True) is Track.MEMBER
    assert budget_of(Track.MEMBER) == PER_USER_DAILY_BUDGET_KRW == 3000.0


def test_친구에게도_실패를_포함한_유료_단계_상한이_있다():
    """성공 3건만 세면 실패를 반복해 성공 0건인 채 비용을 무한히 쓸 수 있다."""
    assert budget_of(Track.MEMBER) > 0


def test_명단에서_빼면_바로_모르는_손님이_된다():
    """★ 되돌릴 수 있어야 한다 — 다 썼거나 계정이 넘어갔을 때."""
    assert _갈래(email=_친구, is_member=False) is Track.PUBLIC


# ══════════════════════════════════════════════════════════
# ② ★ 링크로 들어와 로그인해도 «몫이 늘지 않는다»
# ══════════════════════════════════════════════════════════


def test_링크로_들어와_로그인해도_링크_몫만_쓴다():
    """★ 사용자가 지적한 바로 그 상황.

    인사팀이 열쇠 링크로 들어와 «호기심에» 구글 로그인을 눌러도,
    같은 LINK의 여러 회사 조사 합계 몫 안에서만 쓴다. 로그인했다고 통장이 하나 더
    생기지 않는다.
    """
    갈래 = _갈래(email=_모르는사람, is_member=False, share_key=_열쇠)

    assert 갈래 is Track.LINK
    assert budget_of(갈래) == PER_LINK_DAILY_BUDGET_KRW


def test_링크로_들어온_사람의_돈은_링크_통장에서_나간다():
    """★ 통장이 갈리면 상한이 두 배가 된다 — 같은 통장이어야 한다."""
    갈래 = _갈래(email=_모르는사람, share_key=_열쇠)

    assert bucket_of(갈래, email=_모르는사람, share_key=_열쇠) == _열쇠


def test_링크가_있어도_초대된_친구는_자기_몫을_쓴다():
    """친구가 내 포폴 링크를 눌러봐도 «친구 몫»이다 — 링크 예산을 축내지 않는다."""
    assert _갈래(email=_친구, is_member=True, share_key=_열쇠) is Track.MEMBER


# ══════════════════════════════════════════════════════════
# ③ 관리자
# ══════════════════════════════════════════════════════════


def test_관리자는_관리자_몫을_쓴다():
    assert _갈래(email=_나, is_admin=True) is Track.ADMIN
    assert budget_of(Track.ADMIN) == ADMIN_DAILY_BUDGET_KRW


def test_관리자가_링크로_들어와도_관리자_몫이다():
    """내 링크를 내가 눌러볼 때 포폴 몫을 축내면 안 된다."""
    assert _갈래(email=_나, is_admin=True, share_key=_열쇠) is Track.ADMIN


def test_관리자에게도_상한이_있다():
    """★ 「내 돈이니까 무제한」이 가장 위험하다 — 코드 실수로 밤새 샐 수 있다."""
    assert budget_of(Track.ADMIN) > 0


# ══════════════════════════════════════════════════════════
# ④ 통장이 «겹치지» 않는다 — 겹치면 남의 돈이 나간다
# ══════════════════════════════════════════════════════════


def test_사람_통장과_열쇠_통장은_안_겹친다():
    """★ 열쇠는 16진수뿐이라, `user:` 표시가 붙은 이름과는 절대 같아질 수 없다."""
    사람 = bucket_of(Track.MEMBER, email=_친구, share_key="")
    링크 = bucket_of(Track.LINK, email="", share_key=_열쇠)

    assert 사람 != 링크
    assert 사람.startswith("user:")


def test_사람마다_통장이_다르다():
    가 = bucket_of(Track.MEMBER, email="a@x.com", share_key="")
    나 = bucket_of(Track.MEMBER, email="b@x.com", share_key="")

    assert 가 != 나


def test_대소문자가_달라도_같은_통장이다():
    """★ 안 맞추면 「Hong@」과 「hong@」이 통장 두 개가 되어 상한이 두 배가 된다."""
    가 = bucket_of(Track.MEMBER, email="Hong@Gmail.com", share_key="")
    나 = bucket_of(Track.MEMBER, email="hong@gmail.com", share_key="")

    assert 가 == 나


def test_모르는_손님은_한_통장으로_묶인다():
    assert bucket_of(Track.PUBLIC, email="", share_key="") == PUBLIC_BUCKET


# ══════════════════════════════════════════════════════════
# ⑤ 이상한 열쇠는 갈래를 못 만든다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("열쇠", ["", "아무글자", "zz", "a" * 200])
def test_이상한_열쇠로는_링크_갈래가_안_된다(열쇠: str):
    """★ 아무 글자나 통하면 주소창에 타이핑해 3,000원짜리 통장을 무한히 만든다."""
    assert _갈래(share_key=열쇠) is Track.PUBLIC


def test_DB에_남은_16자리_열쇠도_공개_0원_갈래다():
    legacy_key = "a1b2c3d4e5f60718"

    track = _갈래(share_key=legacy_key)

    assert track is Track.PUBLIC
    assert budget_of(track) == PUBLIC_DAILY_BUDGET_KRW == 0.0


def test_로그인_안_했으면_이메일이_있어도_무시한다():
    """빈 이메일에 관리자 표시만 켜진 이상한 상태 — 통과시키면 안 된다."""
    assert _갈래(email="", is_admin=True, is_member=True) is Track.PUBLIC
