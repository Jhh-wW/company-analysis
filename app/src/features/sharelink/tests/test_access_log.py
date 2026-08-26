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


def _app_record(msg: str, args) -> logging.LogRecord:
    """앱 코드가 남기는 모양의 레코드 (uvicorn 접근 로그가 아니다)."""
    return logging.LogRecord(
        "src.web.routers.reports", logging.INFO, __file__, 1, msg, args, None
    )


def test_형식문자열에_경로가_있고_열쇠가_인자로_와도_가린다():
    """★ msg 와 args 를 «따로» 보면 놓치는 모양이다 (2026-08-26 실측).

    예: logger.info("열람 링크 /k/%s 를 처리했습니다", key)
    합쳐야 비로소 열쇠 경로가 되므로 양쪽 검사 모두 통과해 버렸다.
    """
    raw_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    filter_ = CapabilityAccessLogFilter()

    for msg, args in (
        ("열람 링크 /k/%s 를 처리했습니다", (raw_key,)),
        ("열람 링크 /k/%s.json 요청", (raw_key,)),
        ("관리자 주소 /admin/link/%s 확인", (raw_key,)),
        ("링크 %s /k/%s 처리", ("보고서", raw_key)),
    ):
        record = _app_record(msg, args)
        filter_.filter(record)
        rendered = record.getMessage()
        assert raw_key not in rendered, f"{msg!r} 에서 열쇠가 샜다"
        assert "[LINK_REDACTED]" in rendered


class _포맷_감시:
    """문자열로 바뀔 때마다 «몇 번 불렸는지» 센다."""

    def __init__(self) -> None:
        self.호출수 = 0

    def __str__(self) -> str:
        self.호출수 += 1
        return "값"


def test_경로가_없는_로그는_미리_포맷하지_않는다():
    """싼 검사로 걸러서 «필요할 때만 문자열을 만든다»는 이점을 지킨다.

    ★ args 가 그대로인지만 보면 이걸 못 잡는다 — 사전검사를 빼도 「링크가 없다」로
      되돌아가서 args 는 그대로이기 때문이다. 실제로 «포맷이 실행됐는지»를 세야 한다
      (2026-08-26 음성 대조에서 이 시험이 초록불로 통과해 버려 고쳤다).
    """
    감시 = _포맷_감시()
    record = _app_record("보고서 %s 를 만들었습니다", (감시,))

    CapabilityAccessLogFilter().filter(record)

    assert 감시.호출수 == 0, "경로 접두어가 없는데도 미리 포맷했다"
    assert record.getMessage() == "보고서 값 를 만들었습니다"
    assert 감시.호출수 == 1


def test_경로가_있으면_한_번만_미리_포맷한다():
    """접두어가 보이면 포맷한다 — 그래야 합쳐진 열쇠를 볼 수 있다."""
    감시 = _포맷_감시()
    record = _app_record("/k/%s 를 처리했습니다", (감시,))

    CapabilityAccessLogFilter().filter(record)

    assert 감시.호출수 == 1


def test_포맷이_깨진_레코드에도_예외를_내지_않는다():
    """로깅 필터가 예외를 내면 «본 작업»이 죽는다. 원본을 그대로 둔다."""
    record = _app_record("/k/%s 와 %s", ("only-one",))

    assert CapabilityAccessLogFilter().filter(record) is True
