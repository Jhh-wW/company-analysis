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

#: robots·DNS·리다이렉트·모든 HTML 본문을 합친 홈페이지 수집 전체 상한.
HOMEPAGE_COLLECTION_TIMEOUT_SEC: Final[int] = 25

#: IR 탐색 HTML·PDF 다운로드·격리 파싱을 합친 공식 IR 수집 전체 상한.
IR_COLLECTION_TIMEOUT_SEC: Final[int] = 35

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

# ── 공식 IR PDF 상한 ─────────────────────────────────────

#: PDF 링크를 찾으려고 읽는 HTML 최대 쪽 수(루트 포함).
MAX_IR_DISCOVERY_PAGES: Final[int] = 5

#: 한 홈페이지에서 내려받기를 시도하는 공식 IR PDF 최대 수.
MAX_IR_DOCUMENTS: Final[int] = 2

#: 발견 큐가 비정상적으로 커지는 것을 막는 HTML·PDF 링크 합계 상한.
MAX_IR_DISCOVERY_LINKS: Final[int] = 60

#: 공식 IR PDF 한 파일의 최대 바이트 수(12 MiB).
MAX_IR_PDF_BYTES: Final[int] = 12 * 1024 * 1024

#: 홈페이지 하나에서 내려받을 수 있는 모든 공식 IR PDF의 합계(20 MiB).
MAX_IR_TOTAL_PDF_BYTES: Final[int] = 20 * 1024 * 1024

#: PDF 한 파일에서 허용하는 최대 페이지 수.
MAX_IR_PDF_PAGES: Final[int] = 80

#: 손상 PDF의 루트 객체 복구 탐색 상한.
MAX_IR_ROOT_RECOVERY_OBJECTS: Final[int] = 1_000

#: PDF 압축 스트림 하나가 파서 안에서 풀릴 수 있는 최대 바이트 수.
MAX_IR_DECOMPRESSED_STREAM_BYTES: Final[int] = 12 * 1024 * 1024

#: PDF 파싱·글자 추출 자식 프로세스의 전체 실행시간 상한(초).
IR_PDF_PARSE_TIMEOUT_SEC: Final[int] = 10

#: PDF 워커 하나가 매핑할 수 있는 전체 가상 주소 공간의 OS hard limit(256 MiB).
#: 12 MiB 입력·스트림 상한보다 충분한 파서 여유를 주되, 여러 압축 스트림이
#: 동시에 살아남아 Render 부모 서비스까지 OOM시키는 것은 프로세스 경계에서 막는다.
MAX_IR_WORKER_ADDRESS_SPACE_BYTES: Final[int] = 256 * 1024 * 1024

#: PDF 워커가 쓸 수 있는 CPU 시간의 OS 상한. 부모의 10초 wall timeout보다 먼저
#: 커널이 비정상 파서를 끝낼 수 있도록 짧게 둔다.
MAX_IR_WORKER_CPU_SECONDS: Final[int] = 8

#: 한 앱 프로세스가 동시에 띄울 PDF 파서 수. per-worker OS limit 합계가 부모
#: 서비스 메모리까지 잠식하지 않도록 단일 워커로 직렬화한다.
MAX_CONCURRENT_IR_PDF_WORKERS: Final[int] = 1

#: 이미 PDF 워커가 실행 중일 때 슬롯을 기다리는 최대 시간. 유료 분석 요청을
#: 무기한 붙잡지 않고 기술 실패로 닫는다.
IR_PDF_WORKER_SLOT_TIMEOUT_SEC: Final[int] = 1

#: 강제 종료한 PDF 워커가 실제로 끝났는지 기다리는 최대 시간. ``kill`` 실패나
#: 비정상 OS 상태가 부모 요청을 무기한 붙잡지 못하게 한다.
IR_PDF_WORKER_REAP_TIMEOUT_SEC: Final[int] = 1

#: 원문 위치와 자식 프로세스 계약에 함께 봉인할 PDF 추출기 버전.
IR_PDF_EXTRACTOR_VERSION: Final[str] = "pypdf 6.16.1"

#: 자식 프로세스가 부모에게 돌려줄 수 있는 페이지별 원시 글자 상한.
MAX_IR_RAW_CHARS_PER_PAGE: Final[int] = 12_000

#: 자식 PDF 파서가 돌려주는 JSON 표준출력 최대 바이트 수.
MAX_IR_WORKER_OUTPUT_BYTES: Final[int] = 4 * 1024 * 1024

#: 대상 법인명·별칭이 실제로 나타나는지 확인할 PDF 앞쪽 페이지 수.
IR_IDENTITY_CHECK_PAGES: Final[int] = 4

#: PDF 한 파일에서 근거 조각으로 보존하는 최대 글자 수.
MAX_IR_CHARS_PER_DOCUMENT: Final[int] = 12_000

#: 홈페이지 하나의 모든 IR PDF에서 AI 입력으로 보존하는 최대 글자 수.
MAX_IR_TOTAL_CHARS: Final[int] = 12_000

#: PDF 페이지 하나에서 AI 입력으로 보존하는 최대 글자 수.
MAX_IR_CHARS_PER_PAGE: Final[int] = 6_000

#: 페이지 안 문단 하나가 지나치게 길 때 보존하는 최대 글자 수.
MAX_IR_CHARS_PER_FRAGMENT: Final[int] = 3_000

#: 메뉴 조각처럼 너무 짧은 PDF 글자를 근거에서 빼는 최소 길이.
MIN_IR_FRAGMENT_CHARS: Final[int] = 20

#: 한 문서에서 보존할 수 있는 페이지·문단 조각 수 상한.
MAX_IR_FRAGMENTS_PER_DOCUMENT: Final[int] = 160

#: PDF 문서명 한 줄의 최대 글자 수.
MAX_IR_DOCUMENT_TITLE_CHARS: Final[int] = 200

# ── 조각 모양 ────────────────────────────────────────────

#: 조각의 「종류」 값. 1판 조각 모양(`{"종류","원문"}`)과 맞춘다.
#: 정본: `analysis_engine/tools/run_pilot.py`의 `make_fragments`/`collect_news`
FRAGMENT_KIND: Final[str] = "홈페이지"

# ── 우선순위 ─────────────────────────────────────────────

#: 회사·기술 소개 페이지 우선순위 — 링크 주소에 이 글자가 들어 있으면
#: 먼저 읽는다. 앞에 있는 것일수록 더 먼저다.
#: 정본: 확정/05_생성/2_규칙/02_유형별소스.md — 2·4-2·4-3 칸은 홈페이지의
#: 회사소개·R&D·제품 소개가 재료다.
#: 일반 홈페이지 HTML은 회사소개·사업과 P-106에서 필요성이 확인된
#: 비전·보도자료를 먼저 읽는다. IR 자료실과 PDF는 별도 공식 IR 수집기가
#: 담당하므로, IR·주가·실적 목록이 6쪽 예산을 먼저 소진해 핵심 페이지를
#: 놓치게 하면 안 된다.
#:
#: ★ `about` 바로 다음 묶음은 8장「인재상과 일하는 방식」의 재료다 —
#:   경영철학·경영이념·핵심가치·인재상·윤리기준. 이 페이지들은 이름에
#:   `about`·`business`가 없어 `company`(회사 공통 경로)로만 걸렸고, 그러면
#:   연혁·조직도·CI/BI와 같은 순위가 되어 `MAX_PAGES`(6쪽) 예산 밖으로 밀린다.
#:   실측((주)진영, 2026-08-25): 경영철학이 실린 `/company/overview.php`가
#:   후보 42개 중 18번째라 6쪽 안에 못 들어왔다. `MAX_PAGES`를 올리면 모든
#:   회사의 수집 시간이 늘어나므로, 예산은 그대로 두고 «순서»만 바꾼다.
#:
#: ⚠️ 여기 앞쪽에 넣는 말은 «회사 소개 경로에서만 쓰이는» 것으로 제한한다.
#:   흔한 일반 단어를 올리면 엉뚱한 페이지가 6쪽을 먼저 차지한다.
#:   실측 반례(삼성전자, 2026-08-25): 맨 앞에 `overview`만 넣었더니
#:   `/sustainability/accessibility/overview/`가 1등이 되어 접근성 하위
#:   페이지가 예산을 다 먹었고, 경영이념(인재제일·최고지향·변화선도·정도경영·
#:   상생추구)이 실린 `/about-us/brand-identity/brand-story/`를 **놓쳤다**.
#:   그래서 맨몸 `overview`는 앞에 두지 않고, 회사 경로와 붙은
#:   `company/overview` 꼴만 앞에 둔다. 맨몸 `overview`는 `company` 바로
#:   앞(연혁·조직도보다는 먼저, `about`·사업·비전보다는 나중)에 둔다.
PRIORITY_PATH_KEYWORDS: Final[tuple[str, ...]] = (
    "about",
    # ── 8장 재료: 경영철학·핵심가치·인재상·윤리 ──
    "philosophy",
    "경영철학",
    "경영이념",
    "핵심가치",
    "corevalue",
    "core-value",
    "core_value",
    "인재상",
    "talent",
    # 맨몸 `overview`가 아니라 «회사 경로에 붙은» 개요만 앞에 둔다 (위 반례).
    "company/overview",
    "company-overview",
    "company_overview",
    "ethics",
    "윤리",
    # ── 회사·사업 소개 ──
    "business",
    "vision",
    "press",
    "strategy",
    "news",
    "companyintro",
    "introduce",
    "greeting",
    # 맨몸 개요 — 연혁·조직도·CI/BI(`company`)보다는 먼저 읽는다.
    "overview",
    "company",
    "product",
    "products",
    "service",
    "tech",
    "technology",
    "rnd",
    "r&d",
    "research",
    "career",
    "careers",
    "recruit",
    "recruitment",
    "jobs",
    "people",
    "culture",
    "채용",
    "인재",
    "문화",
    "esg",
    "sustainability",
    "ir-data",
    "irdata",
    "earnings",
    "result",
    "실적",
    "financial",
    "finance",
    "stock",
    "shareholder",
    "ir",
)

#: 회사명이 일반적인 ``about`` 경로가 아닌 브랜드 경로에 들어간 사이트를 위한
#: 최소 길이. ``/ko``·``/en`` 같은 언어 경로는 길이 단계에서 먼저 제외된다.
BRAND_PATH_MIN_TOKEN_CHARS: Final[int] = 3

#: 등록 도메인 핵심 이름과 브랜드 경로가 prefix 관계일 때 허용하는 최대 차이.
#: 예: ``jype.com`` ↔ ``/JYP``는 1글자 차이라 허용한다. 짧은 우연 일치가
#: 전혀 다른 경로를 회사소개로 올리는 것은 막는다.
BRAND_PATH_MAX_PREFIX_GAP: Final[int] = 2

#: 등록 도메인과 경로에 우연히 같은 일반 단어가 있어도 브랜드 소개로 보지
#: 않는다. 언어코드는 최소 길이보다 짧지만 정책을 눈에 보이게 함께 적는다.
BRAND_PATH_EXCLUDED_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "ko",
        "en",
        "ja",
        "jp",
        "zh",
        "cn",
        "es",
        "fr",
        "de",
        "company",
        "corporate",
        "corporation",
        "business",
        "enterprise",
        "enterprises",
        "group",
        "global",
        "holdings",
        "official",
        "home",
        "main",
        "about",
        "ir",
        "esg",
        "news",
        "press",
    }
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
