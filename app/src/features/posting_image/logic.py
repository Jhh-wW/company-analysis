"""공고 이미지 → 글자 추출.

★ 여기서 하는 일은 「이미지 바이트 → 글자」 하나뿐이다. 이게 채용공고인지 판별하는
  1층 AI·2층 코드·3층 개인정보 지우개는 이 모듈의 몫이 «아니다» (다른 조각).
  AI 호출은 항상 «주입»받는다 — 이 모듈은 어떤 AI를 어떻게 부르는지 몰라도 된다.

정본:
    확정/01_식별/1_흐름/03_공고판별.md §글자 추출 엔진 — 3단 조합
    확정/00_공통/2_규칙/01_도구정의.md §4 (이미지 원본 미저장 · 공고 원문 미저장)
    확정/99_기준/2_규칙/01_안전과가드레일.md S1 · S2 (개인정보·원본 잔존 0건)

★★ 원본 미보관 (S2) — 이 모듈은 받은 이미지 바이트를 파일로도, 로그로도, 캐시로도
  «어디에도» 남기지 않는다. 함수가 반환하면 이 모듈 안에는 그 바이트를 가리키는
  참조가 하나도 남지 않는다 (지역 변수뿐이고, 모듈 전역에 담지 않는다).
  다만 «호출자»가 넘겨준 리스트를 계속 들고 있으면 그건 이 모듈이 막을 수 없다 —
  호출자도 처리 직후 참조를 지워야 서버 메모리에서 완전히 사라진다 (연결 지점 참고).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from src.core.constants import (
    AI_COST_KRW_PER_USD,
    MODEL_PRICES_USD_PER_MTOK,
    UNKNOWN_MODEL_PRICE_USD_PER_MTOK,
)
from src.features.posting_image import constants

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractResult:
    """주입받은 AI 함수가 돌려줘야 하는 모양.

    Attributes:
        text: 이미지에서 읽은 글자 전문. 요약·의역하지 않은 원문 그대로여야 한다.
        looks_like_posting: AI가 추출과 «함께» 판단한 「채용공고처럼 보이는가」
            (있으면 덤으로 받아둔다 — 03_공고판별.md의 「1층 판별이 같이 딸려온다」).
            ★ 최종 판정 기준이 아니다. 5.5 판별기(다른 조각)가 정본이다.
    """

    text: str
    looks_like_posting: Optional[bool] = None
    #: 이 추출 호출에 실제로 쓴 AI 비용. 가짜 추출기·코드 추출기는 0원이다.
    cost_krw: float = 0.0
    #: 비용을 낸 모델. 호출하지 않았으면 빈 문자열이다.
    model: str = ""
    #: API 예외로 응답을 못 받아 실제 과금 여부를 확정할 수 없는가.
    billing_uncertain: bool = False


#: 이미지 바이트 목록(한 공고를 나눠 찍은 여러 장)을 글자로 바꾸는 함수의 모양.
#: 시그니처만 지키면 무엇이든 주입할 수 있다 — 시험에서는 가짜를 넣는다.
ExtractFn = Callable[[list[bytes]], ExtractResult]


@dataclass(frozen=True)
class ValidationFailure:
    """형식·크기·장수 검사에서 걸렸을 때의 사유."""

    #: 관측·시험용 짧은 분류 키(개인정보 아님 — 로그에 남겨도 된다).
    reason_kind: str
    #: 사용자에게 그대로 보여줄 한국어 문구.
    message: str


@dataclass(frozen=True)
class PostingImageResult:
    """이 모듈의 최종 결과."""

    ok: bool
    text: str = ""
    #: 실패했을 때 사용자에게 보여줄 한국어 사유. 성공이면 빈 문자열.
    error: str = ""
    #: 실패했어도 응답을 받은 뒤 파싱에서 막혔다면 비용은 이미 들었다.
    cost_krw: float = 0.0
    model: str = ""
    #: True면 알려진 비용과 별개로 미확정 호출 표식을 남겨야 한다.
    billing_uncertain: bool = False
    #: 관측 종료값. 검증·화질·빈 응답은 ``input``, provider·서버 오류는 ``technical``.
    #: 성공이면 빈 문자열이다. 사용자 문구를 다시 해석해 분류하면 문구 변경 때 깨진다.
    failure_kind: str = ""


def _sniff_format(data: bytes) -> Optional[str]:
    """확장자가 아니라 «바이트 자체»로 이미지 형식을 본다.

    Ctrl+V로 붙여넣은 캡처는 확장자가 없거나 브라우저가 임의로 붙인 이름이라
    확장자를 신뢰할 수 없다 — 반드시 매직 바이트로만 판별한다.
    """
    if data.startswith(constants.MAGIC_PNG):
        return "image/png"
    if data.startswith(constants.MAGIC_JPEG):
        return "image/jpeg"
    if data[:4] == constants.MAGIC_RIFF and data[8:12] == constants.MAGIC_WEBP:
        return "image/webp"
    return None


def validate_images(images: list[bytes]) -> Optional[ValidationFailure]:
    """형식·크기·장수를 검사한다. AI를 부르기 «전»에 걸러 비용을 아낀다.

    Args:
        images: 검사할 이미지 원본 바이트 목록.

    Returns:
        문제가 있으면 사유, 없으면 None.
    """
    if not images:
        return ValidationFailure("이미지_없음", constants.ERROR_EMPTY)
    if len(images) > constants.MAX_IMAGE_COUNT:
        return ValidationFailure(
            "장수_초과",
            constants.ERROR_TOO_MANY.format(
                count=len(images), limit=constants.MAX_IMAGE_COUNT
            ),
        )

    total_bytes = 0
    for data in images:
        if not data:
            return ValidationFailure("빈_이미지", constants.ERROR_EMPTY_FILE)
        if len(data) > constants.MAX_IMAGE_BYTES:
            return ValidationFailure(
                "용량_초과",
                constants.ERROR_TOO_LARGE.format(
                    mb=len(data) / (1024 * 1024),
                    limit_mb=constants.MAX_IMAGE_BYTES // (1024 * 1024),
                ),
            )
        if _sniff_format(data) is None:
            return ValidationFailure("형식_미지원", constants.ERROR_UNSUPPORTED_FORMAT)
        total_bytes += len(data)

    if total_bytes > constants.MAX_TOTAL_BYTES:
        return ValidationFailure(
            "전체용량_초과",
            constants.ERROR_TOTAL_TOO_LARGE.format(
                mb=total_bytes / (1024 * 1024),
                limit_mb=constants.MAX_TOTAL_BYTES // (1024 * 1024),
            ),
        )
    return None


def extract_posting_text(
    images: list[bytes],
    extract: ExtractFn,
) -> PostingImageResult:
    """이미지 목록에서 글자를 뽑는다.

    ① 형식·크기·장수를 코드로 먼저 거른다 (AI 비용 0원으로 폐기).
    ② 통과한 것만 주입받은 `extract`를 부른다.
    ③ 결과가 비어 있거나 호출이 실패하면 «막다른 길을 만들지 않고» 텍스트
       경로로 돌아갈 수 있다는 한국어 안내를 돌려준다.

    Args:
        images: 캡처 이미지 원본 바이트 목록(한 공고를 나눠 찍은 여러 장).
            ★ 이 함수는 이 바이트를 저장하지 않는다. 반환 후에는 «호출자»가
              들고 있던 목록도 지워야 한다(S2 — 처리 직후 폐기는 호출자 책임).
        extract: 실제로 글자를 읽어오는 함수(주입). 시험에서는 가짜를 넣는다.

    Returns:
        성공하면 ok=True + text. 실패하면 ok=False + 사용자에게 보여줄 사유.
    """
    failure = validate_images(images)
    if failure is not None:
        return PostingImageResult(
            ok=False,
            error=failure.message,
            failure_kind="input",
        )

    try:
        result = extract(images)
    except Exception as exc:  # noqa: BLE001 — AI 호출 실패도 텍스트 경로로 되돌아갈 수 있는 실패로 다룬다
        # ★ 예외 «종류 이름»만 남긴다. str(exc)는 AI가 돌려준 원문 일부(개인정보 포함
        #   가능성)를 담고 있을 수 있어 절대 로그에 넣지 않는다 (S1).
        logger.warning("공고 이미지 글자 추출 실패: %s", type(exc).__name__)
        return PostingImageResult(
            ok=False,
            error=constants.ERROR_EXTRACT_FAILED,
            # ★ 검증은 extractor를 부르기 전에 끝났다. 여기서 난 예외는 provider가
            # 요청을 받았는지 알 수 없으므로 0원이라고 단정하지 않는다.
            billing_uncertain=True,
            failure_kind="technical",
        )

    text = (result.text or "").strip()
    if not text:
        return PostingImageResult(
            ok=False,
            error=constants.ERROR_EXTRACT_FAILED,
            cost_krw=max(0.0, result.cost_krw),
            model=result.model,
            billing_uncertain=result.billing_uncertain,
            # 정상 응답 안에서 글자를 얻지 못한 것은 흐린 이미지·빈 응답일 수 있다.
            # 과금 여부조차 불명확할 때만 기술 오류로 올린다.
            failure_kind="technical" if result.billing_uncertain else "input",
        )
    return PostingImageResult(
        ok=True,
        text=text,
        cost_krw=max(0.0, result.cost_krw),
        model=result.model,
        billing_uncertain=result.billing_uncertain,
    )


def _usage_cost_krw(model: str, tokens_in: int, tokens_out: int) -> float:
    """Anthropic 응답 사용량을 원화로 바꾼다.

    ★ 모르는 모델은 싸게 추정하지 않는다. 원장에 적게 적히면 예산 상한도 함께
      뚫리므로 `core.constants`의 보수적인 단가를 그대로 쓴다.
    """
    price_in, price_out = MODEL_PRICES_USD_PER_MTOK.get(
        model, UNKNOWN_MODEL_PRICE_USD_PER_MTOK
    )
    usd = (tokens_in * price_in + tokens_out * price_out) / 1_000_000
    return round(usd * AI_COST_KRW_PER_USD, 2)


def default_extract(images: list[bytes]) -> ExtractResult:
    """기본 구현 — 멀티모달 AI(Anthropic)를 직접 불러 글자를 읽는다.

    ★ 이 함수는 이 모듈의 어떤 시험에서도 부르지 않는다 (실제 AI 호출 = 비용).
      존재 이유는 「진짜로 붙일 때 이 모양을 쓰면 된다」는 기본값을 남겨두는 것뿐이다.
      실제 배선(언제·어디서 부를지)은 `pipeline/real.py`가 정한다.

    ★ `anthropic` 패키지를 함수 «안에서만» 불러온다 — 이 모듈을 그냥 가져오는 것만
      으로 무거운 의존 프로그램이 없어도(예: 데모 모드) 실패하지 않아야 한다.
      (`pipeline/real.py`의 `_engine()`과 같은 지연 불러오기 규칙)

    Args:
        images: 이미지 원본 바이트 목록.

    Returns:
        추출한 글자 전문 + (얻었다면) 채용공고처럼 보이는지.

    Raises:
        RuntimeError: ANTHROPIC_API_KEY 환경변수가 없을 때.
    """
    import base64  # noqa: PLC0415
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415

    import anthropic  # noqa: PLC0415

    if not os.environ.get(constants.ENV_ANTHROPIC_API_KEY, "").strip():
        raise RuntimeError(
            f"{constants.ENV_ANTHROPIC_API_KEY} 환경변수가 없습니다"
        )

    schema = {
        "type": "object",
        "properties": {
            "full_text": {"type": "string"},
            "is_job_posting": {"type": "boolean"},
        },
        "required": ["full_text", "is_job_posting"],
        "additionalProperties": False,
    }
    prompt = (
        "이 이미지들은 같은 페이지를 나눠 캡처한 것이다. "
        "① 보이는 모든 텍스트를 순서대로 full_text에 그대로 담아라(요약·의역·생략 금지). "
        "② 이것이 채용공고인지 is_job_posting으로 판정하라 — 애매하면 false."
    )
    content: list[dict[str, object]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _sniff_format(data) or "image/png",
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        }
        for data in images
    ]
    content.append({"type": "text", "text": prompt})

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=constants.DEFAULT_EXTRACT_MODEL,
        max_tokens=constants.DEFAULT_EXTRACT_MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    model = str(getattr(response, "model", "") or constants.DEFAULT_EXTRACT_MODEL)
    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "input_tokens", None) if usage is not None else None
    tokens_out = getattr(usage, "output_tokens", None) if usage is not None else None
    if tokens_in is None or tokens_out is None:
        # 응답이 왔어도 usage가 없으면 실제 금액을 0원으로 확정할 수 없다.
        return ExtractResult(text="", model=model, billing_uncertain=True)
    try:
        clean_in, clean_out = int(tokens_in), int(tokens_out)
    except (TypeError, ValueError, OverflowError):
        return ExtractResult(text="", model=model, billing_uncertain=True)
    if clean_in < 0 or clean_out < 0:
        return ExtractResult(text="", model=model, billing_uncertain=True)
    cost_krw = _usage_cost_krw(
        model,
        clean_in,
        clean_out,
    )
    if getattr(response, "stop_reason", "") == "refusal":
        return ExtractResult(text="", cost_krw=cost_krw, model=model)
    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        return ExtractResult(text="", cost_krw=cost_krw, model=model)
    try:
        parsed = json.loads(text_block.text)
    except (TypeError, ValueError):
        # ★ 응답 파싱이 실패해도 호출 비용은 이미 발생했다. 빈 결과와 함께 돌려줘야
        # 원장이 그 돈을 빠뜨리지 않는다.
        return ExtractResult(text="", cost_krw=cost_krw, model=model)
    if not isinstance(parsed, dict):
        # JSON 자체는 맞아도 목록·문자열이면 약속한 응답 모양이 아니다. 이때도
        # 응답 usage는 이미 받았으므로 비용까지 버리면 안 된다.
        return ExtractResult(text="", cost_krw=cost_krw, model=model)
    return ExtractResult(
        text=parsed.get("full_text", ""),
        looks_like_posting=parsed.get("is_job_posting"),
        cost_krw=cost_krw,
        model=model,
    )
