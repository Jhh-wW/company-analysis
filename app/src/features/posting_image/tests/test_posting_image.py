"""공고 이미지 → 글자 추출 시험.

★ 진짜 AI를 부르지 않는다 (팀장 지시 — 실제 AI 호출 금지). `extract`는 항상
  가짜 함수를 주입한다. `default_extract`도 시험에서는 «부르지 않고» 존재만 본다.

정본: 확정/01_식별/1_흐름/03_공고판별.md, 확정/99_기준/2_규칙/01_안전과가드레일.md S1·S2
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from src.features.posting_image import constants, logic


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1", 1),
        ("0", 0),
    ],
)
def test_운영환경은_이미지상한을_낮출수만_있다(monkeypatch, raw, expected):
    monkeypatch.setenv(constants.ENV_MAX_IMAGE_COUNT, raw)
    assert (
        constants._lowerable_limit(
            constants.ENV_MAX_IMAGE_COUNT, constants.HARD_MAX_IMAGE_COUNT
        )
        == expected
    )


@pytest.mark.parametrize(
    "raw",
    ["-1", "not-a-number", "1.5", str(constants.HARD_MAX_IMAGE_COUNT + 1)],
)
def test_운영환경_이미지상한_잘못된값은_시작오류(monkeypatch, raw):
    monkeypatch.setenv(constants.ENV_MAX_IMAGE_COUNT, raw)
    with pytest.raises(RuntimeError, match=constants.ENV_MAX_IMAGE_COUNT):
        constants._lowerable_limit(
            constants.ENV_MAX_IMAGE_COUNT, constants.HARD_MAX_IMAGE_COUNT
        )


# ══════════════════════════════════════════════════════════
# 시험용 실제 인코딩 이미지 · 가짜 추출기
# ══════════════════════════════════════════════════════════

def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(255, 255, 255)).save(output, image_format)
    return output.getvalue()


def _valid_png(trailing: bytes = b"") -> bytes:
    return _image_bytes("PNG") + trailing


def _valid_jpeg(trailing: bytes = b"") -> bytes:
    return _image_bytes("JPEG") + trailing


def _valid_webp(trailing: bytes = b"") -> bytes:
    return _image_bytes("WEBP") + trailing


def _verify_pass_load_fail_jpeg() -> bytes:
    # Pillow 12.3.0의 JPEG verify는 컨테이너를 통과시키지만, 마지막 바이트가
    # 잘린 실제 픽셀 스트림은 load에서 OSError로 거부한다.
    return _valid_jpeg()[:-1]


def _verify_pass_load_fail_webp() -> bytes:
    # 단일 VP8 WebP의 픽셀 스트림 끝을 자르고 컨테이너·청크 길이만 실제 길이로
    # 맞춘다. 컨테이너 구조 verify는 통과하지만 libwebp 픽셀 디코딩은 실패한다.
    damaged = bytearray(_valid_webp()[:-2])
    assert damaged[12:16] == b"VP8 "
    damaged[4:8] = (len(damaged) - 8).to_bytes(4, "little")
    damaged[16:20] = (len(damaged) - 20).to_bytes(4, "little")
    return bytes(damaged)


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


@pytest.mark.parametrize(
    "maker", [_valid_png, _valid_jpeg, _valid_webp], ids=["PNG", "JPEG", "WEBP"]
)
def test_형식과_실제픽셀디코딩_통과_허용포맷_3종(maker):
    result = logic.extract_posting_text([maker()], extract=_ok_extract("자격요건 있음"))
    assert result.ok is True
    assert result.text == "자격요건 있음"


def test_PNG_매직바이트만_붙인_가짜는_OCR전에_거부한다():
    calls = 0

    def extractor(_images: list[bytes]) -> logic.ExtractResult:
        nonlocal calls
        calls += 1
        return logic.ExtractResult(text="호출되면 안 됨")

    result = logic.extract_posting_text(
        [constants.MAGIC_PNG + b"not-a-real-png"], extract=extractor
    )

    assert result.ok is False
    assert result.error == constants.ERROR_INVALID_IMAGE
    assert result.failure_kind == "input"
    assert calls == 0


def test_잘린_이미지는_OCR전에_거부한다():
    calls = 0

    def extractor(_images: list[bytes]) -> logic.ExtractResult:
        nonlocal calls
        calls += 1
        return logic.ExtractResult(text="호출되면 안 됨")

    truncated = _valid_png()[:-12]
    result = logic.extract_posting_text([truncated], extract=extractor)

    assert result.ok is False
    assert result.error == constants.ERROR_INVALID_IMAGE
    assert result.failure_kind == "input"
    assert calls == 0


@pytest.mark.parametrize(
    ("maker", "expected_format"),
    [
        (_verify_pass_load_fail_jpeg, "JPEG"),
        (_verify_pass_load_fail_webp, "WEBP"),
    ],
    ids=["truncated-jpeg", "corrupt-webp"],
)
def test_verify통과_load실패_이미지도_OCR전에_거부한다(maker, expected_format):
    damaged = maker()

    # 표본 자체가 이번 회귀 조건을 실제로 만족하는지 고정한다. PNG 절단 표본은
    # Pillow verify 단계에서 이미 실패하므로 억지로 이 목록에 넣지 않는다.
    with Image.open(BytesIO(damaged), formats=(expected_format,)) as image:
        assert image.format == expected_format
        image.verify()
    with pytest.raises(OSError):
        with Image.open(BytesIO(damaged), formats=(expected_format,)) as image:
            image.load()

    calls = 0

    def extractor(_images: list[bytes]) -> logic.ExtractResult:
        nonlocal calls
        calls += 1
        return logic.ExtractResult(text="호출되면 안 됨")

    result = logic.extract_posting_text([damaged], extract=extractor)

    assert result.ok is False
    assert result.error == constants.ERROR_INVALID_IMAGE
    assert result.failure_kind == "input"
    assert calls == 0


def test_픽셀상한을_넘는_이미지는_OCR전에_거부한다(monkeypatch):
    monkeypatch.setattr(constants, "MAX_IMAGE_PIXELS", 3)
    calls = 0

    def extractor(_images: list[bytes]) -> logic.ExtractResult:
        nonlocal calls
        calls += 1
        return logic.ExtractResult(text="호출되면 안 됨")

    result = logic.extract_posting_text([_valid_png()], extract=extractor)

    assert result.ok is False
    assert result.error == constants.ERROR_RESOLUTION_TOO_LARGE
    assert result.failure_kind == "input"
    assert calls == 0


def test_한변_해상도상한을_넘는_이미지는_OCR전에_거부한다(monkeypatch):
    monkeypatch.setattr(constants, "MAX_IMAGE_DIMENSION", 1)

    result = logic.extract_posting_text([_valid_png()], extract=_ok_extract())

    assert result.ok is False
    assert result.error == constants.ERROR_RESOLUTION_TOO_LARGE
    assert result.failure_kind == "input"


def test_Pillow_압축폭탄_경고도_입력오류로_거부한다(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 2)

    result = logic.extract_posting_text([_valid_png()], extract=_ok_extract())

    assert result.ok is False
    assert result.error == constants.ERROR_INVALID_IMAGE
    assert result.failure_kind == "input"


def test_Pillow가_운영_Docker_설치의존성에_고정돼있다():
    app_root = Path(__file__).resolve().parents[4]
    requirements = (app_root / "requirements.txt").read_text(encoding="utf-8")
    dockerfile = (app_root / "Dockerfile").read_text(encoding="utf-8")

    assert "pillow==12.3.0" in requirements.lower()
    assert "COPY app/requirements.txt /tmp/requirements.txt" in dockerfile
    assert "pip install --requirement /tmp/requirements.txt" in dockerfile


def test_여러장중_하나만_형식이_틀려도_전체_거부():
    images = [_valid_png(), b"not-an-image"]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is False
    assert result.error == constants.ERROR_UNSUPPORTED_FORMAT


# ══════════════════════════════════════════════════════════
# 크기 초과
# ══════════════════════════════════════════════════════════

def test_크기_초과_거부():
    too_big = _valid_png(b"\x00" * constants.MAX_IMAGE_BYTES)
    result = logic.extract_posting_text([too_big], extract=_ok_extract())
    assert result.ok is False
    assert "MB" in result.error


def test_크기_경계값_통과():
    valid = _valid_png()
    exactly_max = valid + b"\x00" * (constants.MAX_IMAGE_BYTES - len(valid))
    assert len(exactly_max) == constants.MAX_IMAGE_BYTES
    result = logic.extract_posting_text([exactly_max], extract=_ok_extract())
    assert result.ok is True


def test_전체용량_초과_거부():
    # 1장씩은 상한(MAX_IMAGE_BYTES) 안쪽이지만, 몇 장을 모으면 합계 상한을 넘기는 경우.
    each = constants.MAX_IMAGE_BYTES - 1024
    count = constants.MAX_TOTAL_BYTES // each + 1
    assert each <= constants.MAX_IMAGE_BYTES  # 개별 크기 상한은 안 걸림을 보장
    assert count <= constants.MAX_IMAGE_COUNT  # 장수 상한은 안 걸림을 보장 — 합계만 걸려야 함
    valid = _valid_png()
    images = [valid + b"\x00" * (each - len(valid)) for _ in range(count)]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is False
    assert "MB" in result.error


# ══════════════════════════════════════════════════════════
# 장수 초과
# ══════════════════════════════════════════════════════════

def test_장수_초과_거부():
    images = [_valid_png() for _ in range(constants.MAX_IMAGE_COUNT + 1)]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is False
    assert result.error == constants.ERROR_TOO_MANY.format(
        count=len(images), limit=constants.MAX_IMAGE_COUNT
    )


def test_장수_경계값_통과():
    images = [_valid_png() for _ in range(constants.MAX_IMAGE_COUNT)]
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
    result = logic.extract_posting_text([_valid_png()], extract=_empty_extract)
    assert result.ok is False
    assert result.error == constants.ERROR_EXTRACT_FAILED
    assert result.failure_kind == "input"


def test_공백만_있는_추출결과도_실패():
    blank = lambda imgs: logic.ExtractResult(text="   \n  ")  # noqa: E731
    result = logic.extract_posting_text([_valid_png()], extract=blank)
    assert result.ok is False
    assert result.error == constants.ERROR_EXTRACT_FAILED


def test_AI_호출이_예외를_던지면_텍스트경로_안내로_실패():
    result = logic.extract_posting_text([_valid_png()], extract=_raising_extract)
    assert result.ok is False
    assert result.error == constants.ERROR_EXTRACT_FAILED
    assert "글자로 붙여넣어" in result.error
    assert result.failure_kind == "technical"


# ══════════════════════════════════════════════════════════
# 원본 이미지가 안 남는지 (S2 — 이미지 원본 잔존 0건)
# ══════════════════════════════════════════════════════════

def test_원본_바이트가_디스크에_남지_않는다(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    images = [_valid_png(b"\xAB" * 500), _valid_jpeg(b"\xCD" * 500)]
    result = logic.extract_posting_text(images, extract=_ok_extract())
    assert result.ok is True
    assert list(tmp_path.iterdir()) == []  # 아무 파일도 안 생겼다


def test_원본_바이트가_모듈_전역에_남지_않는다():
    marker = _valid_png(b"\xEE" * 777)
    images = [marker]
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
    logic.extract_posting_text([_valid_png()], extract=_raising_extract)
    assert "900101" not in caplog.text
    assert "주민번호" not in caplog.text
    # ★ 예외 «종류 이름»만 남아야 한다.
    assert "RuntimeError" in caplog.text


def test_성공한_추출_텍스트는_로그에_안_남는다(caplog):
    caplog.set_level(logging.DEBUG)
    secret_looking_text = "이름 홍길동 연락처 010-1234-5678"
    logic.extract_posting_text([_valid_png()], extract=_ok_extract(secret_looking_text))
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

    images = [_valid_png() for _ in range(constants.MAX_IMAGE_COUNT + 1)]
    result = logic.extract_posting_text(images, extract=_spy_extract)
    assert result.ok is False
    assert called["count"] == 0


# ══════════════════════════════════════════════════════════
# 기본 구현(default_extract) — 실제로 부르지 않는다. 존재·시그니처만 확인.
# ══════════════════════════════════════════════════════════

def test_기본_추출기는_존재하고_호출가능하다():
    # ★ 절대 실행하지 않는다 — 부르면 anthropic을 불러오고 실제 API를 두드린다(비용).
    assert callable(logic.default_extract)
