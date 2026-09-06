"""언론사 뉴스 도입의 닫힌 어휘와 상한."""

from __future__ import annotations

from typing import Final


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
#: 뉴스 보조 문장을 아예 받지 않는 장. 9장은 「회사가 밝힌 차별점」이라
#: 회사가 스스로 밝힌 말만 싣는 장이고, 기자가 쓴 비교나 증권 칼럼의 해석은
#: 그 정의에 맞지 않는다. 기간만 1년으로 두는 NON_EXTENDABLE_SECTIONS와는
#: 뜻이 다르므로(그쪽은 조건부로 대상이 된다) 하나로 합치지 않는다.
NEWS_EXCLUDED_SECTIONS: Final[frozenset[str]] = frozenset({"competitive_position"})

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
