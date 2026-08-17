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


class MissingCredentialError(Exception):
    """노션 전송에 필요한 환경변수가 없다."""


class NotionAPIError(Exception):
    """노션과의 통신이나 응답이 이상하다. 사용자에게 내부 내용을 그대로 보여주지 않는다."""


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
            # ★ 응답 본문에 우리 쪽 요청 내용(토큰 등)은 없지만, 혹시 모를 노출을 막기
            #   위해 상태 코드만 사용자에게 보여주고 본문은 로그에만 남긴다.
            detail = _safe_error_detail(exc)
            logger.warning("노션 API 오류 status=%s detail=%s", exc.code, detail)
            raise NotionAPIError(f"노션 API가 오류를 돌려줬습니다 (상태 코드 {exc.code})") from exc
        except urllib.error.URLError as exc:
            logger.warning("노션 서버와 통신 실패: %s", exc)
            raise NotionAPIError("노션 서버와 통신하지 못했습니다") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("노션 응답을 해석하지 못함: %s", exc)
            raise NotionAPIError("노션 응답을 해석하지 못했습니다") from exc

    return send


def _safe_error_detail(exc: urllib.error.HTTPError) -> str:
    """오류 응답 본문을 로그용으로만 짧게 읽는다. 읽기 실패해도 예외를 더 키우지 않는다."""
    try:
        return exc.read().decode("utf-8")[:500]
    except (OSError, UnicodeDecodeError):
        return "(응답 본문을 읽지 못함)"


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
        created = active_send("POST", constants.PAGES_PATH, payload)
    except NotionAPIError as exc:
        # ★ 페이지 자체가 안 만들어졌으므로 partial이 아니다 — 노션에 남은 게 없다.
        return NotionExportResult(success=False, error=str(exc), chunk_count=total)

    page_id = created.get("id", "")
    page_url = created.get("url", "")
    if not page_id:
        return NotionExportResult(
            success=False,
            error="노션이 페이지 ID를 돌려주지 않았습니다",
            chunk_count=total,
        )

    for order, chunk in enumerate(chunks[1:], start=2):
        try:
            active_send(
                "PATCH",
                constants.CHILDREN_PATH_TEMPLATE.format(block_id=page_id),
                {"children": chunk},
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
