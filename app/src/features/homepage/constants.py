"""홈페이지 수집 기능이 쓰는 값. 전부 여기서만 바꾼다 (매직 넘버 금지).

이 파일이 다루는 것 — 2·4-2·4-3 칸이 쓰는 회사 홈페이지 소스.
"""

from __future__ import annotations

from typing import Final

from src.shared.report_evidence.constants import (
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)

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
#: 2·4-2·4-3 칸은 홈페이지의
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
#:   실측((주)진영): 경영철학이 실린 `/company/overview.php`가
#:   후보 42개 중 18번째라 6쪽 안에 못 들어왔다. `MAX_PAGES`를 올리면 모든
#:   회사의 수집 시간이 늘어나므로, 예산은 그대로 두고 «순서»만 바꾼다.
#:
#: ⚠️ 여기 앞쪽에 넣는 말은 «회사 소개 경로에서만 쓰이는» 것으로 제한한다.
#:   흔한 일반 단어를 올리면 엉뚱한 페이지가 6쪽을 먼저 차지한다.
#:   실측 반례(삼성전자): 맨 앞에 `overview`만 넣었더니
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
#: 실제 회사가 등록해 쓸 수 있는 진짜 TLD만 담는다 - 아래
#: TEST_FIXTURE_ONLY_SINGLE_LABEL_SUFFIXES 와 절대 섞지 않는다(정정 2 —
#: 실제 커버리지 항목으로 오해되면 안 된다).
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

#: 오프라인 시험 픽스처 전용 TLD - 실제 등록 도메인 커버리지가 아니다.
#: example 은 RFC 2606이 등록을 영구히 금지한 예약 TLD라 실제 회사가
#: 등록해 쓸 수 없다(오탐 위험 0). P0-1 fail-closed 수정 후 fixture가
#: company.example 같은 주소로 실제 코드 경로(등록 도메인 판정, 하위
#: 도메인 자동결속)를 그대로 지나가게 하려고 별도 상수로 둔다(정정 2).
#: SINGLE_LABEL_PUBLIC_SUFFIXES 와 절대 합치지
#: 않고, 판정 시점에만(registrable_core_name) 두 집합을 함께 본다.
TEST_FIXTURE_ONLY_SINGLE_LABEL_SUFFIXES: Final[frozenset[str]] = frozenset({"example"})

# ── 넓은 공식 웹 수집 (Writer B, P-8da84a36) ────────────────
#
# 여러 공식 호스트(채용·IR·뉴스룸·정적 HTML)에 흩어진 공식 문서를 결속 근거와
# 함께 모으는 별도 수집기(wide_collect.py 등)의 상한. 아래 숫자는 실사용
# 트래픽으로 검증한 값이 아니라 «관측용 상한»이며, 이 값에 걸렸다고 해서
# 정상 회사를 거절하는 근거로 쓰면 안 된다 — 상한 도달은 TRUNCATED로 남긴다.

#: 도메인군 전체(root+apex/www 짝+같은 등록 도메인 하위호스트)에서 시도하는
#: 최대 일반 웹 페이지 수(robots.txt·sitemap.xml 조회는 포함하지 않는다).
WIDE_MAX_PAGES: Final[int] = 12

#: 도메인군 전체에서 내려받기를 시도하는 공식 IR PDF 최대 수.
WIDE_MAX_IR_DOCUMENTS: Final[int] = 3

#: robots·sitemap·모든 페이지·모든 IR PDF를 합친 전체 수집 상한(초).
WIDE_COLLECTION_TIMEOUT_SEC: Final[int] = 45

#: 일반 웹 페이지 전체(여러 호스트 합계)에서 내려받는 최대 바이트.
#: 페이지 수 상한보다 이 예산이 우선한다 — 큰 페이지 몇 개가 12쪽 예산을
#: 다 쓰기 전에 시간·바이트로 먼저 멈출 수 있게 한다.
WIDE_MAX_TOTAL_BYTES: Final[int] = 6 * 1024 * 1024

#: 결속 근거가 있어도 무한히 늘지 않도록 두는 호스트 수 상한
#: (root 1개 + apex/www 짝 + 같은 등록 도메인 하위호스트 합계).
WIDE_MAX_HOSTS: Final[int] = 8

#: sitemap.xml 하나에서 읽는 최대 바이트.
WIDE_MAX_SITEMAP_BYTES: Final[int] = 2 * 1024 * 1024

#: sitemap.xml에서 뽑아 후보 큐에 넣는 최대 URL 항목 수.
WIDE_MAX_SITEMAP_ENTRIES: Final[int] = 200

#: 문서 하나에서 보존하는 usable_ranges(본문 구간) 최대 개수.
WIDE_MAX_USABLE_RANGES_PER_DOCUMENT: Final[int] = 40

#: usable_ranges 구간 하나의 최대 글자 수.
WIDE_MAX_CHARS_PER_RANGE: Final[int] = 1_500

#: 구간으로 남기기엔 너무 짧은 글자 수(메뉴 부스러기 제외).
WIDE_MIN_CHARS_PER_RANGE: Final[int] = 40

#: 문서 identity의 source_kind 값.
WIDE_SOURCE_KIND_WEB_PAGE: Final[str] = SOURCE_KIND_OFFICIAL_WEB_PAGE
WIDE_SOURCE_KIND_IR_PDF: Final[str] = "official_ir_pdf"
WIDE_SOURCE_KIND_RECRUIT_PAGE: Final[str] = SOURCE_KIND_OFFICIAL_RECRUIT_PAGE

#: 문서·attempt의 requirement 값.
WIDE_REQUIREMENT_REQUIRED: Final[str] = "REQUIRED"
WIDE_REQUIREMENT_OPTIONAL: Final[str] = "OPTIONAL"

#: attempt의 state 값. 「없다」와 「못 가져옴」은 반드시 구분한다.
WIDE_ATTEMPT_OK: Final[str] = "OK"
WIDE_ATTEMPT_MISSING: Final[str] = "MISSING"
WIDE_ATTEMPT_FAILED: Final[str] = "FAILED"
WIDE_ATTEMPT_TRUNCATED: Final[str] = "TRUNCATED"

#: 원문 위치·수집기 계약에 함께 봉인할 버전 문자열.
WIDE_COLLECTOR_VERSION: Final[str] = "homepage-wide-collector/2"
WIDE_PARSER_VERSION: Final[str] = "homepage-wide-parser/2"

#: 채용·IR·뉴스룸·블로그 호스트·경로를 먼저 살펴보게 하는 우선순위 키워드.
WIDE_PRIORITY_HOST_KEYWORDS: Final[tuple[str, ...]] = (
    "recruit",
    "career",
    "careers",
    "jobs",
    "채용",
    "ir",
    "investor",
    "news",
    "press",
    "newsroom",
    "blog",
)

#: 공식 페이지에 링크돼 있어도 «회사의 다른 공식 채널»로 보지 않는 흔한
#: 소셜/광고/분석 호스트 접미사. 후보 결속 대상에서 제외한다(품질 필터,
#: SSRF 방어와는 무관 — 그 방어는 항상 safe_http가 담당한다).
WIDE_EXCLUDED_LINKED_HOST_SUFFIXES: Final[tuple[str, ...]] = (
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "kakao.com",
    "pf.kakao.com",
    "band.us",
    "google.com",
    "googletagmanager.com",
    "google-analytics.com",
    "doubleclick.net",
    "naver.com",
    "channel.io",
)

#: 넓은 공식 웹 수집기가 attempt.slot_ids·조각 태그에 쓰는 «수집기 필수 슬롯»
#: 목록의 «사본»이다(장별).
#: ★ 정본은 `app/src/shared/report_evidence/policy.py`다. 이 사본은 그 파일의
#:   `REQUIRED_EVIDENCE_SLOTS_BY_SECTION`에서 `INJECTED_EVIDENCE_SLOTS_BY_SECTION`
#:   을 뺀 값과 정확히 같다(실측). 정본이 바뀌면 이 사본도 다시 대조해야 한다.
#: ★ `composer/constants.py`의 `CLAIM_SLOTS_BY_SECTION`과는 다른 목록이다.
#:   `competitive_position:self_context`는 composer 목록에 없는 새 슬롯으로,
#:   자사 강점·시장 내 위치를 회사 스스로 서술한 페이지 전용이다 — 비교
#:   슬롯 5개는 이 수집기가 만들지 않는다(구조화 검증기가 다른 소스에서 별도 주입).
WIDE_REQUIRED_SLOT_IDS_BY_SECTION: Final[dict[str, tuple[str, ...]]] = {
    "identity": ("identity:corporate_identity", "identity:business_definition"),
    "business_model": (
        "business_model:revenue_model",
        "business_model:customer_type",
        "business_model:value_exchange",
    ),
    "portfolio": ("portfolio:product_role", "portfolio:revenue_link"),
    "past_changes": ("past_changes:completed_execution",),
    "current_challenges": ("current_challenges:issue", "current_challenges:response"),
    "future_strategy": ("future_strategy:stated_plan", "future_strategy:plan_status"),
    "operations_partners": (
        "operations_partners:value_chain",
        "operations_partners:operating_role",
    ),
    "culture": ("culture:work_principle", "culture:verified_case"),
    #: composer 목록에 없는 새 슬롯 — 자사 서술 원문 전용, 하나뿐이다.
    "competitive_position": ("competitive_position:self_context",),
}

#: 위 dict을 평탄화한 전체 슬롯 목록(중복 없음) — attempt.slot_ids 참고·검증용.
WIDE_REQUIRED_SLOT_IDS: Final[tuple[str, ...]] = tuple(
    slot
    for slots in WIDE_REQUIRED_SLOT_IDS_BY_SECTION.values()
    for slot in slots
)

#: URL 안의 페이지 유형 키워드 → 후보 슬롯 집합. `wide_domain.slot_ids_for_url`과
#: `wide_fragments.py`가 함께 재사용한다(같은 표 하나만 둔다 — 중복 금지).
#: 첫 번째로 맞는 키워드 묶음을 쓰므로 튜플 순서가 우선순위다. 채용→culture,
#: 제품→portfolio·business_model, 뉴스룸/IR/비전·전략→future_strategy·
#: past_changes, 회사소개→identity·competitive_position:self_context.
WIDE_SLOT_KEYWORD_MAP: Final[tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]] = (
    (
        ("recruit", "career", "careers", "jobs", "채용", "인재", "culture"),
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["culture"],
    ),
    (
        ("product", "products", "service", "tech", "portfolio"),
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["portfolio"]
        + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["business_model"],
    ),
    (
        ("news", "press", "newsroom", "blog", "ir", "investor", "vision", "strategy", "future"),
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["future_strategy"]
        + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["past_changes"],
    ),
    (
        ("about", "company", "overview"),
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["identity"]
        + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["competitive_position"],
    ),
    (
        ("business",),
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["business_model"],
    ),
    (
        ("partner", "partnership"),
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["operations_partners"],
    ),
)

#: 조각(fragment) 본문에서 슬롯 근거를 확인하는 직접 신호 키워드.
#: URL 경로는 검사 범위를 좁힐 뿐, 이 본문 신호가 없으면 조각을 만들지
#: 않는다(`wide_fragments.py`). 그래서 감사보고서의 숫자 표나 빈 회사소개
#: 페이지가 URL 이름만으로 사업모델·문화 슬롯을 채울 수 없다.
WIDE_SLOT_BODY_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "identity:corporate_identity": ("설립", "연혁", "사명", "비전", "미션"),
    "identity:business_definition": (
        "사업영역", "주요사업", "주요 사업", "사업을 영위", "전문기업",
        "제조·판매", "제조 및 판매",
    ),
    "business_model:revenue_model": (
        "매출 구조", "수익 모델", "수익모델", "판매에서 발생", "판매로 매출",
        "구독료", "이용료", "수수료",
    ),
    "business_model:customer_type": (
        "고객사", "주요 고객", "기업 고객", "개인 고객", "b2b", "b2c",
        "수요처", "이용자에게",
    ),
    "business_model:value_exchange": (
        "가치를 제공", "서비스를 제공", "솔루션을 제공", "혜택을 제공",
        "문제를 해결", "구독 서비스",
    ),
    "portfolio:product_role": (
        "주력 제품", "핵심 제품", "대표 제품", "제품군", "서비스 라인업", "솔루션",
    ),
    "portfolio:revenue_link": ("매출 비중", "주력", "핵심 제품", "라인업"),
    "past_changes:completed_execution": ("완료", "달성", "출시했", "런칭했", "성과"),
    # current_challenges의 issue/response는 낱말표로 판정하지 않는다.
    # challenge_evidence.py가 부정 영향-회사 행동-연결어 관계를 함께 본다.
    "future_strategy:stated_plan": ("계획", "전략", "로드맵", "예정"),
    "future_strategy:plan_status": ("진행중", "진행 중", "추진", "착수", "실행 단계"),
    "operations_partners:value_chain": ("공급망", "밸류체인", "협력사", "원자재"),
    "operations_partners:operating_role": ("직접 운영", "공동 운영", "제조", "생산"),
    "culture:work_principle": ("핵심가치", "인재상", "일하는 방식", "원칙"),
    "culture:verified_case": ("사례", "후기", "인터뷰", "스토리"),
    "competitive_position:self_context": ("강점", "차별화", "경쟁력", "1위", "선도"),
}
