"""사용량 계수기 테스트 — 한도·경보·날짜 전환."""
from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import struct
import sys
import threading
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


def test_공시다운로드는_14자리_접수번호만_경로로_쓴다(tmp_path, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    called = False

    def forbidden_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("잘못된 접수번호로 네트워크를 열었습니다")

    monkeypatch.setattr(dart_client, "_urlopen", forbidden_open)

    with pytest.raises(dart_client.DartResponseError, match="접수번호"):
        dart_client.download_document(
            "../outside",
            tmp_path / "document",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )

    assert called is False
    assert not (tmp_path / "outside.xml").exists()


def test_공시다운로드는_Unicode숫자_14자리도_접수번호로_받지_않는다(
    tmp_path, monkeypatch
):
    called = False

    def forbidden_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("비 ASCII 접수번호로 네트워크를 열었습니다")

    monkeypatch.setattr(dart_client, "_urlopen", forbidden_open)

    with pytest.raises(dart_client.DartResponseError, match="접수번호"):
        dart_client.download_document("١" * 14, tmp_path / "document")

    assert called is False


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

    with pytest.raises(dart_client.DartAuthenticationError, match="DART_API_KEY"):
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

    monkeypatch.setattr(dart_client, "_urlopen", fail)
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
        dart_client,
        "_urlopen",
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
        dart_client, "_urlopen", lambda *_args, **_kwargs: _Response(body)
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
        dart_client,
        "_urlopen",
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
        dart_client, "_urlopen", lambda *_args, **_kwargs: response
    )

    with pytest.raises(dart_client.DartResponseError) as caught:
        dart_client.get_json(
            "company.json", {}, UsageCounter(path=tmp_path / "usage.json", limit=10)
        )

    assert response.read_sizes == [9]
    assert secret.decode() not in str(caught.value)


def test_DART_응답이_다른_host로_바뀌면_본문을_읽기전에_거부한다(
    tmp_path, monkeypatch
):
    class RedirectedResponse(_Response):
        def geturl(self):
            return "https://attacker.example/forged-dart-response"

    response = RedirectedResponse(b'{"status":"000"}')
    monkeypatch.setenv("DART_API_KEY", "query-secret-must-not-follow")
    monkeypatch.setattr(dart_client, "_urlopen", lambda *_args, **_kwargs: response)

    with pytest.raises(
        dart_client.DartResponseError,
        match="응답 위치",
    ):
        dart_client.get_json(
            "company.json",
            {},
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )

    assert response.read_sizes == []


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
        dart_client, "_urlopen", lambda *_args, **_kwargs: response
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
        dart_client,
        "_urlopen",
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
        dart_client,
        "_urlopen",
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
        dart_client,
        "_urlopen",
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
        dart_client,
        "_urlopen",
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
        dart_client,
        "_urlopen",
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
        dart_client,
        "_urlopen",
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
        dart_client,
        "_urlopen",
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
        dart_client,
        "_urlopen",
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
        dart_client,
        "_urlopen",
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
            _zip_bytes(
                {
                    "small.xml": b"<SMALL/>",
                    "main.xml": b"<DOC>main-document</DOC>",
                }
            ),
        ]
    )
    monkeypatch.setattr(
        dart_client,
        "_urlopen",
        lambda *_args, **_kwargs: _Response(next(bodies)),
    )
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)

    corp_path = dart_client.download_corpcode(tmp_path / "corpcode", counter)
    document_path = dart_client.download_document(
        "20260000000000", tmp_path / "document", counter
    )

    assert corp_path.read_bytes() == b"<result/>"
    assert document_path.read_bytes() == b"<DOC>main-document</DOC>"


def test_공시ZIP의_작은_XML에만_있는_URL도_versioned_sidecar에_남는다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000001"
    main = b"<DOC>" + (b"main-body-no-url" * 80) + b"</DOC>"
    attachment = (
        "<COVER>공식 홈페이지 https://small-company.example/company</COVER>"
    ).encode("utf-8")
    body = _zip_bytes({"main.xml": main, "covers/company.xml": attachment})
    monkeypatch.setattr(
        dart_client,
        "_urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    document_path = dart_client.download_document(
        receipt_no,
        tmp_path / "document",
        UsageCounter(path=tmp_path / "usage.json", limit=10),
    )
    sidecar_path = dart_client.document_url_sidecar_path(document_path)
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))

    assert document_path.read_bytes() == main
    assert payload["version"] == dart_client.DOCUMENT_URL_SIDECAR_VERSION
    assert payload["rcept_no"] == receipt_no
    assert payload["main_document_sha256"] == hashlib.sha256(main).hexdigest()
    assert payload["candidates"] == [
        {
            "url": "https://small-company.example/company",
            "source_member_name": "covers/company.xml",
            "source_location": payload["candidates"][0]["source_location"],
            "source_payload_sha256": hashlib.sha256(attachment).hexdigest(),
        }
    ]
    assert payload["candidates"][0]["source_location"].startswith(
        "raw_xml_chars:"
    )
    assert "main-body-no-url" not in sidecar_path.read_text(encoding="utf-8")


def test_sidecar가_없는_구버전_warm_cache도_한번_다시받아_보조근거를_채운다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000007"
    cache_dir = tmp_path / "document"
    cache_dir.mkdir()
    document_path = cache_dir / f"{receipt_no}.xml"
    old_document = b"<DOC>old representative cache</DOC>"
    document_path.write_bytes(old_document)
    refreshed = b"<DOC>refreshed representative cache</DOC>" * 10
    attachment = (
        "<COVER>공식 홈페이지 https://backfilled.example/company</COVER>"
    ).encode("utf-8")
    body = _zip_bytes({"main.xml": refreshed, "cover.xml": attachment})
    read_calls = 0

    def fake_read_url(*_args, **_kwargs):
        nonlocal read_calls
        read_calls += 1
        return body

    monkeypatch.setattr(dart_client, "_read_url", fake_read_url)

    returned = dart_client.download_document(
        receipt_no,
        cache_dir,
        UsageCounter(path=tmp_path / "usage.json", limit=10),
        require_official_url_sidecar=True,
    )
    loaded = dart_client.load_document_url_sidecar(
        returned,
        rcept_no=receipt_no,
        main_document=returned.read_bytes(),
    )

    assert read_calls == 1
    assert returned.read_bytes() == refreshed
    assert loaded.is_valid is True
    assert [candidate.url for candidate in loaded.candidates] == [
        "https://backfilled.example/company"
    ]


def test_v1기본호출은_sidecar없는_기존XML을_추가네트워크없이_재사용한다(
    tmp_path, monkeypatch
):
    receipt_no = "20260000000013"
    cache_dir = tmp_path / "document"
    cache_dir.mkdir()
    document_path = cache_dir / f"{receipt_no}.xml"
    old_document = b"<DOC>legacy warm cache</DOC>"
    document_path.write_bytes(old_document)

    def forbidden_read(*_args, **_kwargs):
        raise AssertionError("v1 warm cache에서 추가 다운로드를 열었습니다")

    monkeypatch.setattr(dart_client, "_read_url", forbidden_read)

    returned = dart_client.download_document(receipt_no, cache_dir)

    assert returned == document_path
    assert returned.read_bytes() == old_document


def test_후보가_0개인_정상_sidecar도_완성된_cache라_재다운로드하지_않는다(
    tmp_path, monkeypatch
):
    receipt_no = "20260000000008"
    cache_dir = tmp_path / "document"
    cache_dir.mkdir()
    document_path = cache_dir / f"{receipt_no}.xml"
    main = b"<DOC>valid cache without a URL</DOC>"
    document_path.write_bytes(main)
    dart_client.document_url_sidecar_path(document_path).write_bytes(
        dart_client._document_url_sidecar_bytes(
            rcept_no=receipt_no,
            main_document=main,
            ranked_candidates=[],
        )
    )

    def forbidden_read(*_args, **_kwargs):
        raise AssertionError("완성된 cache를 다시 다운로드했습니다")

    monkeypatch.setattr(dart_client, "_read_url", forbidden_read)

    returned = dart_client.download_document(
        receipt_no,
        cache_dir,
        require_official_url_sidecar=True,
    )

    assert returned == document_path
    assert returned.read_bytes() == main


def test_sidecar_loader는_작은멤버hash의_진위가아니라_형식과_대표XML결속만_확인한다(
    tmp_path,
):
    """작은 ZIP 원문은 저장하지 않으므로 hash를 암호학적 증명이라 부르지 않는다."""

    receipt_no = "20260000000012"
    document_path = tmp_path / f"{receipt_no}.xml"
    main = b"<DOC>representative</DOC>"
    document_path.write_bytes(main)
    asserted_download_time_hash = "a" * 64
    candidate = dart_client.DocumentUrlSidecarCandidate(
        url="https://provenance-only.example/",
        source_member_name="cover.xml",
        source_location="raw_xml_chars:10-30",
        source_payload_sha256=asserted_download_time_hash,
    )
    dart_client.document_url_sidecar_path(document_path).write_bytes(
        dart_client._document_url_sidecar_bytes(
            rcept_no=receipt_no,
            main_document=main,
            ranked_candidates=[(-100, 10, candidate)],
        )
    )

    loaded = dart_client.load_document_url_sidecar(
        document_path,
        rcept_no=receipt_no,
        main_document=main,
    )

    assert loaded.is_valid is True
    assert loaded.candidates[0].source_payload_sha256 == asserted_download_time_hash


def test_warm_cache_backfill_다운로드실패는_기존XML을_보존하고_다음호출도_재시도한다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000009"
    cache_dir = tmp_path / "document"
    cache_dir.mkdir()
    document_path = cache_dir / f"{receipt_no}.xml"
    old_document = b"<DOC>still usable old representative</DOC>"
    document_path.write_bytes(old_document)
    read_calls = 0

    def fail_read(*_args, **_kwargs):
        nonlocal read_calls
        read_calls += 1
        raise dart_client.DartTransportError("가짜 일시 장애")

    monkeypatch.setattr(dart_client, "_read_url", fail_read)
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)

    with pytest.raises(dart_client.DartTransportError):
        dart_client.download_document(
            receipt_no,
            cache_dir,
            counter,
            require_official_url_sidecar=True,
        )
    with pytest.raises(dart_client.DartTransportError):
        dart_client.download_document(
            receipt_no,
            cache_dir,
            counter,
            require_official_url_sidecar=True,
        )

    assert document_path.read_bytes() == old_document
    assert not dart_client.document_url_sidecar_path(document_path).exists()
    assert read_calls == 2


def test_같은접수번호의_동시_warm_cache_backfill은_한번만_다운로드한다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000010"
    cache_dir = tmp_path / "document"
    cache_dir.mkdir()
    document_path = cache_dir / f"{receipt_no}.xml"
    document_path.write_bytes(b"<DOC>old cache</DOC>")
    refreshed = b"<DOC>new cache</DOC>" * 10
    body = _zip_bytes(
        {
            "main.xml": refreshed,
            "cover.xml": b"<COVER>https://concurrent.example/</COVER>",
        }
    )
    calls_lock = threading.Lock()
    start = threading.Barrier(3)
    read_calls = 0

    def fake_read_url(*_args, **_kwargs):
        nonlocal read_calls
        with calls_lock:
            read_calls += 1
        return body

    monkeypatch.setattr(dart_client, "_read_url", fake_read_url)
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)

    def download() -> Path:
        start.wait()
        return dart_client.download_document(
            receipt_no,
            cache_dir,
            counter,
            require_official_url_sidecar=True,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(download) for _index in range(2)]
        start.wait()
        results = [future.result(timeout=10) for future in futures]

    loaded = dart_client.load_document_url_sidecar(
        document_path,
        rcept_no=receipt_no,
        main_document=document_path.read_bytes(),
    )
    assert results == [document_path, document_path]
    assert read_calls == 1
    assert loaded.is_valid is True
    assert document_path.read_bytes() == refreshed


def test_더큰_비XML첨부가_있어도_대표문서는_XML중에서만_고른다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000004"
    xml = b"<DOC>real filing xml</DOC>"
    body = _zip_bytes(
        {
            "document.xml": xml,
            "huge-attachment.pdf": b"%PDF-" + (b"binary" * 200),
        }
    )
    monkeypatch.setattr(
        dart_client,
        "_urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    document_path = dart_client.download_document(
        receipt_no,
        tmp_path / "document",
        UsageCounter(path=tmp_path / "usage.json", limit=10),
    )

    assert document_path.read_bytes() == xml


def test_확장자만_XML인_큰바이너리보다_실제XML을_대표로_고른다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000006"
    xml = b"<DOC>real filing xml</DOC>"
    body = _zip_bytes(
        {
            "fake-large.xml": b"%PDF-" + (b"binary" * 200),
            "real-small.xml": xml,
        }
    )
    monkeypatch.setattr(
        dart_client,
        "_urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    document_path = dart_client.download_document(
        receipt_no,
        tmp_path / "document",
        UsageCounter(path=tmp_path / "usage.json", limit=10),
    )

    assert document_path.read_bytes() == xml


def test_XML이_하나도_없는_공시ZIP은_형식을_추측하지_않는다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    body = _zip_bytes({"attachment.pdf": b"%PDF-binary"})
    monkeypatch.setattr(
        dart_client,
        "_urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    with pytest.raises(dart_client.DartResponseError, match="XML 문서"):
        dart_client.download_document(
            "20260000000005",
            tmp_path / "document",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )


def test_공시URL_sidecar_후보수는_전체_XML을_합쳐도_상한을_넘지_않는다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000002"
    urls = " ".join(
        f"https://company-{index:02d}.example/" for index in range(30)
    )
    body = _zip_bytes({"main.xml": f"<DOC>{urls}</DOC>".encode("utf-8")})
    monkeypatch.setattr(
        dart_client,
        "_urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )

    document_path = dart_client.download_document(
        receipt_no,
        tmp_path / "document",
        UsageCounter(path=tmp_path / "usage.json", limit=10),
    )
    payload = json.loads(
        dart_client.document_url_sidecar_path(document_path).read_text(
            encoding="utf-8"
        )
    )

    assert len(payload["candidates"]) == dart_client.DOCUMENT_URL_SIDECAR_MAX_CANDIDATES


def test_공시URL_top_k는_입력URL수만큼_메모리를_키우지_않고_뒤의_강한후보도_살린다():
    """최종 12개 계약이 중간 list에도 적용되어야 ZIP 메모리 DoS가 닫힌다."""

    noise = " ".join(
        f"https://noise-{index:05d}.example/path"
        for index in range(2_000)
    )
    official = "https://late-official.example/"
    raw = f"<DOC>{noise} 공식 홈페이지 {official}</DOC>".encode("utf-8")

    ranked = dart_client._ranked_document_web_url_candidates(
        raw,
        member_name="main.xml",
    )

    assert len(ranked) <= dart_client.DOCUMENT_URL_SIDECAR_MAX_CANDIDATES
    assert official in {item[2].url for item in ranked}


def test_일반_URL태그_12개가_뒤의_명시적_공식홈페이지를_밀어내지_않는다():
    generic_rows = "".join(
        f"<URL>https://external-{index:02d}.example/</URL>"
        + ("x" * 240)
        for index in range(20)
    )
    official = "https://actual-company.example/company"
    raw = (
        f"<DOC>{generic_rows}<P>공식 홈페이지 {official}</P></DOC>"
    ).encode("utf-8")

    candidates = dart_client.extract_document_web_url_candidates(
        raw,
        member_name="main.xml",
    )

    assert len(candidates) <= dart_client.DOCUMENT_URL_SIDECAR_MAX_CANDIDATES
    assert official in {candidate.url for candidate in candidates}


def test_대표XML_원자저장이_실패하면_앞서쓴_sidecar도_남기지_않는다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000003"
    body = _zip_bytes(
        {"main.xml": b"<DOC>https://small-company.example/</DOC>"}
    )
    monkeypatch.setattr(
        dart_client,
        "_urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )
    original_write = dart_client._write_private_bytes_atomic

    def fail_main(path: Path, data: bytes) -> None:
        if path.name == f"{receipt_no}.xml":
            raise OSError("가짜 대표 XML 쓰기 실패")
        original_write(path, data)

    monkeypatch.setattr(dart_client, "_write_private_bytes_atomic", fail_main)
    document_path = tmp_path / "document" / f"{receipt_no}.xml"

    with pytest.raises(OSError, match="대표 XML"):
        dart_client.download_document(
            receipt_no,
            tmp_path / "document",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )

    assert not document_path.exists()
    assert not dart_client.document_url_sidecar_path(document_path).exists()
    assert list((tmp_path / "document").glob("*.tmp")) == []


def test_warm_cache_backfill_교체실패도_기존XML을_훼손하지_않는다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000011"
    cache_dir = tmp_path / "document"
    cache_dir.mkdir()
    document_path = cache_dir / f"{receipt_no}.xml"
    old_document = b"<DOC>old cache must survive</DOC>"
    document_path.write_bytes(old_document)
    body = _zip_bytes(
        {"main.xml": b"<DOC>https://replacement.example/</DOC>" * 10}
    )
    monkeypatch.setattr(dart_client, "_read_url", lambda *_args, **_kwargs: body)
    original_write = dart_client._write_private_bytes_atomic

    def fail_main(path: Path, data: bytes) -> None:
        if path == document_path:
            raise OSError("가짜 대표 XML 교체 실패")
        original_write(path, data)

    monkeypatch.setattr(dart_client, "_write_private_bytes_atomic", fail_main)

    with pytest.raises(OSError, match="교체 실패"):
        dart_client.download_document(
            receipt_no,
            cache_dir,
            UsageCounter(path=tmp_path / "usage.json", limit=10),
            require_official_url_sidecar=True,
        )

    assert document_path.read_bytes() == old_document
    assert not dart_client.document_url_sidecar_path(document_path).exists()
    assert list(cache_dir.glob("*.tmp")) == []


def test_FULL_warm_cache_sidecar저장실패도_기존XML을_보존하고_실패를_알린다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    receipt_no = "20260000000014"
    cache_dir = tmp_path / "document"
    cache_dir.mkdir()
    document_path = cache_dir / f"{receipt_no}.xml"
    sidecar_path = dart_client.document_url_sidecar_path(document_path)
    old_document = b"<DOC>old cache must survive sidecar failure</DOC>"
    document_path.write_bytes(old_document)
    body = _zip_bytes(
        {"main.xml": b"<DOC>https://replacement.example/</DOC>" * 10}
    )
    monkeypatch.setattr(dart_client, "_read_url", lambda *_args, **_kwargs: body)
    original_write = dart_client._write_private_bytes_atomic

    def fail_sidecar(path: Path, data: bytes) -> None:
        if path == sidecar_path:
            raise OSError("가짜 sidecar 저장 실패")
        original_write(path, data)

    monkeypatch.setattr(dart_client, "_write_private_bytes_atomic", fail_sidecar)

    with pytest.raises(dart_client.DartResponseError, match="sidecar"):
        dart_client.download_document(
            receipt_no,
            cache_dir,
            UsageCounter(path=tmp_path / "usage.json", limit=10),
            require_official_url_sidecar=True,
        )

    assert document_path.read_bytes() == old_document
    assert not sidecar_path.exists()
