"""공고 이미지 → 글자 추출 시험.

★ 진짜 AI를 부르지 않는다 (팀장 지시 — 실제 AI 호출 금지). `extract`는 항상
  가짜 함수를 주입한다. `default_extract`도 시험에서는 «부르지 않고» 존재만 본다.

정본: 확정/01_식별/1_흐름/03_공고판별.md, 확정/99_기준/2_규칙/01_안전과가드레일.md S1·S2
"""

from __future__ import annotations

import logging

import pytest

from src.features.posting_image import constants, logic


# ══════════════════════════════════════════════════════════
# 시험용 가짜 이미지 · 가짜 추출기
# ══════════════════════════════════════════════════════════

def _fake_png(body: bytes = b"\x00" * 100) -> bytes:
    return constants.MAGIC_PNG + body


def _fake_jpeg(body: bytes = b"\x00" * 100) -> bytes:
    return constants.MAGIC_JPEG + body


def _fake_webp(body: bytes = b"\x00" * 100) -> bytes:
    return constants.MAGIC_RIFF + b"\x00\x00\x00\x00" + constants.MAGIC_WEBP + body


def _ok_extract(text: str = "자격요건: 3년 이상") -> logic.ExtractFn:
    def _inner(images: list[bytes]) -> logic.ExtractResult:
        return logic.ExtractResult(text=text, looks_like_posting=True)

    return _inner


def _empty_extract(images: list[bytes]) -> logic.ExtractResult:
    return logic.ExtractResult(text="")


def _raising_extract(images: list[bytes]) -> logic.ExtractResult:
    raise RuntimeError("가짜 실패 — 주민번호 900101-1234567 포함 오류 메시지")


# ══════════════════════════════════════════════════════════
# 형식 검사 — 매직 바이트 기준 (확장자 아님)
# ══════════════════════════════════════════════════════════

def test_형식_거부_지원안하는포맷():
    gif_like = b"GIF89a" + b"\x00" * 100
    result = logic.extract_posting_text([gif_like], extract=_ok_extract())
    assert result.ok is False
    assert result.error == constants.ERROR_UNSUPPORTED_FORMAT


def test_형식_거부_매직바이트_없는_텍스트파일():
    fake = b"this is not an image" * 10
    result = logic.extract_posting_text([fake], extract=_ok_extract())
    assert result.ok is False
    assert result.error == constants.ERROR_UNSUPPORTED_FORMAT


@pytest.mark.parametrize("maker", [_fake_png, _fake_jpeg, _fake_webp])
def test_형식_통과_허용포맷_3종(maker):
    result = logic.extract_posting_text([maker()], extract=_ok_extract("자격요건 있음"))
    assert result.ok is True
    assert result.text == "자격요건 있음"


def test_여러장중_하나만_형식이_틀려도_전체_거부():
    images = [_fake_png(), b"not-an-image"]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is False
    assert result.error == constants.ERROR_UNSUPPORTED_FORMAT


# ══════════════════════════════════════════════════════════
# 크기 초과
# ══════════════════════════════════════════════════════════

def test_크기_초과_거부():
    too_big = _fake_png(b"\x00" * constants.MAX_IMAGE_BYTES)  # 매직 바이트만큼 더 큼
    result = logic.extract_posting_text([too_big], extract=_ok_extract())
    assert result.ok is False
    assert "MB" in result.error


def test_크기_경계값_통과():
    body_len = constants.MAX_IMAGE_BYTES - len(constants.MAGIC_PNG)
    exactly_max = _fake_png(b"\x00" * body_len)
    assert len(exactly_max) == constants.MAX_IMAGE_BYTES
    result = logic.extract_posting_text([exactly_max], extract=_ok_extract())
    assert result.ok is True


def test_전체용량_초과_거부():
    # 1장씩은 상한(MAX_IMAGE_BYTES) 안쪽이지만, 몇 장을 모으면 합계 상한을 넘기는 경우.
    each = constants.MAX_IMAGE_BYTES - 1024
    count = constants.MAX_TOTAL_BYTES // each + 1
    assert each <= constants.MAX_IMAGE_BYTES  # 개별 크기 상한은 안 걸림을 보장
    assert count <= constants.MAX_IMAGE_COUNT  # 장수 상한은 안 걸림을 보장 — 합계만 걸려야 함
    body_len = each - len(constants.MAGIC_PNG)
    images = [_fake_png(b"\x00" * body_len) for _ in range(count)]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is False
    assert "MB" in result.error


# ══════════════════════════════════════════════════════════
# 장수 초과
# ══════════════════════════════════════════════════════════

def test_장수_초과_거부():
    images = [_fake_png() for _ in range(constants.MAX_IMAGE_COUNT + 1)]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is False
    assert result.error == constants.ERROR_TOO_MANY.format(
        count=len(images), limit=constants.MAX_IMAGE_COUNT
    )


def test_장수_경계값_통과():
    images = [_fake_png() for _ in range(constants.MAX_IMAGE_COUNT)]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is True


# ══════════════════════════════════════════════════════════
# 빈 이미지
# ══════════════════════════════════════════════════════════

def test_빈_이미지_목록_거부():
    result = logic.extract_posting_text([], extract=_ok_extract())
    assert result.ok is False
    assert result.error == constants.ERROR_EMPTY
    assert result.failure_kind == "input"


def test_빈_바이트_파일_거부():
    result = logic.extract_posting_text([b""], extract=_ok_extract())
    assert result.ok is False
    assert result.error == constants.ERROR_EMPTY_FILE
    assert result.failure_kind == "input"


# ══════════════════════════════════════════════════════════
# 추출 실패 — 막다른 길을 만들지 않는다 (텍스트 경로 안내)
# ══════════════════════════════════════════════════════════

def test_추출_결과가_빈_문자열이면_실패로_처리():
    result = logic.extract_posting_text([_fake_png()], extract=_empty_extract)
    assert result.ok is False
    assert result.error == constants.ERROR_EXTRACT_FAILED
    assert result.failure_kind == "input"


def test_공백만_있는_추출결과도_실패():
    blank = lambda imgs: logic.ExtractResult(text="   \n  ")  # noqa: E731
    result = logic.extract_posting_text([_fake_png()], extract=blank)
    assert result.ok is False
    assert result.error == constants.ERROR_EXTRACT_FAILED


def test_AI_호출이_예외를_던지면_텍스트경로_안내로_실패():
    result = logic.extract_posting_text([_fake_png()], extract=_raising_extract)
    assert result.ok is False
    assert result.error == constants.ERROR_EXTRACT_FAILED
    assert "글자로 붙여넣어" in result.error
    assert result.failure_kind == "technical"


# ══════════════════════════════════════════════════════════
# 원본 이미지가 안 남는지 (S2 — 이미지 원본 잔존 0건)
# ══════════════════════════════════════════════════════════

def test_원본_바이트가_디스크에_남지_않는다(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    images = [_fake_png(b"\xAB" * 500), _fake_jpeg(b"\xCD" * 500)]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is True
    assert list(tmp_path.iterdir()) == []  # 아무 파일도 안 생겼다


def test_원본_바이트가_모듈_전역에_남지_않는다():
    marker = b"\xEE" * 777
    images = [_fake_png(marker)]
    logic.extract_posting_text(images, extract=_ok_extract())

    def _contains_marker(value: object) -> bool:
        if isinstance(value, (bytes, bytearray)):
            return marker in value
        if isinstance(value, dict):
            return any(_contains_marker(v) for v in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(_contains_marker(v) for v in value)
        return False

    for name, value in vars(logic).items():
        assert not _contains_marker(value), f"logic.{name}에 이미지 바이트가 남아 있음"


# ══════════════════════════════════════════════════════════
# 개인정보가 로그에 안 남는지 (S1 — 저장·출력에 개인정보 0건)
# ══════════════════════════════════════════════════════════

def test_추출_실패_로그에_예외_메시지_내용이_안_남는다(caplog):
    caplog.set_level(logging.DEBUG)
    logic.extract_posting_text([_fake_png()], extract=_raising_extract)
    assert "900101" not in caplog.text
    assert "주민번호" not in caplog.text
    # ★ 예외 «종류 이름»만 남아야 한다.
    assert "RuntimeError" in caplog.text


def test_성공한_추출_텍스트는_로그에_안_남는다(caplog):
    caplog.set_level(logging.DEBUG)
    secret_looking_text = "이름 홍길동 연락처 010-1234-5678"
    logic.extract_posting_text([_fake_png()], extract=_ok_extract(secret_looking_text))
    assert secret_looking_text not in caplog.text


# ══════════════════════════════════════════════════════════
# 형식 검사가 AI 호출보다 먼저인지 (비용 방어)
# ══════════════════════════════════════════════════════════

def test_형식_위반이면_AI를_아예_부르지_않는다():
    called = {"count": 0}

    def _spy_extract(images: list[bytes]) -> logic.ExtractResult:
        called["count"] += 1
        return logic.ExtractResult(text="불렸으면 안 됨")

    result = logic.extract_posting_text([b"not-an-image"], extract=_spy_extract)
    assert result.ok is False
    assert called["count"] == 0


def test_장수_위반이면_AI를_아예_부르지_않는다():
    called = {"count": 0}

    def _spy_extract(images: list[bytes]) -> logic.ExtractResult:
        called["count"] += 1
        return logic.ExtractResult(text="불렸으면 안 됨")

    images = [_fake_png() for _ in range(constants.MAX_IMAGE_COUNT + 1)]
    result = logic.extract_posting_text(images, extract=_spy_extract)
    assert result.ok is False
    assert called["count"] == 0


# ══════════════════════════════════════════════════════════
# 기본 구현(default_extract) — 실제로 부르지 않는다. 존재·시그니처만 확인.
# ══════════════════════════════════════════════════════════

def test_기본_추출기는_존재하고_호출가능하다():
    # ★ 절대 실행하지 않는다 — 부르면 anthropic을 불러오고 실제 API를 두드린다(비용).
    assert callable(logic.default_extract)
