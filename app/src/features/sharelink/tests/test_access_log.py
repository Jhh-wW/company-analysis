import logging

from src.features.sharelink.access_log import CapabilityAccessLogFilter


def _uvicorn_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", path, "1.1", 303),
        None,
    )


def test_LINK_원문은_uvicorn_접근로그에서_가린다():
    raw_key = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
    record = _uvicorn_record(f"/k/{raw_key}?from=messenger")

    assert CapabilityAccessLogFilter().filter(record) is True

    rendered = record.getMessage()
    assert raw_key not in rendered
    assert "127.0.0.1" not in rendered
    assert "[CLIENT_REDACTED]" in rendered
    assert "from=messenger" not in rendered
    assert "/k/[LINK_REDACTED]" in rendered


def test_안전한_관리자_해시와_일반_보고서_ID는_로그에서_유지한다():
    report_id = "b" * 32
    key_hash = "c" * 64
    report_record = _uvicorn_record(f"/result/{report_id}")
    admin_record = _uvicorn_record(f"/admin/link/{key_hash}")
    filter_ = CapabilityAccessLogFilter()

    filter_.filter(report_record)
    filter_.filter(admin_record)

    assert report_id in report_record.getMessage()
    assert key_hash in admin_record.getMessage()


def test_구형과_신형_관리자_주소의_raw_LINK를_모두_가린다():
    raw_key = "0f1e2d3c4b5a69780f1e2d3c4b5a6978"
    filter_ = CapabilityAccessLogFilter()

    for path in (f"/admin/link/{raw_key}", f"/admin/links/{raw_key}"):
        record = _uvicorn_record(path)
        filter_.filter(record)
        assert raw_key not in record.getMessage()
        assert "[LINK_REDACTED]" in record.getMessage()
