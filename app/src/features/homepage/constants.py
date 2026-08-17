"""홈페이지 수집 기능이 쓰는 값. 전부 여기서만 바꾼다 (매직 넘버 금지).

정본: 확정/03_수집/2_규칙/01_소스정책.md §2 회사 홈페이지
      확정/05_생성/2_규칙/02_유형별소스.md (2·4-2·4-3 칸의 홈페이지 소스)
"""

from __future__ import annotations

from typing import Final

# ── 접속 ─────────────────────────────────────────────────

#: 접속 시간 제한(초). 회사 홈페이지는 트래픽이 적어 느릴 수 있어
#: 뉴스 API(`naver_client.py` 15초)보다 조금 더 여유를 둔다.
TIMEOUT_SEC: Final[int] = 10

#: 접속할 때 보내는 User-Agent. 봇임을 숨기지 않는다 — robots.txt가
#: 우리를 식별할 수 있어야 규칙이 의미가 있다.
USER_AGENT: Final[str] = "GiupBunseokBot/1.0 (+jobseeker-report-tool)"

# ── 상한 (무한 크롤링 금지) ──────────────────────────────

#: 홈페이지 한 곳에서 시도하는 최대 페이지 수 (루트 페이지 포함).
#: robots.txt 조회는 여기 안 들어간다.
MAX_PAGES: Final[int] = 6

#: 페이지 1개에서 뽑는 최대 글자 수. 회사소개 문단 몇 개면 충분하고,
#: 넘기면 나중에 AI에게 넣을 때 비용만 커진다.
MAX_CHARS_PER_PAGE: Final[int] = 3_000

#: 홈페이지 전체(여러 페이지 합계)에서 뽑는 최대 글자 수.
MAX_TOTAL_CHARS: Final[int] = 12_000

#: 이보다 짧게 뽑히면 「빈 페이지」로 보고 조각을 만들지 않는다.
#: 메뉴 몇 글자만 있는 페이지가 조각으로 잡히는 것을 막는다.
MIN_FRAGMENT_CHARS: Final[int] = 80

# ── 조각 모양 ────────────────────────────────────────────

#: 조각의 「종류」 값. 1판 조각 모양(`{"종류","원문"}`)과 맞춘다.
#: 정본: `prototype_v1/tools/run_pilot.py`의 `make_fragments`/`collect_news`
FRAGMENT_KIND: Final[str] = "홈페이지"

# ── 우선순위 ─────────────────────────────────────────────

#: 회사·기술 소개 페이지 우선순위 — 링크 주소에 이 글자가 들어 있으면
#: 먼저 읽는다. 앞에 있는 것일수록 더 먼저다.
#: 정본: 확정/05_생성/2_규칙/02_유형별소스.md — 2·4-2·4-3 칸은 홈페이지의
#: 회사소개·R&D·제품 소개가 재료다.
#: ★ 「비전·IR·보도자료」를 앞에 둔다 (문제로그 P-106).
#:   실제 합격 자소서 3건이 전부 **「전략 선언·슬로건 변경」**을 근거로 썼다
#:   (「가전의 지배자에서 '스마트 라이프 솔루션'을 선포하며…」 — 2026-08-16 조사).
#:   그런 문장은 `vision`·`ir`·`press` 페이지에 있는데, 이 목록에 없어서
#:   **최대 6쪽 상한에 밀려 사실상 한 번도 안 읽혔다.**
PRIORITY_PATH_KEYWORDS: Final[tuple[str, ...]] = (
    "vision",
    "ir",
    "press",
    "news",
    "esg",
    "sustainability",
    "about",
    "company",
    "companyintro",
    "introduce",
    "greeting",
    "tech",
    "technology",
    "rnd",
    "r&d",
    "research",
    "product",
    "products",
    "business",
    "service",
)

#: 이 확장자로 끝나는 링크는 따라가지 않는다. 문서·이미지·미디어 파일은
#: 사람이 읽는 글자 텍스트가 아니다.
EXCLUDED_EXTENSIONS: Final[tuple[str, ...]] = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".hwp",
    ".zip",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".mp4",
    ".mp3",
)

# ── 인증서 이름 불일치 우회 (C안, 문제로그 P-46) ────────────
#
# 사용자 결정 C안: 인증서 검증은 절대 끄지 않는다. 대신 인증서에 적힌
# 「진짜 이름」이 원래 주소와 «같은 회사」로 보일 때만 그 이름으로
# 검증을 켠 채 다시 접속한다. 다르면(예: 호스팅 업체 기본 인증서) 포기한다.

#: 파이썬 ssl 모듈은 인증서 오류 종류를 구분하는 별도 코드를 안 주고
#: 오류 메시지 글자로만 구분된다 — 만료·자체서명도 이름 불일치와 같은
#: 예외 클래스(`ssl.SSLCertVerificationError`)로 온다. 그래서 메시지에
#: 이 글자가 있어야만 「이름 불일치」로 본다 (그 외 오류는 절대 따라가지 않음).
HOSTNAME_MISMATCH_MARKER: Final[str] = "hostname mismatch"

#: 주소에 포트가 없을 때 인증서를 들여다볼 접속에 쓰는 HTTPS 기본 포트.
HTTPS_DEFAULT_PORT: Final[int] = 443

#: 도메인 끝 «두 칸」을 통째로 공개 접미사로 보는 경우 (예: "co.kr").
#: ★ 완전한 Public Suffix List가 아니라 국내 기업 분석에 흔한 것만 담은
#: 축약판이다 — 알려진 한계. 목록에 없는 두-칸 접미사는 한 칸만 떼는
#: 보수적인 기본 동작으로 넘어간다 (아래 `_registrable_core_name` 참조).
MULTI_LABEL_PUBLIC_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "co.kr",
        "or.kr",
        "go.kr",
        "ac.kr",
        "ne.kr",
        "pe.kr",
        "re.kr",
        "co.jp",
        "co.uk",
        "com.cn",
    }
)

#: 도메인 끝 «한 칸」만 공개 접미사로 보는 경우 (일반 gTLD·국가 코드).
SINGLE_LABEL_PUBLIC_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "kr",
        "com",
        "net",
        "org",
        "co",
        "io",
        "biz",
        "info",
        "me",
        "tv",
        "asia",
        "shop",
    }
)
