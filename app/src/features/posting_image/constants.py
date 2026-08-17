"""공고 이미지 입력 기능이 쓰는 값. 매직 넘버 금지 — 전부 여기서만 바꾼다.

정본:
    확정/01_식별/1_흐름/03_공고판별.md §글자 추출 엔진 — 3단 조합
    확정/00_공통/2_규칙/01_도구정의.md §4 채용공고 입력 — 텍스트와 이미지 둘 다 1차부터 연다
    확정/90_운영기록/03_결정기록_01_기본설계.md D2 (이미지를 1차부터 여는 근거)

★ 「잠정」 표기 — 기획서에 숫자로 박혀 있지 않은 크기·장수 상한은 결정기록 D3
  방식(값이 없으면 시작 못 하니 잠정값 + 근거 한 줄 + 무엇이 풀리면 바뀌는지)을 따른다.
  첫 20건을 실제로 받아본 뒤 재검토 대상이다. 매직 바이트 값은 파일 형식 자체의
  고정된 사실이라 「잠정」이 아니다.

⚠️ 동기화 주의 — 화면 쪽 스크립트(`web/templates/_posting_image_field.html`)에도
  같은 형식·크기·장수 값이 JS 상수로 «따로» 있다. 여기를 고치면 그쪽도 같이 고쳐야
  한다 (자동 동기화 안 됨 — 최종 보고서의 「못 한 것」 참고).
"""

from __future__ import annotations

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

#: 이미지 1장 최대 용량.
#: 근거 — `prototype_v1/data/ocr_samples`의 실측 캡처(1920×1080대 PNG)는 이보다
#: 훨씬 작았다. 4K·멀티모니터 캡처까지 감안해 여유 있게 잡은 잠정 상한이다.
#: 풀리는 조건 — 실제 사용자 캡처 20건의 용량 분포를 재보면 조정한다.
MAX_IMAGE_BYTES: Final[int] = 8 * 1024 * 1024  # 8MB

#: 공고 하나를 나눠 찍은 캡처의 최대 장수.
#: 근거 — `prototype_v1/data/ocr_samples/manifest.jsonl` 실측 8클러스터 중
#: 최대 9장(원티드-01)이었다. 거기에 여유를 둔 잠정 상한이다.
MAX_IMAGE_COUNT: Final[int] = 10

#: 장수를 다 채워도 전체 요청 용량이 지나치게 커지지 않도록 거는 합계 상한.
MAX_TOTAL_BYTES: Final[int] = 40 * 1024 * 1024  # 40MB

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
#: 글자 추출 실패(형식 검사는 통과했지만 AI가 못 읽었거나 호출이 실패한 경우).
#: ★ 막다른 길을 만들지 않는다 — 텍스트로 붙여넣는 경로가 항상 남아 있다고 안내한다.
ERROR_EXTRACT_FAILED: Final[str] = "사진에서 글자를 못 읽었습니다. 글자로 붙여넣어 주세요."

# ── 환경변수 ──────────────────────────────────────────────
# ★ 실제 값(API 키)은 절대 코드에 넣지 않는다. os.environ에서만 읽는다.
#   (default_extract가 지연 참조한다 — logic.py 참고)
ENV_ANTHROPIC_API_KEY: Final[str] = "ANTHROPIC_API_KEY"

#: 기본 추출기가 쓰는 멀티모달 모델. 확정/01_식별 실측 비교에서 회사명 인식이
#: 가장 안정적이었던 모델(착수2 본측정 opus5)과 별개로, 비용 방어를 위해
#: 우선 haiku급을 기본값으로 둔다. 실제 배선은 `pipeline/real.py`가 정한다.
DEFAULT_EXTRACT_MODEL: Final[str] = "claude-haiku-4-5"

#: 기본 추출기 호출의 응답 토큰 상한. 공고 1건 전문을 담기에 충분한 여유치.
DEFAULT_EXTRACT_MAX_TOKENS: Final[int] = 8000
