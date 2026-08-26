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


# ══════════════════════════════════════════════════════════
# 뒷경계 우회 (2026-08-26 적대 검수가 실제로 뚫은 것)
# ══════════════════════════════════════════════════════════


def test_열쇠_뒤에_다른_글자가_붙어도_가린다():
    """★ 예전 정규식은 «슬래시·공백·따옴표·끝»만 경계로 인정해 뚫렸다.

    실전 경로 — 링크를 문장 끝에 붙여 공유하면 마침표가 따라붙고, 링크 미리보기
    크롤러가 그 주소를 그대로 요청하면 접근 로그에 원문 열쇠가 찍힌다.
    """
    raw_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    filter_ = CapabilityAccessLogFilter()

    붙는_것 = (".json", ".", "\uff09", "\ub97c", ")", ",", ".png?x=1")
    for 꼬리 in 붙는_것:
        record = _uvicorn_record(f"/k/{raw_key}{꼬리}")
        filter_.filter(record)
        rendered = record.getMessage()
        assert raw_key not in rendered, f"꼬리 {꼬리!r} 에서 열쇠가 샜다"
        assert "[LINK_REDACTED]" in rendered


def test_관리자_주소도_꼬리가_붙으면_가린다():
    raw_key = "0f1e2d3c4b5a69780f1e2d3c4b5a6978"
    filter_ = CapabilityAccessLogFilter()

    for path in (f"/admin/link/{raw_key}.json", f"/admin/links/{raw_key}."):
        record = _uvicorn_record(path)
        filter_.filter(record)
        assert raw_key not in record.getMessage()


def test_뒤에_hex가_더_붙으면_열쇠가_아니므로_건드리지_않는다():
    """★ 이게 64자리 관리자 해시를 지킨다.

    「32자 이상 전부 가림」으로 넓히면 추적용으로 «일부러 남기는» 해시까지
    지워진다. 발급 열쇠는 언제나 정확히 32자다.
    """
    filter_ = CapabilityAccessLogFilter()
    for 값 in ("d" * 33, "e" * 40, "c" * 64):
        record = _uvicorn_record(f"/admin/link/{값}")
        filter_.filter(record)
        assert 값 in record.getMessage()
