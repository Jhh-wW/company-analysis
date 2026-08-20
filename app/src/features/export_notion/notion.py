"""노션과 실제로 이야기하는 부분 — 페이지 만들기 / 블록 100개씩 나눠 보내기.

★ 실제 네트워크 호출은 이 파일의 기본 구현(`_make_urllib_send`가 만드는 함수)에서만
  일어난다. `send_report_to_notion`을 부를 때 `send` 자리에 가짜 함수를 넣으면
  시험에서 진짜 노션 서버에 접속하지 않고도 전체 흐름을 검증할 수 있다.

블록으로 바꾸는 로직(판단)은 여기 두지 않는다 — 전부 `logic.py`.
이 파일은 순수 변환 위에 얹힌 「바깥 세상과 이야기하는 껍데기」다 (auth/google.py와 같은 구조).

★ 노션은 되돌리기가 없다 — 페이지를 만든 뒤 블록을 나눠 보내다 중간에 실패하면
  「반쯤 만들어진 페이지」가 남는다. 이 파일은 그 사실을 절대 숨기지 않고
  `NotionExportResult.partial`로 알린다 (팀장 지시 §6).
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from src.features.export_notion import constants, logic
from src.features.pipeline.port import Report

logger = logging.getLogger(__name__)

#: 노션 API 요청 하나를 보내고 JSON 응답을 dict로 돌려주는 함수의 모양.
#: (method, path, body) -> 응답(dict). 시험에서 가짜로 바꿔 끼운다.
SendFn = Callable[[str, str, dict], dict]
SleepFn = Callable[[float], None]


class MissingCredentialError(Exception):
    """노션 전송에 필요한 환경변수가 없다."""


class NotionAPIError(Exception):
    """비밀값 없는 전송 실패 메타데이터.

    ``uncertain``이면 원격 적용 여부를 증명할 수 없으므로 호출자는 상태를
    저장하고 명시적 중복 위험 확인 없이는 다시 보내면 안 된다.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.uncertain = uncertain


def _retry_after_seconds(value: object) -> float | None:
    """Notion이 쓰는 Retry-After delta-seconds를 제한 범위로 읽는다."""
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, constants.MAX_RETRY_AFTER_SEC)


# ══════════════════════════════════════════════════════════
# 환경변수 읽기 — 토큰 값은 절대 로그·예외 메시지에 담지 않는다
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class NotionConfig:
    """노션 전송에 필요한 값. ★ 절대 코드에 하드코딩하지 않는다."""

    token: str
    parent_page_id: str


def _resolve_config(token: str, parent_page_id: str) -> NotionConfig:
    """토큰·부모 페이지 ID를 정한다. 인자로 안 주면 환경변수에서 읽는다.

    Raises:
        MissingCredentialError: 하나라도 없으면, «무엇이 없는지»만 한국어로 알린다.
            값(토큰 등)은 메시지에 절대 담지 않는다.
    """
    resolved_token = token or os.environ.get(constants.ENV_NOTION_TOKEN, "").strip()
    resolved_parent = parent_page_id or os.environ.get(
        constants.ENV_NOTION_PARENT_PAGE_ID, ""
    ).strip()

    missing = [
        name
        for name, value in (
            (constants.ENV_NOTION_TOKEN, resolved_token),
            (constants.ENV_NOTION_PARENT_PAGE_ID, resolved_parent),
        )
        if not value
    ]
    if missing:
        raise MissingCredentialError(
            "노션 전송에 필요한 환경변수가 없습니다: " + ", ".join(missing)
        )
    return NotionConfig(token=resolved_token, parent_page_id=resolved_parent)


def is_notion_configured() -> bool:
    """노션 전송에 필요한 두 설정이 모두 있는지만 돌려준다.

    화면은 이 값으로 실행 가능한 버튼과 설정 안내를 구분한다. 실제 토큰이나
    페이지 ID는 반환하지 않으므로 템플릿·로그에 비밀값이 섞일 길을 만들지 않는다.
    """
    try:
        _resolve_config("", "")
    except MissingCredentialError:
        return False
    return True


# ══════════════════════════════════════════════════════════
# 실제 네트워크 호출 (기본 구현) — 시험에서는 주입한 가짜로 대체된다
# ══════════════════════════════════════════════════════════


def _make_urllib_send(token: str) -> SendFn:
    """`urllib`으로 실제 노션 서버에 접속하는 `SendFn`을 만든다.

    ★ 새 의존성(예: requests, notion-client)을 쓰지 않는다 — 표준 라이브러리
      `urllib`만 쓴다 (`auth/google.py`와 같은 방침).
    """

    def send(method: str, path: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{constants.NOTION_API_BASE}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": constants.NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=constants.HTTP_TIMEOUT_SEC
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 응답 본문은 기록하지 않는다. 외부 서비스가 요청 원문을 오류 본문에
            # 되비추면 보고서 내용이 로그에 남을 수 있기 때문이다.
            logger.warning("노션 API 오류 status=%s", exc.code)
            retry_after = None
            if exc.code == 429:
                header_value = (
                    exc.headers.get("Retry-After") if exc.headers is not None else None
                )
                retry_after = _retry_after_seconds(header_value)
            raise NotionAPIError(
                f"노션 API가 오류를 돌려줬습니다 (상태 코드 {exc.code})",
                status_code=exc.code,
                retry_after=retry_after,
                uncertain=exc.code >= 500,
            ) from exc
        except urllib.error.URLError as exc:
            logger.warning(
                "노션 서버와 통신 실패 type=%s",
                type(getattr(exc, "reason", exc)).__name__,
            )
            raise NotionAPIError(
                "노션 서버와 통신하지 못했습니다", uncertain=True
            ) from exc
        except (TimeoutError, OSError) as exc:
            # urllib가 환경에 따라 URLError로 감싸지 않은 TimeoutError/OSError를
            # 그대로 올리기도 한다. 이 경우에도 웹 500과 내부 메시지 노출을 막는다.
            logger.warning("노션 서버와 통신 실패 type=%s", type(exc).__name__)
            raise NotionAPIError(
                "노션 서버와 통신하지 못했습니다", uncertain=True
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("노션 응답을 해석하지 못함 type=%s", type(exc).__name__)
            raise NotionAPIError(
                "노션 응답을 해석하지 못했습니다", uncertain=True
            ) from exc

    return send


# ══════════════════════════════════════════════════════════
# 블록 나누기 — 한 요청 100개 제한 (팀장 지시 §4)
# ══════════════════════════════════════════════════════════


def _chunk_blocks(blocks: list[dict], size: int) -> list[list[dict]]:
    """블록 목록을 `size`개씩 나눈다. 빈 목록이면 빈 조각 하나를 돌려준다."""
    if not blocks:
        return [[]]
    return [blocks[i : i + size] for i in range(0, len(blocks), size)]


def _page_payload(parent_page_id: str, title: str, children: list[dict]) -> dict:
    return {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        },
        "children": children,
    }


# ══════════════════════════════════════════════════════════
# 결과값 — 실패해도 「어디까지 갔는지」를 반드시 알린다
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class NotionExportResult:
    """노션 전송 한 번의 결과.

    ★ `partial=True`면 페이지는 만들어졌지만 블록 일부만 들어갔을 수 있다는 뜻이다
      — 노션은 되돌리기가 없으므로 이 값을 반드시 사용자에게 보여줘야 한다.
    """

    success: bool
    page_id: str = ""
    page_url: str = ""
    #: 블록을 몇 개 조각으로 나눠 보냈는지 (100개 넘으면 2 이상).
    chunk_count: int = 1
    #: 사용자에게 보여줄 한국어 오류 메시지. 성공하면 빈 문자열.
    error: str = ""
    #: 페이지가 «일부만» 만들어졌을 가능성이 있는가.
    partial: bool = False
    #: 원격 적용 여부를 증명할 수 없어 자동 재전송하면 안 되는가.
    uncertain: bool = False


def _send_safely(
    send: SendFn,
    method: str,
    path: str,
    body: dict,
    *,
    sleep: SleepFn = time.sleep,
) -> dict:
    """전송 어댑터의 예상 밖 실패를 안전한 사용자 오류로 수렴시킨다.

    실제 urllib 어댑터뿐 아니라 시험·향후 교체 어댑터가 ``RuntimeError`` 같은
    다른 예외를 올려도 보고서 원문이나 비밀값이 응답·로그에 남지 않게 한다.
    """
    retries = 0
    total_wait = 0.0
    while True:
        try:
            response = send(method, path, body)
        except NotionAPIError as exc:
            delay = exc.retry_after
            may_retry = (
                exc.status_code == 429
                and delay is not None
                and retries < constants.MAX_429_RETRIES
                and total_wait + delay <= constants.MAX_TOTAL_RETRY_WAIT_SEC
            )
            if not may_retry:
                raise
            sleep(delay)
            retries += 1
            total_wait += delay
            continue
        except Exception as exc:  # noqa: BLE001 — 어댑터 경계에서 웹 500을 막는다
            logger.warning("노션 전송 어댑터 실패 type=%s", type(exc).__name__)
            raise NotionAPIError(
                "노션 전송 중 예상하지 못한 오류가 났습니다",
                uncertain=True,
            ) from exc
        if not isinstance(response, dict):
            logger.warning("노션 응답 형식 오류 type=%s", type(response).__name__)
            raise NotionAPIError(
                "노션 응답 형식이 올바르지 않습니다", uncertain=True
            )
        return response


# ══════════════════════════════════════════════════════════
# 전체 흐름 — 페이지 생성 → (필요하면) 나머지 조각 이어 붙이기
# ══════════════════════════════════════════════════════════


def send_report_to_notion(
    report: Report,
    *,
    grade_note: str = "",
    send: Optional[SendFn] = None,
    token: str = "",
    parent_page_id: str = "",
    sleep: SleepFn = time.sleep,
) -> NotionExportResult:
    """보고서 하나를 노션 페이지로 만든다.

    순서: 환경변수(또는 인자) 확인 → 블록 변환 → 100개씩 나누기 →
          페이지 생성(첫 조각 포함) → 나머지 조각을 `children` API로 이어 붙이기.

    ★ 실패해도 화면·워드는 그대로 남는다 — 노션 전송 실패에는 통과 기준을
      두지 않는다 (확정/07_출력/3_기준/01_성공기준.md — 「남의 서버 사정」).

    Args:
        report: 화면에 낸 것과 같은 보고서 데이터.
        grade_note: 상단 라벨 문구. `logic.build_blocks`로 그대로 전달한다.
        send: 노션 API 호출 함수. 없으면 실제 네트워크로 나가는 기본 구현을 쓴다.
            시험에서는 반드시 가짜를 넣어 진짜 접속을 막는다.
        token: 노션 통합 토큰. 없으면 환경변수(`NOTION_TOKEN`)에서 읽는다.
        parent_page_id: 페이지를 만들 부모 페이지 ID. 없으면 환경변수
            (`NOTION_PARENT_PAGE_ID`)에서 읽는다 — 코드에 페이지 ID를 박지 않는다.

    Returns:
        성공 여부·페이지 ID·나눈 조각 수·(실패 시) 반쯤 만들어졌을 가능성을 담은 결과.
        예외를 던지지 않는다 — 호출부(웹 화면)가 항상 결과값 하나로 처리할 수 있게.
    """
    try:
        config = _resolve_config(token, parent_page_id)
    except MissingCredentialError as exc:
        return NotionExportResult(success=False, error=str(exc))

    active_send = send if send is not None else _make_urllib_send(config.token)
    title = logic.build_page_title(report)
    blocks = logic.build_blocks(report, grade_note=grade_note)
    chunks = _chunk_blocks(blocks, constants.MAX_BLOCKS_PER_REQUEST)
    total = len(chunks)

    try:
        payload = _page_payload(config.parent_page_id, title, chunks[0])
        created = _send_safely(
            active_send,
            "POST",
            constants.PAGES_PATH,
            payload,
            sleep=sleep,
        )
    except NotionAPIError as exc:
        # ★ 페이지 자체가 안 만들어졌으므로 partial이 아니다 — 노션에 남은 게 없다.
        return NotionExportResult(
            success=False,
            error=str(exc),
            chunk_count=total,
            uncertain=exc.uncertain,
        )

    page_id = created.get("id", "")
    page_url = created.get("url", "")
    if not page_id:
        return NotionExportResult(
            success=False,
            error="노션이 페이지 ID를 돌려주지 않았습니다",
            chunk_count=total,
            uncertain=True,
        )

    for order, chunk in enumerate(chunks[1:], start=2):
        try:
            _send_safely(
                active_send,
                "PATCH",
                constants.CHILDREN_PATH_TEMPLATE.format(block_id=page_id),
                {"children": chunk},
                sleep=sleep,
            )
        except NotionAPIError as exc:
            sent = order - 1
            return NotionExportResult(
                success=False,
                page_id=page_id,
                page_url=page_url,
                chunk_count=total,
                partial=True,
                error=(
                    f"노션에 페이지는 만들어졌지만 일부 내용만 들어갔을 수 있습니다 "
                    f"({sent}/{total} 조각까지 보낸 뒤 실패) — {exc}"
                ),
            )

    return NotionExportResult(
        success=True, page_id=page_id, page_url=page_url, chunk_count=total
    )
