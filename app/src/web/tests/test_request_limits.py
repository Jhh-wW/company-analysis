"""multipart 파서와 OCR 앞의 요청·업로드 메모리 경계를 확인한다."""

from __future__ import annotations

import asyncio
import re
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.datastructures import UploadFile

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.posting_image import constants as image_constants
from src.features.pipeline.demo import DemoPipeline
from src.web import main
from src.web import job_runtime, request_helpers, runtime
from src.web import security as web_security
from src.web.security import (
    FORM_BODY_MAX_BYTES,
    RUN_BODY_MAX_BYTES,
    RequestBodyLimitMiddleware,
    body_limit_for,
)


def _valid_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(255, 255, 255)).save(output, "PNG")
    return output.getvalue()


def _confirmed_demo_run(
    client: TestClient,
    data: dict[str, str],
    *,
    files=None,
):
    confirmed = client.post(
        "/confirm",
        data={"company": data["company"], "region": data.get("region", "")},
    )
    token = re.search(
        r'name="paid_attempt_token" value="([^"]+)"', confirmed.text
    )
    assert token is not None
    return client.post(
        "/run",
        data={**data, "paid_attempt_token": token.group(1)},
        files=files,
        follow_redirects=False,
    )


def _run_asgi_request(
    *, path: str, chunks: list[bytes], headers=(), method: str = "POST"
):
    called = False
    sent: list[dict] = []

    async def downstream(scope, receive, send):
        nonlocal called
        called = True
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": list(headers),
    }
    asyncio.run(RequestBodyLimitMiddleware(downstream)(scope, receive, send))
    status = next(item["status"] for item in sent if item["type"] == "http.response.start")
    return called, status


def test_content_length가_없어도_실제_본문을_세어_413으로_막는다():
    called, status = _run_asgi_request(
        path="/confirm",
        chunks=[b"a" * FORM_BODY_MAX_BYTES, b"b"],
    )

    assert called  # 앱이 receive를 읽기 시작해도 파서가 상한 밖 바이트는 받지 못한다.
    assert status == 413


def test_실제_FastAPI_form파서도_content_length없는_초과본문을_413으로_받는다():
    sent: list[dict] = []
    messages = [
        {
            "type": "http.request",
            "body": b"company=" + b"a" * FORM_BODY_MAX_BYTES,
            "more_body": True,
        },
        {"type": "http.request", "body": b"x", "more_body": False},
    ]

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/confirm",
        "raw_path": b"/confirm",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    asyncio.run(main.app(scope, receive, send))

    status = next(item["status"] for item in sent if item["type"] == "http.response.start")
    assert status == 413


def test_큰_content_length는_본문_파서에_들어가기_전에_막는다():
    called, status = _run_asgi_request(
        path="/run",
        chunks=[b""],
        headers=[(b"content-length", str(RUN_BODY_MAX_BYTES + 1).encode("ascii"))],
    )

    assert not called
    assert status == 413


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"not-a-number")],
        [(b"content-length", b"-1")],
        [
            (b"content-length", b"1"),
            (b"content-length", str(FORM_BODY_MAX_BYTES + 1).encode("ascii")),
        ],
    ],
    ids=["invalid", "negative", "duplicate"],
)
def test_이상한_content_length도_실제_본문_계수로_우회하지_못한다(headers):
    called, status = _run_asgi_request(
        path="/confirm",
        chunks=[b"a" * FORM_BODY_MAX_BYTES, b"b"],
        headers=headers,
    )

    assert called
    assert status == 413


def test_run과_confirm은_같은_1MiB_상한을_쓴다():
    assert body_limit_for({"type": "http", "method": "POST", "path": "/confirm"}) == FORM_BODY_MAX_BYTES
    multipart_scope = {
        "type": "http",
        "method": "POST",
        "path": "/run",
        "headers": [(b"content-type", b"multipart/form-data; boundary=abc")],
    }
    assert body_limit_for(multipart_scope) == RUN_BODY_MAX_BYTES
    assert body_limit_for({"type": "http", "method": "POST", "path": "/run"}) == FORM_BODY_MAX_BYTES
    assert RUN_BODY_MAX_BYTES == FORM_BODY_MAX_BYTES


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-type", b"application/x-www-form-urlencoded")],
        [(b"content-type", b"application/octet-stream")],
        [
            (b"content-type", b"multipart/form-data; boundary=abc"),
            (b"content-type", b"application/x-www-form-urlencoded"),
        ],
    ],
)
def test_run은_본문형식과_무관하게_1MiB만_받는다(headers):
    scope = {"type": "http", "method": "POST", "path": "/run", "headers": headers}
    assert body_limit_for(scope) == FORM_BODY_MAX_BYTES


def test_run의_13MiB_multipart는_파싱전에_413으로_막는다():
    with TestClient(main.app) as client:
        response = client.post(
            "/run",
            data={"company": "우리엔", "region": "서울"},
            files={"posting_images": ("legacy.png", b"x" * (13 * 1024 * 1024))},
        )

    assert response.status_code == 413


def test_run의_content_length없는_streaming초과도_413으로_막는다():
    called, status = _run_asgi_request(
        path="/run",
        chunks=[b"a" * FORM_BODY_MAX_BYTES, b"b"],
        headers=[(b"content-type", b"multipart/form-data; boundary=legacy")],
    )

    assert called
    assert status == 413


def test_run의_정상_확인폼은_1MiB_아래에서_진행한다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    with TestClient(main.app) as client:
        response = _confirmed_demo_run(
            client,
            {"company": "우리엔", "region": "서울"},
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/progress/")


def test_DELETE도_본문_상한을_건너뛰지_못한다():
    called, status = _run_asgi_request(
        path="/future-delete",
        method="DELETE",
        chunks=[b"a" * (64 * 1024), b"b"],
    )

    assert called
    assert status == 413


class _TrackedUpload(UploadFile):
    def __init__(self, body: bytes, filename: str = "capture.png") -> None:
        super().__init__(BytesIO(body), filename=filename)
        self.requested_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        return await super().read(size)


def test_upload은_무제한_read없이_조각으로_읽고_항상_닫는다():
    upload = _TrackedUpload(b"x" * (job_runtime._UPLOAD_READ_CHUNK_BYTES + 1))

    images, failure = asyncio.run(job_runtime._read_posting_images_bounded([upload]))

    assert failure is None
    assert len(images[0]) == job_runtime._UPLOAD_READ_CHUNK_BYTES + 1
    assert upload.requested_sizes
    assert -1 not in upload.requested_sizes
    assert max(upload.requested_sizes) == job_runtime._UPLOAD_READ_CHUNK_BYTES
    assert upload.file.closed


def test_upload_한장_상한을_넘으면_즉시_거절하고_닫는다():
    upload = _TrackedUpload(b"x" * (image_constants.MAX_IMAGE_BYTES + 1))

    images, failure = asyncio.run(job_runtime._read_posting_images_bounded([upload]))

    assert images == []
    assert failure is not None and "1장은 최대" in failure.error
    assert upload.file.closed


def test_upload_전체_상한을_넘어도_모든_파일을_닫는다():
    each = image_constants.MAX_TOTAL_BYTES // 3 + 1
    uploads = [_TrackedUpload(b"x" * each) for _ in range(3)]

    images, failure = asyncio.run(job_runtime._read_posting_images_bounded(uploads))

    assert images == []
    assert failure is not None and "전체 용량" in failure.error
    assert all(upload.file.closed for upload in uploads)


def test_upload_장수_상한을_넘으면_읽지_않고_모두_닫는다():
    uploads = [
        _TrackedUpload(b"x") for _ in range(image_constants.MAX_IMAGE_COUNT + 1)
    ]

    images, failure = asyncio.run(job_runtime._read_posting_images_bounded(uploads))

    assert images == []
    assert failure is not None and "최대" in failure.error
    assert all(not upload.requested_sizes for upload in uploads)
    assert all(upload.file.closed for upload in uploads)


def test_upload_dependency는_빈_파일명을_제외해도_모든_파일을_닫는다():
    empty_name = UploadFile(BytesIO(b"unexpected-body"), filename="")
    named = UploadFile(BytesIO(b"image"), filename="capture.png")

    async def consume_dependency() -> list[UploadFile]:
        dependency = job_runtime._posting_images_dependency([empty_name, named])
        files = await anext(dependency)
        await dependency.aclose()
        return files

    files = asyncio.run(consume_dependency())

    assert files == [named]
    assert empty_name.file.closed
    assert named.file.closed


def test_run이_CSRF로_일찍_끝나도_multipart_임시파일을_닫는다(monkeypatch):
    closed: list[UploadFile] = []
    original_close = UploadFile.close

    async def tracked_close(upload):
        closed.append(upload)
        await original_close(upload)

    monkeypatch.setattr(UploadFile, "close", tracked_close)
    client = TestClient(main.app)
    session = auth_logic.create_session("member@example.com", False)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)

    response = client.post(
        "/run",
        data={"company": "우리엔", "job": "영업", "region": "서울"},
        files={"posting_images": ("capture.png", b"image")},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert closed
    assert all(upload.file.closed for upload in closed)


def test_중간에_깨진_multipart도_400으로_끝나고_만든_임시파일을_닫는다(
    monkeypatch,
):
    created: list[UploadFile] = []
    original_init = UploadFile.__init__

    def tracked_init(upload, *args, **kwargs):
        original_init(upload, *args, **kwargs)
        created.append(upload)

    monkeypatch.setattr(UploadFile, "__init__", tracked_init)
    boundary = "security-regression-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="posting_images"; '
        'filename="capture.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
        "image-bytes\r\n"
        f"--{boundary}\r\n"
        # 두 번째 part에 필수 name을 일부러 빼서, 첫 파일을 만든 뒤 파서가 거절하게 한다.
        'Content-Disposition: form-data; filename="missing-name.txt"\r\n\r\n'
        "bad-part\r\n"
        f"--{boundary}--\r\n"
    ).encode("ascii")

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/run",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    assert response.status_code == 400
    assert "traceback" not in response.text.lower()
    assert created
    assert all(upload.file.closed for upload in created)


def test_회사분석_run은_옛_이미지필드를_무시하고_OCR을_부르지않는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    ocr_calls = 0

    def forbidden_ocr(*_args, **_kwargs):
        nonlocal ocr_calls
        ocr_calls += 1
        raise AssertionError("동의 없는 원본을 OCR로 보내면 안 됩니다")

    monkeypatch.setattr(job_runtime, "extract_posting_text", forbidden_ocr)
    with TestClient(main.app) as client:
        response = _confirmed_demo_run(
            client,
            {
                "company": "우리엔",
                "job": "무시할 옛 직무",
                "region": "서울",
                "posting_text": "무시할 옛 공고",
                "legal_name": "우리엔",
                "ref": "재수집-p003",
                "address": "서울",
            },
            files={"posting_images": ("capture.png", _valid_png())},
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/progress/")
    assert ocr_calls == 0


def test_파일명이_빈_업로드_필드는_파일_없음으로_처리한다(monkeypatch):
    """브라우저가 빈 file input을 보내도 `/run`이 500으로 깨지지 않는다."""
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = _confirmed_demo_run(
            client,
            {
                "company": "우리엔",
                "job": "영업",
                "region": "서울",
                "posting_text": "공고",
                "legal_name": "우리엔",
                "ref": "재수집-p003",
                "address": "서울",
            },
            files={"posting_images": ("", b"", "application/octet-stream")},
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/progress/")


def test_회사분석_run은_공고없이_빈호환필드로_진행한다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    with TestClient(main.app) as client:
        response = _confirmed_demo_run(
            client,
            {
                "company": "우리엔",
                "region": "서울",
                "legal_name": "우리엔",
                "ref": "재수집-p003",
                "address": "서울",
            },
        )

    assert response.status_code == 303
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    assert job_runtime._JOBS[run_id].user_input.job == ""
    assert job_runtime._JOBS[run_id].user_input.posting_text == ""


def test_run은_옛_image_only요청도_회사분석으로만_처리한다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = _confirmed_demo_run(
            client,
            {
                "company": "우리엔",
                "job": "영업",
                "region": "서울",
                "posting_text": " \t\r\n ",
                "legal_name": "우리엔",
                "ref": "재수집-p003",
                "address": "서울",
                "posting_image_consent": "yes",
            },
            files={
                "posting_images": (
                    "capture.png",
                    _valid_png(),
                    "image/png",
                )
            },
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/progress/")


def test_하이브_확인카드의_홈페이지는_안전한_새창_링크다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    response = TestClient(main.app).post(
        "/confirm",
        data={
            "company": "하이브",
            "job": "매니지먼트",
            "region": "서울 용산구",
            "posting_text": "공고",
        },
    )

    assert response.status_code == 200
    assert 'href="https://hybecorp.com"' in response.text
    assert 'target="_blank"' in response.text
    assert 'rel="noopener noreferrer external"' in response.text
    assert "홈페이지 새 창에서 열기" in response.text


def test_입력화면에는_직무_공고_OCR입력이_없다(
    monkeypatch,
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    body = TestClient(main.app).get("/").text

    assert 'name="company"' in body
    assert 'name="region"' in body
    assert 'name="job"' not in body
    assert 'name="posting_text"' not in body
    assert 'name="posting_image_consent"' not in body
    assert "Anthropic으로 전송" not in body
    assert "PostingImageStore" not in body
    assert 'id="confirmSubmitButton"' in body


def test_주소_힌트가_없어도_회사_확인_요청을_받는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    response = TestClient(main.app).post(
        "/confirm",
        data={"company": "하이브"},
    )

    assert response.status_code == 200
    assert "하이브" in response.text


def test_텍스트필드_글자수_상한도_서버가_검사한다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    response = TestClient(main.app).post(
        "/confirm",
        data={
            "company": "가" * (web_security.COMPANY_MAX_CHARS + 1),
            "job": "영업",
            "region": "서울",
            "posting_text": "공고",
        },
    )

    assert response.status_code == 422
