"""애플리케이션 로그를 «실제로 남게» 하는 최상위(root) 로거 설정.

★ 왜 이 파일이 필요한가 (실측)
  이 앱에는 최상위 로거 설정이 **어디에도 없었다.** `Dockerfile`이 uvicorn에
  넘기는 ``--log-level``은 uvicorn 자기 로거(``uvicorn``·``uvicorn.error``·
  ``uvicorn.access``)만 켜고 최상위 로거는 건드리지 않는다. 그래서 운영에서
  최상위 로거는 «핸들러 0개 · 레벨 WARNING»이었고, 코드 88곳의
  ``logger.info(...)``는 «출력이 안 되는» 정도가 아니라 **레코드조차 만들어지지
  않았다** — 실측으로 ``logging.getLogger("src.web.main").isEnabledFor(INFO)``가
  ``False``였다.

  대표적 피해는 ``composer/dedupe.py``의 「장별 문장 수(정리 전→후)」다.
  **유료 재조사 없이** 어느 장이 얼마나 깎였는지 보려고 넣은 로그인데 운영에서
  한 번도 찍히지 않았다. 시험이 ``caplog.at_level(INFO)``로 레벨을 «강제»해
  통과하고 있었으므로 이 구멍을 아무도 잡지 못했다.

★ 보안 — 필터를 «로거»가 아니라 «핸들러»에 건다
  비밀 링크 주소(``/k/<32자리>``)는 로그에 남으면 안 된다. 지금까지는
  ``sharelink/access_log.py``가 ``uvicorn.access`` **로거**에만 필터를 걸어
  막았다. 최상위 핸들러를 새로 다는 순간 애플리케이션 로그는 **그 필터를 타지
  않는다.** 그래서 이 함수는 필터를 인자로 받아 **핸들러**에 건다 — 핸들러
  필터는 그 핸들러로 들어오는 모든 레코드에 걸리므로 어느 로거에서 왔든 가려진다.

★ 이 모듈은 feature를 import하지 않는다
  필터가 무엇인지 모르고, 조립 지점(``src/web/main.py``)이 넣어 준다.
  feature 간 직접 import 금지 규칙(``rules/feature-atomic.md``)을 지키기 위해서다.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final, Iterable, TextIO

#: 로그 수준을 정하는 환경변수. ``Dockerfile``이 이미 ``LOG_LEVEL=info``로 넣고
#: uvicorn에도 같은 값을 넘긴다 — 새 이름을 만들지 않고 그것을 그대로 쓴다.
ENV_LOG_LEVEL: Final[str] = "LOG_LEVEL"

#: 환경변수가 없거나 알 수 없는 값일 때 쓰는 수준.
#: ``INFO``인 이유 — 이 설정의 목적 자체가 ``logger.info``를 살리는 것이다.
DEFAULT_LEVEL_NAME: Final[str] = "INFO"

#: 한 줄 형식. 시각·수준·로거 이름이 있어야 여러 장·여러 요청이 섞여도 읽힌다.
LOG_FORMAT: Final[str] = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"

#: 우리가 단 핸들러인지 알아보려고 붙이는 표식. 이름이 겹칠 일이 없게 길게 쓴다.
_OWNED_HANDLER_MARK: Final[str] = "_기업분석_root_stream_handler"

#: 최상위 수준을 내려도 **INFO를 주지 않을** 남의 라이브러리들.
#:
#: ★ 왜 필요한가 (실측으로 시험이 잡아냈다)
#:   최상위 수준만 INFO로 내렸더니 ``httpx``가 **요청 URL을 통째로** 찍기 시작했고,
#:   그 URL에 보고서 번호가 들어 있어 `test_admin_access.py`의
#:   「감사 기록에 비밀이 새면 안 된다」 시험이 **빨간불**이 됐다.
#:   우리가 켜려던 것은 «우리 코드»의 진단 로그이지 남의 라이브러리 통신 기록이 아니다.
#:
#: ★ 왜 «막을 것»을 적고 «켤 것»을 안 적나 — 반대로 하면(우리 로거 이름만 허용)
#:   새 모듈이 다른 이름을 쓰는 순간 **아무 소리 없이 로그가 다시 죽는다.**
#:   실제로 이 앱에도 ``src.*`` 밖에 있는 ``security.admin_audit``가 있다.
#:   이쪽 실패는 「소음이 는다」로 끝나고 눈에 보인다 — 그쪽이 훨씬 안전하다.
#:
#: ⚠️ 새 라이브러리를 requirements에 더할 때, 그것이 INFO로 URL·본문을 찍으면
#:   여기에 이름을 더한다. 개별 라이브러리를 자세히 보려면 그 로거만 따로 켜라.
NOISY_THIRD_PARTY_LOGGERS: Final[tuple[str, ...]] = (
    # 통신 — URL·헤더를 그대로 찍는다 (실측으로 확인된 유출 경로)
    "httpx",
    "httpcore",
    "urllib3",
    "requests",
    "anthropic",
    # S3 백업 — 버킷·키 경로를 찍는다
    "boto3",
    "botocore",
    "s3transfer",
    # 파일·문서 처리 — 페이지마다 수십 줄씩 쏟아진다
    "pdfminer",
    "PIL",
    "multipart",
    "filelock",
    # 개인정보 삭제·형태소 분석
    "presidio-analyzer",
    "spacy",
    "thinc",
    "weasel",
    # 기타
    "tldextract",
    "watchfiles",
    "asyncio",
)


def resolve_level(raw: object) -> int:
    """환경변수 문자열을 로깅 수준 숫자로 바꾼다.

    Args:
        raw: ``"info"``·``"DEBUG"``·``10`` 같은 값. ``None``이면 기본값을 쓴다.

    Returns:
        ``logging`` 모듈의 수준 숫자.

    ★ 모르는 값이면 예외를 던지지 않고 기본값으로 간다 — 오타 하나로 서버가
      못 뜨는 것이 로그가 조금 많은 것보다 훨씬 나쁘다.
    """
    if isinstance(raw, int):
        return raw
    text = str(raw or "").strip().upper()
    if not text:
        text = DEFAULT_LEVEL_NAME
    resolved = logging.getLevelName(text)
    if not isinstance(resolved, int):
        return logging.getLevelName(DEFAULT_LEVEL_NAME)
    return resolved


def _owned_handler(root: logging.Logger) -> logging.Handler | None:
    """이미 우리가 단 핸들러가 있으면 돌려준다."""
    for handler in root.handlers:
        if getattr(handler, _OWNED_HANDLER_MARK, False):
            return handler
    return None


def configure_logging(
    *,
    level: object = None,
    stream: TextIO | None = None,
    filters: Iterable[logging.Filter] = (),
) -> logging.Handler:
    """최상위 로거에 핸들러를 **한 번만** 달고 수준을 정한다.

    Args:
        level: 수준을 직접 지정할 때만 넘긴다. ``None``이면 ``LOG_LEVEL``을 읽는다.
        stream: 로그를 쓸 곳. ``None``이면 ``sys.stderr``.
            uvicorn 기본 핸들러와 같은 곳이라 운영 로그가 한 갈래로 모인다.
        filters: 핸들러에 걸 필터. **같은 종류는 한 번만** 걸린다.

    Returns:
        설치된(또는 이미 있던) 핸들러.

    ★ 멱등이다 — 여러 번 불러도 핸들러가 늘어나지 않는다. 이 함수는 앱 조립
      지점에서 불리는데, 시험은 같은 모듈을 여러 번 import하기 때문이다.
    ★ 남이 단 핸들러는 «건드리지 않는다». pytest도 최상위에 자기 핸들러를
      달아 두는데, 그것을 지우면 시험이 로그를 못 잡는다.
    """
    root = logging.getLogger()
    resolved = resolve_level(level if level is not None else os.environ.get(ENV_LOG_LEVEL))

    handler = _owned_handler(root)
    if handler is None:
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        setattr(handler, _OWNED_HANDLER_MARK, True)
        root.addHandler(handler)

    for candidate in filters:
        if not any(isinstance(existing, type(candidate)) for existing in handler.filters):
            handler.addFilter(candidate)

    handler.setLevel(resolved)
    # ★ 최상위 로거의 «수준»까지 내려야 한다. 핸들러만 만들면 레코드 자체가
    #   안 만들어져서(기본 WARNING) 아무 일도 일어나지 않는다 — 이것이 원래의 결함이다.
    root.setLevel(resolved)
    quiet_third_party(resolved)
    return handler


def quiet_third_party(level: int) -> None:
    """남의 라이브러리는 최소 WARNING 위로 올려 둔다.

    Args:
        level: 지금 정한 수준. 이보다 «조용하게» 만들지는 않는다.

    ★ ``max``를 쓰는 이유 — ``LOG_LEVEL=ERROR``로 더 조용히 하라고 했는데
      여기서 WARNING으로 «되살리면» 지시를 어기는 것이 된다.
    """
    floor = max(level, logging.WARNING)
    for name in NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(floor)
