"""정확 식별 우선권을 보존하는 DART 후보 프로필 lookahead."""

from __future__ import annotations

from typing import Protocol, Sequence, TypeVar

from src.features.pipeline.candidate_profile_constants import (
    DART_PROFILE_DIVERSE_MATCH_KINDS,
    DART_PROFILE_EXACT_MATCH_KINDS,
)


class _CandidateRecord(Protocol):
    @property
    def corp_code(self) -> str: ...


class CandidateProfileMatch(Protocol):
    @property
    def record(self) -> _CandidateRecord: ...

    @property
    def match_kind(self) -> str: ...


MatchT = TypeVar("MatchT", bound=CandidateProfileMatch)


def candidate_profile_lookahead(
    matches: Sequence[MatchT], *, limit: int
) -> tuple[MatchT, ...]:
    """정확 식별과 강한 공식 약어 근거를 최대 ``limit``건에 담는다."""

    cap = max(0, int(limit))
    if cap == 0:
        return ()

    selected: list[MatchT] = []
    selected_codes: set[str] = set()

    def add(match: MatchT) -> None:
        corp_code = match.record.corp_code
        if corp_code in selected_codes or len(selected) >= cap:
            return
        selected.append(match)
        selected_codes.add(corp_code)

    # matcher의 기존 결정적 순서대로 exact 후보를 먼저 담는다. exact 후보가 cap을
    # 채우면 fuzzy 후보는 다양성을 이유로 끼워 넣지 않는다.
    for match in matches:
        if match.match_kind in DART_PROFILE_EXACT_MATCH_KINDS:
            add(match)
            if len(selected) >= cap:
                return tuple(selected)

    # exact 아래의 강한 공식 약어 근거 갈래에서 최고 순위 한 건씩만 먼저 보강한다.
    # 약한 token/trigram은 이 예약을 쓰지 못한다.
    represented_kinds: set[str] = set()
    for match in matches:
        if (
            match.match_kind not in DART_PROFILE_DIVERSE_MATCH_KINDS
            or match.match_kind in represented_kinds
        ):
            continue
        add(match)
        represented_kinds.add(match.match_kind)
        if len(selected) >= cap:
            return tuple(selected)

    # 남는 자리는 기존 전체 순위로 채운다.
    for match in matches:
        add(match)
        if len(selected) >= cap:
            break
    return tuple(selected)
