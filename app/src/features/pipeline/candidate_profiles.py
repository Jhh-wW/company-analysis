"""주소 비교 후보를 일정한 병렬 폭과 요청 기한 안에서 조회한다."""

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence, TypeVar

from src.features.pipeline.candidate_profile_constants import DART_PROFILE_WORKERS

Item = TypeVar("Item")
Profile = TypeVar("Profile")


def fetch_candidate_profiles(
    matches: Sequence[Item], fetch: Callable[[Item], Profile], *, deadline: float
) -> list[tuple[Item, Profile]]:
    """완료 순서에 흔들리지 않고, 기한이 지나면 다음 묶음을 보내지 않는다."""
    results: list[tuple[Item, Profile]] = []

    def invoke(match: Item) -> Profile:
        if time.monotonic() >= deadline:
            raise TimeoutError("회사 후보의 주소 조회 시간이 초과되었습니다")
        return fetch(match)

    # 실행 중인 HTTP 요청이 끝날 때까지 상위 resolver의 worker 자리를 유지한다.
    # 한 요청이 시간초과되었다고 미완료 thread를 버리고 새 조회를 계속 만들지 않는다.
    with ThreadPoolExecutor(max_workers=DART_PROFILE_WORKERS) as executor:
        for start in range(0, len(matches), DART_PROFILE_WORKERS):
            if time.monotonic() >= deadline:
                raise TimeoutError("회사 후보의 주소 조회 시간이 초과되었습니다")
            batch = matches[start : start + DART_PROFILE_WORKERS]
            futures = [
                executor.submit(contextvars.copy_context().run, invoke, match)
                for match in batch
            ]
            for match, future in zip(batch, futures):
                results.append((match, future.result()))
        if time.monotonic() >= deadline:
            raise TimeoutError("회사 후보의 주소 조회 시간이 초과되었습니다")
    return results
