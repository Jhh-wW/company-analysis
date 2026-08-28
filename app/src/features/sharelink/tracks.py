"""손님을 «네 갈래»로 나누고 각자 다른 상한을 준다 (문제로그 P-94·P-95).

★ 왜 나누나 — 상한을 하나만 두면 포트폴리오용 링크 하나가 친구들 몫까지 먹는다.
  반대로 다 열어두면 인터넷의 아무나 돈을 쓴다.

| 갈래 | 누구 | 하루 상한 | 통장 이름 |
|---|---|---:|---|
| `ADMIN`  | 나 (관리자 명단) | 5,000원 | `user:<이메일>` |
| `MEMBER` | 초대한 친구      | 3,000원 + 성공 3건 | `user:<이메일>` |
| `LINK`   | 열쇠 링크 방문자 | 3,000원 | `<열쇠>` |
| `PUBLIC` | 그냥 들어온 사람  | **0원** | `(열쇠 없음)` |

★ **로그인은 「누구인가」일 뿐 「써도 되는가」가 아니다** (P-95).
  로그인만으로 갈래를 정하면, 아무나 구글 로그인해서 돈을 쓴다.
  초대 명단(`allowlist.py`)에 있어야 `MEMBER`가 된다.

★ 여기에는 DB도 시계도 없다. **판단에 필요한 사실을 인자로 받는** 순수 함수다.
"""

from __future__ import annotations

from enum import Enum

from src.features.sharelink.constants import (
    ADMIN_DAILY_BUDGET_KRW,
    PER_LINK_DAILY_BUDGET_KRW,
    PER_USER_DAILY_BUDGET_KRW,
    PUBLIC_BUCKET,
    PUBLIC_DAILY_BUDGET_KRW,
    USER_BUCKET_PREFIX,
)
from src.features.sharelink.logic import is_valid_key


class Track(str, Enum):
    """손님의 갈래."""

    ADMIN = "admin"      #: 관리자 (나)
    MEMBER = "member"    #: 초대한 친구
    LINK = "link"        #: 열쇠 LINK로 들어온 방문자
    PUBLIC = "public"    #: 로그인도 열쇠도 없는 손님


#: 갈래별 비용 하루 입장 상한. MEMBER는 성공 3건 제한도 별도로 함께 적용한다.
BUDGET_BY_TRACK: dict[Track, float] = {
    Track.ADMIN: ADMIN_DAILY_BUDGET_KRW,
    Track.MEMBER: PER_USER_DAILY_BUDGET_KRW,
    Track.LINK: PER_LINK_DAILY_BUDGET_KRW,
    Track.PUBLIC: PUBLIC_DAILY_BUDGET_KRW,
}


def decide_track(
    *, email: str, is_admin: bool, is_member: bool, share_key: str
) -> Track:
    """이 손님이 어느 갈래인가.

    Args:
        email: 로그인한 사람의 이메일. 로그인 안 했으면 빈 문자열.
        is_admin: 관리자 명단에 있는가.
        is_member: **초대 명단에 있는가.** ★ 로그인 여부와 «다른» 값이다 (P-95).
        share_key: 열쇠 링크로 들어왔다면 그 열쇠. 아니면 빈 문자열.

    Returns:
        갈래.

    ★ 순서에 뜻이 있다 — **관리자 → 초대된 친구 → 열쇠 링크 → 나머지**.
      · 관리자가 열쇠 링크로 들어와도 «관리자 몫»을 쓴다 (내 링크를 내가 눌러볼 때).
      · **로그인했지만 초대 명단에 없으면 열쇠 링크 몫**을 쓴다 —
        방문자가 링크로 들어와 «호기심에» 구글 로그인을 눌러도
        같은 LINK의 여러 회사 조사 합계 안에서만 쓴다. 로그인했다고 몫이 늘지 않는다.
      · 그것도 없으면 `PUBLIC`(0원)이다.
    """
    if email and is_admin:
        return Track.ADMIN
    if email and is_member:
        return Track.MEMBER
    if is_valid_key(share_key):
        return Track.LINK
    return Track.PUBLIC


def bucket_of(track: Track, *, email: str, share_key: str) -> str:
    """이 손님의 돈이 «어느 통장»에서 나가는가.

    Args:
        track: `decide_track()`이 정한 갈래.
        email: 로그인한 사람의 이메일.
        share_key: 열쇠.

    Returns:
        통장 이름.

    ★ 사람 통장에는 `user:` 표시를 붙인다 — 열쇠(16진수)와 **절대 겹치지 않게**.
      겹치면 남의 통장에서 돈이 나간다.
    """
    if track in (Track.ADMIN, Track.MEMBER):
        return f"{USER_BUCKET_PREFIX}{email.strip().lower()}"
    if track is Track.LINK:
        return share_key.strip().lower()
    return PUBLIC_BUCKET


def budget_of(track: Track) -> float:
    """이 갈래의 비용 하루 입장 상한.

    MEMBER에는 이 값과 별도로 성공 보고서 3건 제한도 함께 적용한다.
    """
    return BUDGET_BY_TRACK[track]
