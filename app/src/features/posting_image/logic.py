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
import math
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Callable, Optional

from PIL import Image, UnidentifiedImageError

from src.core.pricing import usage_cost_krw
from src.core.provider_gateway import attempt_context, gateway
from src.core.provider_gateway.anthropic_adapter import AnthropicAdapter
from src.features.budget import provider_budget
from src.features.posting_image import constants

logger = logging.getLogger(__name__)

_PIL_FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
_ALLOWED_PIL_FORMATS = tuple(_PIL_FORMAT_TO_MIME)


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
    #: provider 응답은 받았지만 계약과 달라 글자를 사용할 수 없는가.
    technical_failure: bool = False


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
    """확장자가 아니라 바이트 시그니처로 후보 이미지 형식을 좁힌다.

    Ctrl+V로 붙여넣은 캡처는 확장자가 없거나 브라우저가 임의로 붙인 이름이라
    확장자를 신뢰할 수 없다. 이 값은 후보 판별일 뿐이며 Pillow 검증과 함께 쓴다.
    """
    if data.startswith(constants.MAGIC_PNG):
        return "image/png"
    if data.startswith(constants.MAGIC_JPEG):
        return "image/jpeg"
    if data[:4] == constants.MAGIC_RIFF and data[8:12] == constants.MAGIC_WEBP:
        return "image/webp"
    return None


def _validate_decodable_image(
    data: bytes, sniffed_mime: str
) -> Optional[ValidationFailure]:
    """허용 디코더로 실제 구조와 해상도를 확인한다.

    매직 바이트는 쉽게 위조할 수 있으므로 Pillow가 파일 구조 전체를 검증해야 한다.
    원본은 메모리에서 읽기만 하며 저장하거나 재인코딩하지 않는다.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=_ALLOWED_PIL_FORMATS) as image:
                detected_mime = _PIL_FORMAT_TO_MIME.get(image.format or "")
                if detected_mime is None or detected_mime != sniffed_mime:
                    return ValidationFailure(
                        "형식_불일치", constants.ERROR_UNSUPPORTED_FORMAT
                    )

                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or width > constants.MAX_IMAGE_DIMENSION
                    or height > constants.MAX_IMAGE_DIMENSION
                    or width * height > constants.MAX_IMAGE_PIXELS
                ):
                    return ValidationFailure(
                        "해상도_초과", constants.ERROR_RESOLUTION_TOO_LARGE
                    )
                image.verify()

            # ``verify()``는 컨테이너 구조만 검사하고 픽셀을 읽지 않는다. 검증 뒤의
            # Image 객체는 사용할 수 없으므로 같은 메모리 바이트를 다시 열어 실제
            # 픽셀 디코딩까지 끝내야 절단 JPEG·손상 WebP를 provider 전에 막을 수 있다.
            with Image.open(BytesIO(data), formats=_ALLOWED_PIL_FORMATS) as image:
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        return ValidationFailure("이미지_손상", constants.ERROR_INVALID_IMAGE)
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
        sniffed_mime = _sniff_format(data)
        if sniffed_mime is None:
            return ValidationFailure("형식_미지원", constants.ERROR_UNSUPPORTED_FORMAT)
        decode_failure = _validate_decodable_image(data, sniffed_mime)
        if decode_failure is not None:
            return decode_failure
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

    if not isinstance(result, ExtractResult):
        logger.warning("공고 이미지 글자 추출 응답 형식 오류")
        return PostingImageResult(
            ok=False,
            error=constants.ERROR_EXTRACT_FAILED,
            billing_uncertain=True,
            failure_kind="technical",
        )
    try:
        if isinstance(result.cost_krw, bool):
            raise ValueError
        cost_krw = float(result.cost_krw)
        if not math.isfinite(cost_krw) or cost_krw < 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        logger.warning("공고 이미지 글자 추출 비용 형식 오류")
        return PostingImageResult(
            ok=False,
            error=constants.ERROR_EXTRACT_FAILED,
            billing_uncertain=True,
            failure_kind="technical",
        )
    model = result.model if isinstance(result.model, str) else ""
    billing_uncertain = result.billing_uncertain is True
    if not isinstance(result.text, str):
        logger.warning("공고 이미지 글자 추출 text 형식 오류")
        return PostingImageResult(
            ok=False,
            error=constants.ERROR_EXTRACT_FAILED,
            cost_krw=cost_krw,
            model=model,
            billing_uncertain=billing_uncertain,
            failure_kind="technical",
        )
    text = result.text.strip()
    if not text:
        return PostingImageResult(
            ok=False,
            error=constants.ERROR_EXTRACT_FAILED,
            cost_krw=cost_krw,
            model=model,
            billing_uncertain=billing_uncertain,
            # 정상 응답 안에서 글자를 얻지 못한 것은 흐린 이미지·빈 응답일 수 있다.
            # 과금 여부조차 불명확할 때만 기술 오류로 올린다.
            failure_kind=(
                "technical"
                if billing_uncertain or result.technical_failure is True
                else "input"
            ),
        )
    return PostingImageResult(
        ok=True,
        text=text,
        cost_krw=cost_krw,
        model=model,
        billing_uncertain=billing_uncertain,
    )


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

    dimensions: list[tuple[int, int]] = []
    for data in images:
        try:
            with Image.open(BytesIO(data), formats=_ALLOWED_PIL_FORMATS) as image:
                width, height = image.size
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise provider_budget.ProviderBudgetUnavailable(
                "provider 호출 전 이미지 token 수를 추정할 수 없습니다"
            ) from exc
        if (
            width <= 0
            or height <= 0
            or width > constants.MAX_IMAGE_DIMENSION
            or height > constants.MAX_IMAGE_DIMENSION
            or width * height > constants.MAX_IMAGE_PIXELS
        ):
            raise provider_budget.ProviderBudgetUnavailable(
                "provider 호출 전 이미지가 서버 해상도 상한을 넘었습니다"
            )
        dimensions.append((width, height))

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

    call_reservation = provider_budget.current().reserve_call(
        model=constants.DEFAULT_EXTRACT_MODEL,
        input_tokens_upper=(
            provider_budget.estimate_image_tokens(dimensions)
            + provider_budget.estimate_request_tokens(
                {"prompt": prompt, "output_config": schema}
            )
        ),
        max_tokens=constants.DEFAULT_EXTRACT_MAX_TOKENS,
    )

    def usage_details(value: object) -> tuple[str, int, int, float] | None:
        usage = getattr(value, "usage", None)
        if usage is None:
            usage = getattr(getattr(value, "response", None), "usage", None)
        try:
            tokens_in = int(getattr(usage, "input_tokens"))
            tokens_out = int(getattr(usage, "output_tokens"))
            if tokens_in < 0 or tokens_out < 0:
                raise ValueError
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        model = str(
            getattr(value, "model", "") or constants.DEFAULT_EXTRACT_MODEL
        )
        return (
            model,
            tokens_in,
            tokens_out,
            usage_cost_krw(model, tokens_in, tokens_out),
        )

    try:
        callbacks = attempt_context.current()
    except Exception as error:
        provider_budget.current().cancel_before_dispatch(call_reservation)
        raise provider_budget.ProviderBudgetUnavailable(
            "OCR provider 시도 원장이 없어 호출하지 않았습니다"
        ) from error
    try:
        client = anthropic.Anthropic(
            max_retries=0,
            timeout=constants.ANTHROPIC_TIMEOUT_SEC,
        )
    except Exception:
        provider_budget.current().cancel_before_dispatch(call_reservation)
        raise
    try:
        attempt_token = callbacks.begin_attempt(
            "anthropic",
            "ocr",
            call_reservation.estimated_krw,
        )
    except Exception as error:
        provider_budget.current().cancel_before_dispatch(call_reservation)
        raise provider_budget.ProviderBudgetUnavailable(
            "OCR provider 시도 원장을 시작할 수 없어 호출하지 않았습니다"
        ) from error

    adapter = AnthropicAdapter(
        lambda value: (
            None
            if (details := usage_details(value)) is None
            else details[3]
        ),
        failure_cost_resolver=lambda value: (
            None
            if (details := usage_details(value)) is None
            else details[3]
        ),
    )

    def before_dispatch() -> None:
        callbacks.heartbeat(attempt_token)
        callbacks.mark_dispatch_intent(attempt_token)

    try:
        response = gateway.call_once(
            adapter=adapter,
            reserved_krw=call_reservation.estimated_krw,
            before_dispatch=before_dispatch,
            send=lambda: client.messages.create(
                model=constants.DEFAULT_EXTRACT_MODEL,
                max_tokens=constants.DEFAULT_EXTRACT_MAX_TOKENS,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "format": {"type": "json_schema", "schema": schema}
                },
            ),
            record_observation=lambda observation: callbacks.record_observation(
                attempt_token, observation
            ),
        )
    except gateway.ProviderDispatchNotStarted as error:
        provider_budget.current().cancel_before_dispatch(call_reservation)
        raise provider_budget.ProviderBudgetUnavailable(
            "OCR provider 전송 의도를 기록하지 못해 호출하지 않았습니다"
        ) from error
    except gateway.ProviderObservationRecordFailed as error:
        # 전송은 이미 일어났으므로 예약을 반환하지 않는다. DB lease 만료가
        # 보수부채로 회수하고 상위 OCR phase도 미확정으로 끝낸다.
        provider_budget.current().mark_unknown(call_reservation)
        raise provider_budget.ProviderBudgetUnavailable(
            "OCR provider 결과를 비용 원장에 기록하지 못했습니다"
        ) from error
    except gateway.ProviderCallFailed as wrapped:
        error = wrapped.__cause__
        if not isinstance(error, Exception):
            provider_budget.current().mark_unknown(call_reservation)
            raise provider_budget.ProviderBudgetUnavailable(
                "OCR provider 실패 원인을 확인할 수 없습니다"
            ) from wrapped
        details = usage_details(error)
        if details is None:
            # SDK 예외에는 대개 usage가 없다. adapter가 같은 예약액을 영속
            # 보수부채로 기록했으므로 로컬 예약도 0원으로 지우지 않는다.
            provider_budget.current().mark_unknown(call_reservation)
            raise error
        failure_model, _failure_in, _failure_out, failure_cost = details
        provider_budget.current().settle_call(
            call_reservation, actual_krw=failure_cost
        )
        logger.warning(
            "OCR provider가 usage를 포함한 실패를 돌려줬습니다 type=%s",
            type(error).__name__,
        )
        return ExtractResult(
            text="",
            cost_krw=failure_cost,
            model=failure_model,
            technical_failure=True,
        )
    details = usage_details(response)
    if details is None:
        # 응답이 왔어도 usage가 없으면 실제 금액을 0원으로 확정할 수 없다.
        # 영속 attempt도 AnthropicAdapter가 같은 보수부채로 이미 닫았다.
        provider_budget.current().mark_unknown(call_reservation)
        model = str(
            getattr(response, "model", "") or constants.DEFAULT_EXTRACT_MODEL
        )
        return ExtractResult(text="", model=model, billing_uncertain=True)
    model, _clean_in, _clean_out, cost_krw = details
    try:
        provider_budget.current().settle_call(
            call_reservation,
            actual_krw=cost_krw,
        )
    except provider_budget.ProviderCostInvariantError:
        # 실제 usage 금액은 반환값에 보존돼 상위 원장이 숨기지 않는다.
        logger.critical("OCR provider 비용 예약의 정산 상태가 손상됐습니다")
        raise
    if getattr(response, "stop_reason", "") == "refusal":
        return ExtractResult(text="", cost_krw=cost_krw, model=model)
    content_blocks = getattr(response, "content", None)
    if not isinstance(content_blocks, (list, tuple)):
        return ExtractResult(
            text="", cost_krw=cost_krw, model=model, technical_failure=True
        )
    text_block = next(
        (block for block in content_blocks if getattr(block, "type", "") == "text"),
        None,
    )
    if text_block is None:
        return ExtractResult(
            text="", cost_krw=cost_krw, model=model, technical_failure=True
        )
    raw_text = getattr(text_block, "text", None)
    if not isinstance(raw_text, str):
        return ExtractResult(
            text="", cost_krw=cost_krw, model=model, technical_failure=True
        )
    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        # ★ 응답 파싱이 실패해도 호출 비용은 이미 발생했다. 빈 결과와 함께 돌려줘야
        # 원장이 그 돈을 빠뜨리지 않는다.
        return ExtractResult(
            text="", cost_krw=cost_krw, model=model, technical_failure=True
        )
    if not isinstance(parsed, dict):
        # JSON 자체는 맞아도 목록·문자열이면 약속한 응답 모양이 아니다. 이때도
        # 응답 usage는 이미 받았으므로 비용까지 버리면 안 된다.
        return ExtractResult(
            text="", cost_krw=cost_krw, model=model, technical_failure=True
        )
    full_text = parsed.get("full_text")
    if not isinstance(full_text, str):
        return ExtractResult(
            text="", cost_krw=cost_krw, model=model, technical_failure=True
        )
    posting_flag = parsed.get("is_job_posting")
    return ExtractResult(
        text=full_text,
        looks_like_posting=posting_flag if isinstance(posting_flag, bool) else None,
        cost_krw=cost_krw,
        model=model,
    )
