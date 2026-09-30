"""선택적 개인 감사 관측은 수집 결과나 예산을 바꾸지 않는다."""
from typing import Any, Callable

NewsObserver = Callable[[str, dict[str, Any]], object]


def observe_news(observer: NewsObserver | None, event: str,
                 payload: dict[str, Any] | Callable[[], dict[str, Any]]) -> None:
    if observer is not None:
        try:
            observer(event, payload() if callable(payload) else payload)
        except Exception:
            pass  # 관측 보관 실패는 수집·검수·원장에 전파하지 않는다.
