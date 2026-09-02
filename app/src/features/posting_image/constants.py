"""공고 이미지 입력 기능이 쓰는 값. 매직 넘버 금지 — 전부 여기서만 바꾼다.

이 기능이 따르는 규칙:
  - 글자 추출은 3단 조합 엔진
  - 입력은 텍스트와 이미지 둘 다 1차부터 연다

★ 「잠정」 표기 — 기획서에 숫자로 박혀 있지 않은 크기·장수 상한은 결정기록 D3
  방식(값이 없으면 시작 못 하니 잠정값 + 근거 한 줄 + 무엇이 풀리면 바뀌는지)을 따른다.
  첫 20건을 실제로 받아본 뒤 재검토 대상이다. 매직 바이트 값은 파일 형식 자체의
  고정된 사실이라 「잠정」이 아니다.

⚠️ 동기화 주의 — 화면 쪽 스크립트(`web/templates/_posting_image_field.html`)에도
  같은 형식·크기·장수 값이 JS 상수로 «따로» 있다. 여기를 고치면 그쪽도 같이 고쳐야
  한다 (자동 동기화 안 됨 — 최종 보고서의 「못 한 것」 참고).
"""

from __future__ import annotations

import os

from typing import Final

# ── 허용 형식 — 매직 바이트로 판별한다 ──────────────────
# ★ 확장자가 아니라 «바이트 자체»를 본다. Ctrl+V로 붙여넣은 캡처는 확장자가
#   없거나 브라우저가 임의로 붙인 이름이라 확장자를 신뢰할 수 없다.
MAGIC_PNG: Final[bytes] = b"\x89PNG\r\n\x1a\n"
MAGIC_JPEG: Final[bytes] = b"\xff\xd8\xff"
MAGIC_RIFF: Final[bytes] = b"RIFF"
MAGIC_WEBP: Final[bytes] = b"WEBP"

#: 사람이 읽는 이름표 + 화면 file input의 accept 속성과 맞춰야 하는 MIME 목록.
ALLOWED_MIME_TYPES: Final[tuple[str, ...]] = ("image/png", "image/jpeg", "image/webp")

# ── 크기·장수 상한 (잠정 — 첫 20건 뒤 재검토) ────────────

#: 운영 환경에서는 이 세 값을 더 낮추거나 0으로 꺼둘 수 있다. 코드 상한보다 큰
#: 값이나 잘못된 값은 시작 오류로 알려준다 — 조용히 다른 값으로 도는 것을 막는다.
ENV_MAX_IMAGE_BYTES: Final[str] = "POSTING_IMAGE_MAX_BYTES"
ENV_MAX_IMAGE_COUNT: Final[str] = "POSTING_IMAGE_MAX_COUNT"
ENV_MAX_TOTAL_BYTES: Final[str] = "POSTING_IMAGE_MAX_TOTAL_BYTES"

HARD_MAX_IMAGE_BYTES: Final[int] = 5 * 1024 * 1024
HARD_MAX_IMAGE_COUNT: Final[int] = 4
HARD_MAX_TOTAL_BYTES: Final[int] = 12 * 1024 * 1024


def _lowerable_limit(name: str, hard_max: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return hard_max
    try:
        requested = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 환경변수는 0 이상의 정수여야 합니다.") from exc
    if requested < 0:
        raise RuntimeError(f"{name} 환경변수는 0 이상의 정수여야 합니다.")
    if requested > hard_max:
        raise RuntimeError(
            f"{name} 환경변수는 안전 상한 {hard_max} 이하여야 합니다."
        )
    return requested


#: 이미지 1장 최대 용량.
#: 근거 — `analysis_engine/data/ocr_samples`의 실측 캡처(1920×1080대 PNG)는 이보다
#: 훨씬 작았다. 4K·멀티모니터 캡처까지 감안해 여유 있게 잡은 잠정 상한이다.
#: 풀리는 조건 — 실제 사용자 캡처 20건의 용량 분포를 재보면 조정한다.
MAX_IMAGE_BYTES: Final[int] = _lowerable_limit(
    ENV_MAX_IMAGE_BYTES, HARD_MAX_IMAGE_BYTES
)

#: 공고 하나를 나눠 찍은 캡처의 최대 장수.
#: 근거 — 작은 Render 인스턴스에서 OCR 모델과 함께 뜰 때 남는 메모리가 적어
#: 우선 4장으로 제한한다. 더 긴 공고는 글자 붙여넣기를 함께 안내한다.
MAX_IMAGE_COUNT: Final[int] = _lowerable_limit(
    ENV_MAX_IMAGE_COUNT, HARD_MAX_IMAGE_COUNT
)

#: 장수를 다 채워도 전체 요청 용량이 지나치게 커지지 않도록 거는 합계 상한.
MAX_TOTAL_BYTES: Final[int] = _lowerable_limit(
    ENV_MAX_TOTAL_BYTES, HARD_MAX_TOTAL_BYTES
)

#: 압축 파일 크기와 별개로 디코딩 때 펼쳐질 픽셀 수를 제한한다. 일반적인 4K
#: 캡처(약 830만 픽셀)는 허용하되 작은 압축 폭탄이 OCR 앞에서 메모리를 소진하지
#: 못하게 하는 서버 상한이다.
MAX_IMAGE_PIXELS: Final[int] = 20_000_000

#: 비정상적으로 길거나 넓은 이미지 한 변의 상한. 전체 픽셀 상한과 함께 적용한다.
MAX_IMAGE_DIMENSION: Final[int] = 10_000

# ── 오류 문구 (전부 한국어 — 사용자에게 그대로 보여준다) ──

ERROR_EMPTY: Final[str] = "이미지를 선택하거나 붙여넣어 주세요."
ERROR_EMPTY_FILE: Final[str] = "빈 이미지 파일입니다. 다시 선택해 주세요."
#: {count}·{limit}로 채운다.
ERROR_TOO_MANY: Final[str] = "이미지는 최대 {limit}장까지 올릴 수 있습니다 (지금 {count}장)."
#: {mb}·{limit_mb}로 채운다.
ERROR_TOO_LARGE: Final[str] = (
    "이미지 1장은 최대 {limit_mb}MB까지 올릴 수 있습니다 (지금 {mb:.1f}MB)."
)
#: {mb}·{limit_mb}로 채운다.
ERROR_TOTAL_TOO_LARGE: Final[str] = (
    "이미지 전체 용량이 너무 큽니다 (최대 {limit_mb}MB, 지금 {mb:.1f}MB). 장수를 줄여주세요."
)
ERROR_UNSUPPORTED_FORMAT: Final[str] = (
    "지원하지 않는 이미지 형식입니다. PNG · JPEG · WEBP 형식만 올릴 수 있습니다."
)
ERROR_INVALID_IMAGE: Final[str] = (
    "손상되었거나 올바르지 않은 이미지입니다. PNG · JPEG · WEBP 파일을 다시 선택해 주세요."
)
ERROR_RESOLUTION_TOO_LARGE: Final[str] = (
    "이미지 해상도가 너무 큽니다. 더 작은 이미지로 다시 올려주세요."
)
#: 글자 추출 실패(형식 검사는 통과했지만 AI가 못 읽었거나 호출이 실패한 경우).
#: ★ 막다른 길을 만들지 않는다 — 텍스트로 붙여넣는 경로가 항상 남아 있다고 안내한다.
ERROR_EXTRACT_FAILED: Final[str] = "사진에서 글자를 못 읽었습니다. 글자로 붙여넣어 주세요."

# ── 환경변수 ──────────────────────────────────────────────
# ★ 실제 값(API 키)은 절대 코드에 넣지 않는다. os.environ에서만 읽는다.
#   (default_extract가 지연 참조한다 — logic.py 참고)
ENV_ANTHROPIC_API_KEY: Final[str] = "ANTHROPIC_API_KEY"

#: 기본 추출기가 쓰는 멀티모달 모델. 실측 비교에서 회사명 인식이
#: 가장 안정적이었던 모델(착수2 본측정 opus5)과 별개로, 비용 방어를 위해
#: 우선 haiku급을 기본값으로 둔다. 실제 배선은 `pipeline/real.py`가 정한다.
DEFAULT_EXTRACT_MODEL: Final[str] = "claude-haiku-4-5"

#: 기본 추출기 호출의 응답 토큰 상한. 공고 1건 전문을 담기에 충분한 여유치.
DEFAULT_EXTRACT_MAX_TOKENS: Final[int] = 8000

#: OCR provider 단일 호출이 worker를 무한정 점유하지 못하게 하는 초 단위 상한.
ANTHROPIC_TIMEOUT_SEC: Final[float] = 180.0
