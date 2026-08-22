"""사용량 계수기 테스트 — 한도·경보·날짜 전환."""
from __future__ import annotations

import io
import struct
import sys
import traceback
import urllib.error
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import dart_client
from core.dart_client import DartLimitReached, UsageCounter


class _Response:
    def __init__(self, data: bytes):
        self.data = data
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1):
        self.read_sizes.append(size)
        return self.data if size < 0 else self.data[:size]


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    return _zip_entry_bytes(list(entries.items()))


def _zip_entry_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _replace_zip_end_u16(data: bytes, field_offset: int, value: int) -> bytes:
    mutated = bytearray(data)
    end_offset = mutated.rfind(b"PK\x05\x06")
    assert end_offset >= 0
    struct.pack_into("<H", mutated, end_offset + field_offset, value)
    return bytes(mutated)


def test_tick이_세고_한도에서_멈춘다(tmp_path):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=3)
    assert counter.tick("2026-08-14") == 1
    assert counter.tick("2026-08-14") == 2
    assert counter.tick("2026-08-14") == 3
    with pytest.raises(DartLimitReached):
        counter.tick("2026-08-14")


def test_날짜가_바뀌면_0부터(tmp_path):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=3)
    counter.tick("2026-08-14")
    counter.tick("2026-08-14")
    assert counter.tick("2026-08-15") == 1


def test_경보_문턱_경고_출력(tmp_path, capsys):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)
    for _ in range(8):
        counter.tick("2026-08-14")
    assert "경보" in capsys.readouterr().out


def test_로컬_기본_경로는_예전과_같다(monkeypatch):
    monkeypatch.delenv(dart_client.ENV_DATA_ROOT, raising=False)

    assert dart_client.default_counter_path() == dart_client.COUNTER_PATH


def test_배포에서는_영속_데이터_루트를_쓴다(tmp_path, monkeypatch):
    monkeypatch.setenv(dart_client.ENV_DATA_ROOT, str(tmp_path))

    counter = UsageCounter(limit=3)
    counter.tick("2026-08-17")

    expected = tmp_path / "logs" / dart_client.COUNTER_FILENAME
    assert counter.path == expected
    assert expected.exists()


def test_키가_없으면_사용량을_올리기_전에_멈춘다(monkeypatch):
    class Counter:
        calls = 0

        def tick(self):
            self.calls += 1

    counter = Counter()
    monkeypatch.delenv("DART_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="DART_API_KEY"):
        dart_client.get_json("company.json", {}, counter)

    assert counter.calls == 0


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, dart_client.DartAuthenticationError),
        (429, dart_client.DartLimitReached),
        (503, dart_client.DartResponseError),
    ],
)
def test_HTTP오류를_키없는_예외로_정규화한다(
    tmp_path, monkeypatch, status, error_type
):
    secret = "dart-secret-must-not-leak"
    monkeypatch.setenv("DART_API_KEY", secret)

    def fail(request_url, **_kwargs):
        raise urllib.error.HTTPError(request_url, status, "provider body", None, None)

    monkeypatch.setattr(dart_client.urllib.request, "urlopen", fail)
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)

    with pytest.raises(error_type) as caught:
        dart_client.get_json("company.json", {"corp_code": "001"}, counter)

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert secret not in rendered
    assert counter.today_count() == 1


def test_timeout을_비밀값없는_통신오류로_정규화한다(
    tmp_path, monkeypatch
):
    secret = "timeout-secret"
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError(secret)),
    )

    with pytest.raises(dart_client.DartTransportError) as caught:
        dart_client.get_json(
            "company.json", {}, UsageCounter(path=tmp_path / "usage.json", limit=10)
        )

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert secret not in rendered


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"message":"missing status"}'])
def test_깨진_JSON과_누락_응답을_계약오류로_막는다(
    tmp_path, monkeypatch, body
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(body)
    )

    with pytest.raises(dart_client.DartResponseError):
        dart_client.get_json(
            "company.json", {}, UsageCounter(path=tmp_path / "usage.json", limit=10)
        )


def test_zip이_아닌_다운로드응답의_원문을_반사하지_않는다(
    tmp_path, monkeypatch
):
    secret = "reflected-secret"
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            f"<error><message>{secret}</message></error>".encode()
        ),
    )

    with pytest.raises(dart_client.DartResponseError) as caught:
        dart_client.download_corpcode(
            tmp_path / "corp",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )

    assert secret not in str(caught.value)


def test_DART_JSON은_상한보다_한_바이트만_더_읽고_거부한다(
    tmp_path, monkeypatch
):
    secret = b"oversized-secret"
    response = _Response(secret)
    monkeypatch.setattr(dart_client, "JSON_RESPONSE_MAX_BYTES", 8)
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client.urllib.request, "urlopen", lambda *_args, **_kwargs: response
    )

    with pytest.raises(dart_client.DartResponseError) as caught:
        dart_client.get_json(
            "company.json", {}, UsageCounter(path=tmp_path / "usage.json", limit=10)
        )

    assert response.read_sizes == [9]
    assert secret.decode() not in str(caught.value)


@pytest.mark.parametrize(
    ("download", "cap_name"),
    [
        (dart_client.download_corpcode, "CORPCODE_ZIP_RESPONSE_MAX_BYTES"),
        (dart_client.download_document, "DOCUMENT_ZIP_RESPONSE_MAX_BYTES"),
    ],
)
def test_DART_ZIP응답은_용도별_압축크기_상한을_지킨다(
    tmp_path, monkeypatch, download, cap_name
):
    response = _Response(b"provider-body-secret")
    monkeypatch.setattr(dart_client, cap_name, 8)
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client.urllib.request, "urlopen", lambda *_args, **_kwargs: response
    )
    counter = UsageCounter(path=tmp_path / f"{cap_name}.json", limit=10)

    with pytest.raises(dart_client.DartResponseError) as caught:
        if download is dart_client.download_document:
            download("20260000000000", tmp_path / "document", counter)
        else:
            download(tmp_path / "corpcode", counter)

    assert response.read_sizes == [9]
    assert "provider-body-secret" not in str(caught.value)


def test_corpCode_ZIP의_선언_해제크기_초과를_파일생성전에_막는다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(dart_client, "CORPCODE_XML_MAX_BYTES", 8)
    monkeypatch.setattr(dart_client, "CORPCODE_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES", 8)
    body = _zip_bytes({"CORPCODE.xml": b"x" * 9})
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )
    dest_dir = tmp_path / "corpcode"

    with pytest.raises(dart_client.DartResponseError, match="선언 크기"):
        dart_client.download_corpcode(
            dest_dir, UsageCounter(path=tmp_path / "usage.json", limit=10)
        )

    assert not (dest_dir / "CORPCODE.xml").exists()


def test_공시ZIP의_전체_선언크기_초과를_막는다(tmp_path, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(dart_client, "DOCUMENT_MEMBER_MAX_BYTES", 8)
    monkeypatch.setattr(dart_client, "DOCUMENT_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES", 10)
    body = _zip_bytes({"first.xml": b"a" * 6, "second.xml": b"b" * 6})
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    with pytest.raises(dart_client.DartResponseError, match="전체 선언 크기"):
        dart_client.download_document(
            "20260000000000",
            tmp_path / "document",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_ZipFile_생성전에_중앙디렉터리_항목수를_제한한다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(dart_client, "DOCUMENT_ZIP_MAX_MEMBERS", 1)
    body = _zip_bytes({"first.xml": b"a", "second.xml": b"b"})
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    with pytest.raises(dart_client.DartResponseError, match="항목 수"):
        dart_client.download_document(
            "20260000000000",
            tmp_path / "document",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_분할_ZIP은_ZipFile_생성전에_거부한다(tmp_path, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    body = _replace_zip_end_u16(_zip_bytes({"CORPCODE.xml": b"data"}), 4, 1)
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    with pytest.raises(dart_client.DartResponseError, match="분할 ZIP"):
        dart_client.download_corpcode(
            tmp_path / "corpcode",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_ZIP64는_ZipFile_생성전에_거부한다(tmp_path, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    body = _zip_bytes({"CORPCODE.xml": b"data"})
    body = _replace_zip_end_u16(body, 8, 0xFFFF)
    body = _replace_zip_end_u16(body, 10, 0xFFFF)
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    with pytest.raises(dart_client.DartResponseError, match="ZIP64"):
        dart_client.download_corpcode(
            tmp_path / "corpcode",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_중앙디렉터리_크기_상한을_ZipFile_생성전에_검증한다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(dart_client, "CORPCODE_ZIP_CENTRAL_DIRECTORY_MAX_BYTES", 1)
    body = _zip_bytes({"CORPCODE.xml": b"data"})
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    with pytest.raises(dart_client.DartResponseError, match="중앙 디렉터리 크기"):
        dart_client.download_corpcode(
            tmp_path / "corpcode",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_암호화로_선언된_ZIP항목을_해제전에_거부한다(tmp_path, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    body = bytearray(_zip_bytes({"CORPCODE.xml": b"data"}))
    central_offset = body.find(b"PK\x01\x02")
    assert central_offset >= 0
    flags = struct.unpack_from("<H", body, central_offset + 8)[0]
    struct.pack_into("<H", body, central_offset + 8, flags | 0x1)
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(bytes(body)),
    )

    with pytest.raises(dart_client.DartResponseError, match="암호화된 항목"):
        dart_client.download_corpcode(
            tmp_path / "corpcode",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_corpCode_ZIP의_필수항목_중복을_거부한다(tmp_path, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    with pytest.warns(UserWarning, match="Duplicate name"):
        body = _zip_entry_bytes(
            [("CORPCODE.xml", b"first"), ("CORPCODE.xml", b"second")]
        )
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    with pytest.raises(dart_client.DartResponseError, match="필수 항목 구성"):
        dart_client.download_corpcode(
            tmp_path / "corpcode",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_ZIP의_과도한_압축비를_막는다(tmp_path, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(dart_client, "ZIP_MEMBER_MAX_COMPRESSION_RATIO", 1)
    body = _zip_bytes({"CORPCODE.xml": b"a" * 100})
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    with pytest.raises(dart_client.DartResponseError, match="압축비"):
        dart_client.download_corpcode(
            tmp_path / "corpcode",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_ZIP항목은_선언과_달라도_실제읽기_상한에서_멈춘다():
    info = zipfile.ZipInfo("document.xml")

    class Archive:
        def open(self, _info):
            return _Response(b"x" * 9)

    with pytest.raises(dart_client.DartResponseError, match="실제 크기"):
        dart_client._read_zip_member(
            Archive(),
            info,
            max_bytes=8,
            archive_label="시험 ZIP",
        )


def test_정상_corpCode와_공시ZIP은_명시한_파일만_저장한다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    bodies = iter(
        [
            _zip_bytes({"CORPCODE.xml": b"<result/>", "note.txt": b"ignored"}),
            _zip_bytes({"small.xml": b"small", "main.xml": b"main-document"}),
        ]
    )
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(next(bodies)),
    )
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)

    corp_path = dart_client.download_corpcode(tmp_path / "corpcode", counter)
    document_path = dart_client.download_document(
        "20260000000000", tmp_path / "document", counter
    )

    assert corp_path.read_bytes() == b"<result/>"
    assert document_path.read_bytes() == b"main-document"
