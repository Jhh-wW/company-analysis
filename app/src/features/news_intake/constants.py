"""언론사 뉴스 도입의 닫힌 어휘와 상한."""

from __future__ import annotations

from typing import Final

from src.shared.report_evidence.constants import NEWS_EXCLUDED_SECTION_IDS


DEFAULT_WINDOW_DAYS: Final[int] = 365
EXTENDED_WINDOW_DAYS: Final[int] = 1_095
#: 공식 웹 문서가 이 수 이하이면 회사 사이트에서 읽어 온 문장이 보고서에
#: 하나도 없다는 뜻이다(본문을 자바스크립트로 만드는 사이트가 대표적이다).
#: 그런 회사는 모든 장이 READY라도 공식 웹 몫이 통째로 비므로 보조 문장을
#: 받을 장이 생긴다. 발동 규칙의 정본은 파이프라인이 아니라 이 기능 폴더다.
WEB_DOCUMENT_ZERO_THRESHOLD: Final[int] = 0
MAX_CANDIDATES: Final[int] = 20
MAX_SECTIONS_PER_CANDIDATE: Final[int] = 3
#: 기사 한 건이 만들 수 있는 조각 수 상한. 한 기사에서 조각이 여럿 나오면
#: 전체 상한(6개)을 그 기사 하나가 다 먹고 부록이 같은 글로 채워진다.
#: 근거의 폭을 지키려고 기사마다 따로 건다.
MAX_FRAGMENTS_PER_ARTICLE: Final[int] = 2
ATTRIBUTION_CONTEXT_CHARS: Final[int] = 20
TITLE_DUPLICATE_SIMILARITY: Final[float] = 0.72
TITLE_DUPLICATE_MIN_CHARS: Final[int] = 8

PRESS_RELEASE_KEYWORDS: Final[tuple[str, ...]] = (
    "밝혔다",
    "출시",
    "선정",
    "계약",
    "수주",
    "체결",
    "공개",
)
INTERVIEW_KEYWORDS: Final[tuple[str, ...]] = ("인터뷰", "[인터뷰]", "［인터뷰］")
#: 증권·투자 칼럼을 걸러 내는 낱말. 회사가 무엇을 했는지가 아니라 그 주식을
#: 어떻게 사고팔지를 쓴 글이라, 사업 사실의 근거가 되지 못한다. 한 항목이라도
#: 제목·요약에 있으면 기사 전체를 뺀다.
#:
#: ★ 여기에는 **그 낱말만으로 증권 기사가 확실한 것**만 넣는다. 「급등」·
#: 「급락」·「매수」·「매도」·「주주」처럼 사업 사실 기사에도 흔히 쓰이는 낱말은
#: 넣지 않는다 — 「매출 급등」·「지분 매수」·「최대주주 변경」·「주주환원 정책」은
#: 회사가 한 일이거나 공시 사실이라 근거로 쓸 수 있어야 한다. 그런 낱말은
#: 아래 ``STOCK_PHRASES``의 결합형으로만 잡는다.
STOCK_KEYWORDS: Final[tuple[str, ...]] = (
    "주가",  # 시세를 다룬 기사
    "목표주가",  # 증권사가 제시한 목표 시세
    "52주",  # 52주 신고가·신저가 시세 기사
    "수혜주",  # 「~ 수혜주는」 종목 추천 칼럼의 대표 표현
    "관련주",  # 주제를 묶어 종목을 나열하는 글
    "테마주",  # 위와 같은 종목 묶음 글
    "증권가",  # 증권업계 관측을 옮긴 글
    "애널리스트",  # 증권사 분석가 의견을 옮긴 글
    "투자의견",  # 증권사 의견 등급
    "목표가",  # 「목표주가」의 줄임 표기
    "시총",  # 시가총액 순위·변동 기사
    "시가총액",  # 위와 같음
)
#: 낱말 하나로는 사업 기사와 못 가르지만 붙어 나오면 증권 문맥이 확실한 말.
#: 띄어쓰기 차이를 타지 않게 공백을 지운 뒤 견준다(「매수 추천」·「매수추천」).
#:
#: 여기에 낱말 「주주」를 단독으로 넣으면 회사 공식 행사인 「주주총회」 기사가
#: 통째로 걸린다. 그래서 「주주 가치 제고」·「소액주주」처럼 붙여서만 쓴다.
STOCK_PHRASES: Final[tuple[str, ...]] = (
    "주가 급등",  # 「매출 급등」과 가르려고 「주가」를 붙여서만 본다
    "주가 급락",  # 위와 같음
    "매수 추천",  # 「지분 매수」와 가르려고 증권사 권유 문맥으로만 본다
    "매도 추천",  # 위와 같음
    "매수 의견",  # 증권사 의견 등급의 다른 표기
    "매도 의견",  # 위와 같음
    "주주 가치 제고",  # 「주주환원 정책」 같은 공시 사실과 가르는 투자 담론
    "소액주주",  # 지분 다툼·주주행동 기사
)
#: 대괄호 꼬리표에 이 말이 있으면 연재 칼럼 표식으로 본다. 어휘 자체는
#: 본문 어디에나 흔히 나오므로 꼬리표 안에서만 찾는다.
STOCK_COLUMN_TAG_KEYWORDS: Final[tuple[str, ...]] = (
    "머니",  # 「[○○머니]」 꼴의 재테크 연재 꼬리표
    "증시",  # 증시 시황 연재
    "마켓",  # 시장 시황 연재
    "투자",  # 투자 정보 연재
    "종목",  # 종목 추천 연재
)
#: 꼬리표를 감싸는 괄호 짝. 반각·전각 대괄호와 검은 괄호를 모두 본다.
COLUMN_TAG_BRACKETS: Final[tuple[tuple[str, str], ...]] = (
    ("[", "]"),
    ("［", "］"),
    ("【", "】"),
)
UNVERIFIED_MARKERS: Final[tuple[str, ...]] = (
    "업계에 따르면",
    "알려졌다",
    "전망이다",
)
RUMOR_ONLY_MARKERS: Final[tuple[str, ...]] = ("업계에 따르면", "알려졌다")
NEWSROOM_PATH_MARKERS: Final[tuple[str, ...]] = (
    "/newsroom",
    "/news-room",
    "/press",
    "/media",
    "/news/",
)
CORPORATE_DESIGNATORS: Final[tuple[str, ...]] = (
    "주식회사",
    "(주)",
    "（주）",
    "㈜",
)
TITLE_PARTICLES: Final[tuple[str, ...]] = (
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "와",
    "과",
    "도",
    "로",
)
ATTRIBUTION_ROLE_MARKERS: Final[tuple[str, ...]] = (
    "대표이사",
    "공동대표",
    "각자대표",
    "부대표",
    "대표",
    "최고경영자",
    "ceo",
    "회장",
    "사장",
    "임원",
    "관계자는",
    "관계자",
)
QUOTE_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("「", "」"),
    ("“", "”"),
    ('"', '"'),
)

KIND_PRESS_RELEASE: Final[str] = "press_release"
KIND_INTERVIEW: Final[str] = "interview"
KIND_EVENT: Final[str] = "event"
KIND_OTHER: Final[str] = "other"
ALLOWED_KINDS: Final[tuple[str, ...]] = (
    KIND_PRESS_RELEASE,
    KIND_INTERVIEW,
    KIND_EVENT,
    KIND_OTHER,
)

PRIORITY_NEWSROOM: Final[int] = 1
PRIORITY_PRESS_RELEASE: Final[int] = 2
PRIORITY_INTERVIEW: Final[int] = 3
PRIORITY_OTHER: Final[int] = 4

#: 9장은 여기 남아 있지만 보조 칸이 하나도 없어(``source_kind_policy``)
#: 매핑에서 ``no_supplementary_slot``으로 세어 뺀다. 목록에서 지우면 조용히
#: 사라져 제외 집계에 남지 않으므로 일부러 남긴다.
JOURNALIST_NARRATION_SECTIONS: Final[frozenset[str]] = frozenset(
    {
        "identity",
        "business_model",
        "portfolio",
        "past_changes",
        "operations_partners",
        "competitive_position",
    }
)
ATTRIBUTED_QUOTE_ONLY_SECTIONS: Final[frozenset[str]] = frozenset(
    {"current_challenges", "future_strategy"}
)
DATED_QUOTE_ONLY_SECTIONS: Final[frozenset[str]] = frozenset({"culture"})
NON_EXTENDABLE_SECTIONS: Final[frozenset[str]] = frozenset(
    {"current_challenges", "future_strategy"}
)
#: 뉴스 보조 문장을 아예 받지 않는 장(9장). 정본은 shared에 있고 여기서는
#: 같은 객체를 별명으로 다시 내보내기만 한다.
#:
#: ★ 왜 shared로 옮겼나 — 이 목록을 «수집하는 쪽»(여기)과 «보고서에 싣는
#:   쪽»(`composer/news_block.py`)이 둘 다 본다. 여기 두면 composer가
#:   news_intake를 직접 import하게 되어 feature 경계를 깬다. 새 이름을 만들지
#:   않고 «같은 객체»를 가리켜, 어느 쪽으로 읽어도 같은 값이 되게 한다.
#: ★ 기간만 1년으로 두는 NON_EXTENDABLE_SECTIONS와는 뜻이 다르므로(그쪽은
#:   조건부로 대상이 된다) 하나로 합치지 않는다.
NEWS_EXCLUDED_SECTIONS: Final[frozenset[str]] = NEWS_EXCLUDED_SECTION_IDS

#: 뉴스 경로가 「왜」 열렸는지. 대상 장을 정하는 규칙과 한 자리에서 나오므로
#: 호출부가 같은 조건을 다시 계산하지 않아도 된다. 창 이름은 이 값으로만
#: 정한다 — 공식 웹 문서 수를 호출부가 또 읽으면 두 판정이 어긋난다.
NEWS_TRIGGER_NONE: Final[str] = "none"
#: 준비되지 않은 장이 있어 열렸다. 기간은 `needs_extended_window`가 따로 정한다.
NEWS_TRIGGER_UNREADY: Final[str] = "unready_section"
#: 미달인 장은 없지만 공식 웹 문서가 0건이라 열렸다. 기간은 늘 1년이다.
NEWS_TRIGGER_WEB_ZERO: Final[str] = "web_zero_backfill"

SOURCE_KIND_NEWS: Final[str] = "news"
ORIGIN_NEWS_INTAKE: Final[str] = "news_intake"
NUMERIC_VALUE_PATTERN: Final[str] = r"[0-9０-９]|[%％]|(?:한|두|세|네|몇)\s*배"

EXCLUDED_COMPANY_NOT_MENTIONED: Final[str] = "company_not_mentioned"
EXCLUDED_RUMOR_ONLY: Final[str] = "rumor_only"
EXCLUDED_STOCK_ARTICLE: Final[str] = "stock_article"
EXCLUDED_INVALID_DATE: Final[str] = "invalid_date"
EXCLUDED_OUTSIDE_WINDOW: Final[str] = "outside_window"
EXCLUDED_INVALID_URL: Final[str] = "invalid_url"
EXCLUDED_DUPLICATE_RELEASE: Final[str] = "duplicate_press_release"
EXCLUDED_CANDIDATE_LIMIT: Final[str] = "candidate_limit"
EXCLUDED_INVALID_JSON: Final[str] = "classification_invalid_json"
EXCLUDED_UNKNOWN_ID: Final[str] = "classification_unknown_id"
EXCLUDED_DUPLICATE_ID: Final[str] = "classification_duplicate_id"
EXCLUDED_UNKNOWN_SECTION: Final[str] = "classification_unknown_section"
#: 장은 허용됐지만 보조 종류가 주장할 수 있는 의미 칸이 하나도 없어 뺀 문장.
EXCLUDED_NO_SUPPLEMENTARY_SLOT: Final[str] = "no_supplementary_slot"
EXCLUDED_TOO_MANY_SECTIONS: Final[str] = "classification_too_many_sections"
EXCLUDED_UNKNOWN_KIND: Final[str] = "classification_unknown_kind"
EXCLUDED_READY_SECTION: Final[str] = "ready_section"
EXCLUDED_FETCH_FAILED: Final[str] = "fetch_failed"
EXCLUDED_NUMERIC_SENTENCE: Final[str] = "numeric_sentence"
EXCLUDED_UNVERIFIED_SENTENCE: Final[str] = "unverified_sentence"
EXCLUDED_QUOTE_REQUIRED: Final[str] = "quote_required"
EXCLUDED_ATTRIBUTION_REQUIRED: Final[str] = "attribution_required"
EXCLUDED_PUBLISHED_ON_REQUIRED: Final[str] = "published_on_required"
EXCLUDED_DUPLICATE_SENTENCE: Final[str] = "duplicate_sentence"
#: 같은 기사에서 상한(MAX_FRAGMENTS_PER_ARTICLE)을 넘겨 뺀 문장.
EXCLUDED_ARTICLE_FRAGMENT_LIMIT: Final[str] = "article_fragment_limit"

#: 기사 본문 읽기 실패 사유. 예전에는 아래 사유가 모두 ``fetch_failed`` 하나로
#: 뭉개져 「robots가 막았다」와 「200인데 본문이 0자다」를 운영 로그에서 가를 수
#: 없었다. 사유마다 고쳐야 할 곳이 다르므로(robots는 손댈 수 없고, 본문 0자는
#: 추출 폴백으로 살릴 수 있다) 코드도 따로 둔다.
#: 주소 자체가 http/https 공개 웹이 아니거나 origin을 만들 수 없다.
EXCLUDED_FETCH_ORIGIN_DENIED: Final[str] = "fetch_origin_denied"
#: robots.txt가 이 경로를 막았거나 robots.txt 자체를 확인하지 못했다(fail-closed).
EXCLUDED_FETCH_ROBOTS_BLOCKED: Final[str] = "fetch_robots_blocked"
#: 200을 받았지만 본문 폴백을 다 거쳐도 쓸 글자가 없었다.
EXCLUDED_FETCH_EMPTY_BODY: Final[str] = "fetch_empty_body"
#: 받은 글자에 대체문자(U+FFFD)가 너무 많다 — 해독이 깨졌다는 뜻이다.
EXCLUDED_FETCH_DECODE_ERROR: Final[str] = "fetch_decode_error"
#: 시간 제한 안에 응답을 끝내지 못했다.
EXCLUDED_FETCH_TIMEOUT: Final[str] = "fetch_timeout"
#: 상태 코드조차 받지 못한 그 밖의 전송 실패(DNS·연결 거부·응답 형식 거부 등).
EXCLUDED_FETCH_TRANSPORT_ERROR: Final[str] = "fetch_transport_error"
#: HTTP 상태별 사유 코드의 앞머리. 403·429처럼 상태마다 대응이 달라서
#: 하나로 합치지 않고 ``fetch_http_403`` 꼴로 상태를 그대로 남긴다.
FETCH_HTTP_CODE_PREFIX: Final[str] = "fetch_http_"

#: 본문을 어느 겹에서 얻었는지 남기는 단계 코드. 「메타 설명 한 문장으로
#: 겨우 건졌다」와 「기사 본문을 통째로 읽었다」는 근거의 두께가 다르므로
#: steps에 단계별 수를 남겨 사람이 가를 수 있게 한다.
BODY_STAGE_USABLE_RANGES: Final[str] = "usable_ranges"
BODY_STAGE_JSON_LD: Final[str] = "json_ld_article_body"
BODY_STAGE_ARTICLE_TAG: Final[str] = "article_tag"
BODY_STAGE_META_DESCRIPTION: Final[str] = "meta_description"
#: 파이프라인 밖에서 본문 글자를 그대로 주입받은 경우(시험·대체 수집기).
#: 실제 폴백 사다리를 거치지 않았으므로 위 네 단계와 섞지 않는다.
BODY_STAGE_PROVIDED: Final[str] = "provided_text"
#: 본문 폴백을 시도하는 순서. **이 tuple이 폴백 사다리의 정본이다** —
#: 항목을 빼면 그 겹이 실제로 꺼진다(음성 대조가 이 성질을 쓴다).
BODY_EXTRACTION_STAGE_ORDER: Final[tuple[str, ...]] = (
    BODY_STAGE_JSON_LD,
    BODY_STAGE_ARTICLE_TAG,
    BODY_STAGE_USABLE_RANGES,
    BODY_STAGE_META_DESCRIPTION,
)
#: 본문으로 인정하는 최소 글자 수. 메타 설명 한 문장(보통 80~160자)은 넘고,
#: 「더보기」 같은 조각 글자는 넘지 못하는 자리에 둔다.
BODY_MIN_CHARS: Final[int] = 20
#: 해독이 깨졌다고 볼 대체문자 비율. 정상 문서에도 U+FFFD가 한두 개 섞일 수
#: 있으므로 개수가 아니라 비율로 본다.
DECODE_REPLACEMENT_RATIO_LIMIT: Final[float] = 0.02
DECODE_REPLACEMENT_CHAR: Final[str] = "\ufffd"

#: 한 기사에서 본문을 시도할 주소의 순서. 언론사 원문을 먼저 보고, 그 주소가
#: 막히거나 본문이 비면 검색 서비스가 준 주소를 같은 규칙(robots 포함)으로
#: 시도한다. 순서를 뒤집으려면 이 tuple만 바꾼다.
BODY_FETCH_URL_FIELD_ORDER: Final[tuple[str, ...]] = (
    "source_url",
    "originallink",
    "link",
)

#: 같은 기사를 가리키는 «표기만 다른» 주소를 만들 때 쓰는 변형과 그 순서.
#: 네이버 검색이 주는 originallink에는 ``http://``나 ``www`` 없는 표기가 흔한데,
#: 언론사 대부분이 그것을 301로 정식 주소에 돌려보낸다. 리다이렉트를 따라가는
#: 대신 «정식 표기 주소로 처음부터 다시 요청»한다 — 그래야 그 origin의
#: robots.txt를 새로 확인하게 되고, 같은 origin만 허용하는 리다이렉트 방어를
#: 조금도 느슨하게 만들지 않는다.
URL_VARIANT_AS_GIVEN: Final[str] = "as_given"
URL_VARIANT_HTTPS_UPGRADE: Final[str] = "https_upgrade"
URL_VARIANT_WWW_TOGGLE: Final[str] = "www_toggle"
URL_VARIANT_ORDER: Final[tuple[str, ...]] = (
    URL_VARIANT_AS_GIVEN,
    URL_VARIANT_HTTPS_UPGRADE,
    URL_VARIANT_WWW_TOGGLE,
)
#: 주소 변형 상한. 변형마다 robots.txt 확인이 한 번 더 붙으므로 늘리면
#: 요청 수가 그대로 늘어난다.
MAX_URL_VARIANTS: Final[int] = 3

# 새 수집 경로의 상한은 기사 수를 채우는 목표가 아니라 요청 비용의 경계다.
COLLECTION_POLICY_VERSION: Final[str] = "news-grounded-v5"
NAME_ACRONYM_MIN_CHARS: Final[int] = 2
NAME_ACRONYM_MAX_CHARS: Final[int] = 8
NAME_RETAINED_SUFFIX_MIN_CHARS: Final[int] = 2
NAME_DERIVED_VARIANT_BUDGET: Final[int] = 8
NAME_READING_VARIANT_BUDGET: Final[int] = 16
# 공식 한글 상호의 알파벳 독음과 공식 영문 표기가 함께 맞을 때만 사용한다.
# 접두만 떼어 별칭을 만들지 않고 상호의 나머지 글자를 모두 보존한다.
LATIN_LETTER_KOREAN_READINGS: Final[dict[str, tuple[str, ...]]] = {
    "A": ("에이",), "B": ("비",), "C": ("씨", "시"), "D": ("디",),
    "E": ("이",), "F": ("에프",), "G": ("지",), "H": ("에이치",),
    "I": ("아이",), "J": ("제이",), "K": ("케이",), "L": ("엘",),
    "M": ("엠",), "N": ("엔",), "O": ("오",), "P": ("피",),
    "Q": ("큐",), "R": ("알", "아르"), "S": ("에스",), "T": ("티",),
    "U": ("유",), "V": ("브이",), "W": ("더블유", "더블류"),
    "X": ("엑스",), "Y": ("와이",), "Z": ("제트", "지"),
}
ENGLISH_CORPORATE_SUFFIX_PATTERN: Final[str] = (
    r"(?:[,\s]+(?:co\.?\s*,?\s*ltd\.?|inc(?:orporated)?\.?|corp(?:oration)?\.?|limited|ltd\.?))+$"
)
NAME_PARTICLE_PATTERN: Final[str] = r"(?:은|는|이|가|을|를|의|와|과|에서|에게|관계자|대표|측)"
NAME_SEPARATOR_PATTERN: Final[str] = r"[\s.,·ㆍ'’\"()\[\]_-]*"
NAME_RESOLUTION_PENDING: Final[str] = "name_resolution_pending"
SEARCH_CALL_BUDGET: Final[int] = 12
# 논리 검색 하나가 재시도하더라도 실제 전송은 이 경계를 함께 나눠 쓴다.
SEARCH_TRANSPORT_ATTEMPT_BUDGET: Final[int] = 12
SEARCH_TRANSPORT_UNOBSERVED: Final[str] = "news_search_transport_unobserved"
SEARCH_TRANSPORT_METADATA_INVALID: Final[str] = "news_search_transport_metadata_invalid"
SEARCH_TRANSPORT_BUDGET_UNENFORCED: Final[str] = "news_search_transport_budget_unenforced"
SEARCH_TRANSPORT_BUDGET_EXHAUSTED: Final[str] = "news_search_transport_budget_exhausted"
SEARCH_TRANSPORT_BUDGET_EXCEEDED: Final[str] = "news_search_transport_budget_exceeded"
SEARCH_TRANSPORT_OBSERVATION_PENDING: Final[str] = "transport_observation_pending"
SEARCH_PAGE_SIZE: Final[int] = 20
SEARCH_CANDIDATE_BUDGET: Final[int] = 80
SEARCH_SECONDS_BUDGET: Final[int] = 90
SEARCH_ALIAS_BUDGET: Final[int] = 2
SEARCH_TITLE_CHARS: Final[int] = 500
SEARCH_DESCRIPTION_CHARS: Final[int] = 2_000
SEARCH_URL_CHARS: Final[int] = 2_000
COMPANY_CONTEXT_CHARS: Final[int] = 4_000
BODY_ARTICLE_BUDGET: Final[int] = 24
BODY_CALL_BUDGET: Final[int] = 48
BODY_CHARS_PER_ARTICLE: Final[int] = 12_000
BODY_TOTAL_CHARS_BUDGET: Final[int] = 200_000
COLLECTION_SECONDS_BUDGET: Final[int] = 180
GROUNDED_BATCH_SIZE: Final[int] = 4
GROUNDED_CALL_BUDGET: Final[int] = 8
GROUNDED_MAX_TOKENS: Final[int] = 5_000
GROUNDED_PROMPT_CHARS_BUDGET: Final[int] = 60_000
GROUNDED_RESPONSE_CHARS_BUDGET: Final[int] = 60_000
GROUNDED_MIN_EXCERPT_CHARS: Final[int] = 25
GROUNDED_MAX_EXCERPT_CHARS: Final[int] = 1_000
GROUNDED_EXCERPTS_PER_ARTICLE: Final[int] = 2
GROUNDED_SUBJECT_CHARS: Final[int] = 100
SUBJECT_GENERIC_TERMS: Final[frozenset[str]] = frozenset({
    "그", "그녀", "그룹", "가수", "배우", "제품", "서비스", "브랜드", "고객",
    "회사", "기업", "관계자", "대표", "사장", "회장", "사업", "계약", "이번",
})
SUBJECT_RELATION_MARKERS: Final[tuple[str, ...]] = (
    "소속", "브랜드", "제품", "서비스", "개발", "제조", "공급", "판매", "출시",
    "운영", "제작", "배급", "고객", "계약", "협력", "파트너", "매니지먼트",
    "대표", "임원", "직원", "사업부", "사업장", "공장",
)
FINAL_ARTICLE_BUDGET: Final[int] = 12
FINAL_FRAGMENT_BUDGET: Final[int] = 16
FINAL_FRAGMENT_CHARS_BUDGET: Final[int] = 12_000
SUFFICIENT_DISTINCT_EVENTS: Final[int] = 6
SUFFICIENT_DISTINCT_TOPICS: Final[int] = 3
WINDOW_MONTHS: Final[tuple[int, ...]] = (12, 24, 36)
WINDOW_ARTICLE_BUDGETS: Final[tuple[int, ...]] = (16, 4, 4)
CONTENT_DUPLICATE_SIMILARITY: Final[float] = 0.88
EVENT_DUPLICATE_SIMILARITY: Final[float] = 0.84
NEWS_TRIGGER_REFRESH: Final[str] = "recent_news_refresh"
SEARCH_TOPICS: Final[tuple[tuple[str, str], ...]] = (
    ("products", "사업"),
    ("partnerships", "협력"),
    ("strategy", "전략"),
    ("official", "발표"),
)
GROUNDED_TOPICS: Final[tuple[str, ...]] = (
    "business", "products", "partnerships", "strategy", "operations", "people", "risk",
)
GROUNDED_CLAIM_KINDS: Final[tuple[str, ...]] = (
    "reported_fact", "company_statement", "company_plan",
)
GROUNDED_TEMPORAL_STATES: Final[tuple[str, ...]] = ("completed", "ongoing", "planned")
GROUNDED_SOURCE_TYPES: Final[tuple[str, ...]] = (
    "official_release", "news_report", "opinion", "blog", "community", "unknown",
)
# 매체 도메인은 회사별 예외가 아니다. 확인된 전문 매체는 정책의 추가 목록으로
# 확장하되 검색 결과에 URL이 있다는 이유만으로 자동 승격하지 않는다.
TRUSTED_PUBLISHER_DOMAINS: Final[tuple[str, ...]] = (
    "yna.co.kr", "yonhapnews.co.kr", "newsis.com", "news1.kr", "reuters.com",
    "apnews.com", "bloomberg.com", "ft.com", "wsj.com", "hankyung.com",
    "mk.co.kr", "edaily.co.kr", "mt.co.kr", "sedaily.com", "fnnews.com",
    "heraldcorp.com", "asiae.co.kr", "chosun.com", "joongang.co.kr", "donga.com",
    "hani.co.kr", "khan.co.kr", "hankookilbo.com", "kbs.co.kr", "imbc.com",
    "sbs.co.kr", "jtbc.co.kr", "ytn.co.kr", "etnews.com", "zdnet.co.kr",
    "bloter.net", "byline.network", "thebell.co.kr", "dnews.co.kr", "dt.co.kr",
    "newstomato.com", "businesspost.co.kr", "ebn.co.kr", "dailypharm.com",
    "hitnews.co.kr", "medipana.com", "thelec.kr", "medigatenews.com",
    "newsway.co.kr", "businesswatch.co.kr", "sisajournal-e.com",
    "sportschosun.com", "osen.co.kr", "xportsnews.com", "hangyo.com",
    "fashionbiz.co.kr", "klnews.co.kr",
)
# 전문 매체 확장 근거. 발행자 확인은 본문 정확성 보증이 아니므로 모든 기사가
# 같은 법인·실질내용·원문범위 검수를 거친다. 미확인 도메인은 진단에 보류한다.
SPECIALIST_PUBLISHER_REFERENCES: Final[tuple[tuple[str, str, str], ...]] = (
    ("sportschosun.com", "스포츠·엔터", "https://www.sportschosun.com/company/"),
    ("osen.co.kr", "스포츠·엔터", "https://www.osen.co.kr/"),
    ("xportsnews.com", "스포츠·게임", "https://www.xportsnews.com/"),
    ("hangyo.com", "교육", "https://www.hangyo.com/"),
    ("fashionbiz.co.kr", "패션", "https://fashionbiz.co.kr/about"),
    ("klnews.co.kr", "물류", "https://www.klnews.co.kr/rssIndex.html"),
)
BLOCKED_PUBLISHER_DOMAINS: Final[tuple[str, ...]] = (
    "blog.naver.com", "cafe.naver.com", "tistory.com", "blog.daum.net",
    "cafe.daum.net", "brunch.co.kr", "medium.com", "dcinside.com", "fmkorea.com",
    "clien.net", "ppomppu.co.kr", "theqoo.net", "instiz.net", "youtube.com",
    "facebook.com", "instagram.com", "x.com", "twitter.com", "reddit.com",
)
NEWS_PORTAL_DOMAINS: Final[tuple[str, ...]] = ("news.naver.com", "n.news.naver.com")
NON_ARTICLE_TEXT_MARKERS: Final[tuple[str, ...]] = (
    "개인정보처리방침", "쿠키 정책", "회원가입", "무단전재", "재배포 금지",
    "본문 바로가기", "기사 공유", "기사를 공유", "관련기사 더보기", "로그인하세요",
)
MARKET_COMMENTARY_MARKERS: Final[tuple[str, ...]] = (
    "목표주가", "투자의견", "주가 상승", "주가 하락", "주가가", "주가는",
    "투자심리", "수혜주", "테마주", "관련주", "종목 추천", "매수 추천",
)
IDENTITY_STOP_WORDS: Final[frozenset[str]] = frozenset({
    "회사", "기업", "법인", "주식회사", "대한민국", "한국", "사업", "주요",
    "서비스", "제공", "운영", "관련", "대상", "중심", "활동", "업체", "기타",
    "대표", "대표이사", "영위", "하는", "있다", "위한", "통해", "분야",
})
SEARCH_REASON_CODES: Final[frozenset[str]] = frozenset({
    "news_search_ok", "news_search_not_configured", "news_search_authentication_failed",
    "news_search_rate_limited", "news_search_temporarily_unavailable", "news_search_invalid_response",
    "news_search_daily_cap", "news_search_internal_error", "news_search_response_limit",
    "news_search_invalid_request", SEARCH_TRANSPORT_BUDGET_EXHAUSTED,
})
SEARCH_TRANSPORT_ATTEMPT_REASON_CODES: Final[frozenset[str]] = frozenset({
    "news_search_ok", "news_search_authentication_failed", "news_search_rate_limited",
    "news_search_temporarily_unavailable", "news_search_invalid_response",
})
SEARCH_ZERO_TRANSPORT_REASON_CODES: Final[frozenset[str]] = frozenset({
    "news_search_not_configured", "news_search_daily_cap", "news_search_invalid_request",
    "news_search_invalid_response", SEARCH_TRANSPORT_BUDGET_EXHAUSTED,
})
SEARCH_BUDGET_REASON_CODES: Final[frozenset[str]] = frozenset({
    "search_budget_exhausted", SEARCH_TRANSPORT_BUDGET_EXHAUSTED,
})
JSON_LD_ARTICLE_TYPES: Final[frozenset[str]] = frozenset({
    "Article", "NewsArticle", "ReportageNewsArticle", "AnalysisNewsArticle", "PressRelease",
})
BODY_FAILURE_CODES: Final[frozenset[str]] = frozenset({
    EXCLUDED_FETCH_FAILED, EXCLUDED_FETCH_ORIGIN_DENIED, EXCLUDED_FETCH_ROBOTS_BLOCKED,
    EXCLUDED_FETCH_EMPTY_BODY, EXCLUDED_FETCH_DECODE_ERROR, EXCLUDED_FETCH_TIMEOUT,
    EXCLUDED_FETCH_TRANSPORT_ERROR,
})
ARTICLE_PUBLISHED_META_KEYS: Final[frozenset[str]] = frozenset({
    "article:published_time", "datepublished", "pubdate", "publication_date",
})
ATTRIBUTED_STATEMENT_MARKERS: Final[tuple[str, ...]] = (
    "밝혔다", "말했다", "설명했다", "전했다", "발표했다", "강조했다", "계획", "예정", "방침",
)
FUTURE_PLAN_PATTERN: Final[str] = r"(?:할|될|할\s*수\s*있도록)\s*(?:계획|예정)|하기로\s*(?:했다|계획)|목표로\s*(?:한다|하고|추진)"
