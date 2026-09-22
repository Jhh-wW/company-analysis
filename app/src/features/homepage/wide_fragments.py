"""문서의 usable_ranges 텍스트 구간을 슬롯 태그된 조각(fragment)으로 바꾼다.

★ URL 페이지 유형은 검사할 슬롯 범위를 좁히는 힌트일 뿐이다. 본문에서 그
  슬롯의 직접 신호가 실제로 확인될 때만 조각을 만든다. URL이 ``/careers``
  라는 이유만으로 문화 사례가 있다고 주장하지 않는다.
★ URL 유형을 못 알아낸 REQUIRED 공식 문서(특히 ``/`` 메인)는 본문을 전체
  슬롯 어휘로 검사한다. 경로가 아니라 본문이 무엇을 증명하는지로 분류한다.
★ 외부 exact-link IR 첨부와 신원만 맞은 교차 도메인 페이지는 provenance
  문서로만 보존하며 Writer 슬롯 조각을 만들지 않는다.
★ ``slot_id``는 ``WideFragment``가 강제하는 허용 어휘(정본은
  `app/src/shared/report_evidence/policy.py`)만 나온다. comparison_*·
  limitation·historical_performance는 이 어휘에 없으므로 이 모듈에서
  «절대» 만들어지지 않는다(시험으로 0건을 고정한다).
★ 계약 generation=8: ``build_fragments``는 ``company_id``를 **필수
  키워드 인자**로 받는다 — ``document.company_id``에서 자동으로 채워
  넣지 않는다. 호출자(문서마다 이 함수를 부르는 다음 담당자)가 «지금
  이 조회가 실제로 어느 회사를 대상으로 했는지»를 매번 직접 넘겨야
  한다. document의 company_id와 자동으로 같다고 가정하면 다른 회사
  조회 결과가 섞여도 조용히 통과할 수 있어, 그 가정 자체를 없앴다.
  ``build_fragments``는 이 명시 인자를 **공격 시험 전용**으로 유지한다
  — 「호출자가 일부러 다른 값을 넘기는 정당한 운영 시나리오」는 없다.
  운영 결합부도 아래 ``build_fragments_for_collection``을 쓴다 — 수집 결과 자신에서
  company_id를 한 번만 꺼내 모든 문서에 그대로 실으므로, 호출 지점에서
  값을 잘못 넘길 방법이 구조적으로 없다(``build_fragments``를 직접
  호출하며 손으로 company_id를 옮겨 적지 않는다). 실서비스 FULL 조립부도
  ``web.official_evidence_adapter``에서 이 함수만 호출한다.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import urljoin, urlsplit, urldefrag

from lxml import etree, html

from src.features.homepage.challenge_evidence import (
    ChallengeEvidence,
    classify_challenge_evidence,
)
from src.features.homepage.constants import (
    WIDE_MAX_CHARS_PER_RANGE,
    WIDE_REQUIRED_SLOT_IDS,
    WIDE_SLOT_BODY_KEYWORDS,
    WIDE_SOURCE_KIND_IR_PDF,
    WIDE_SOURCE_KIND_RECRUIT_PAGE,
    WIDE_SOURCE_KIND_WEB_PAGE,
)
from src.features.homepage.wide_domain import classify_official_page_url
from src.features.homepage.wide_types import (
    WideCollectionResult,
    WideDocumentIdentity,
    WideFragment,
)
from src.shared.report_evidence.source_kind_policy import (
    document_slots_for_formal_source_kind,
    formal_document_is_writer_eligible,
)

#: 구간 본문에 후보 슬롯의 직접 신호 키워드가 있을 때의 점수.
_SCORE_BODY_KEYWORD_MATCH = 700

_REASON_PAGE_TYPE_SIGNAL = "page_type_signal"
_REASON_BODY_KEYWORD_MATCH = "body_keyword_match"

_VERIFIED_CASE_MARKERS: tuple[str, ...] = (
    "사례", "후기", "인터뷰", "스토리", "프로젝트", "수상", "인증"
)
_VERIFIED_CASE_ACTIONS: tuple[str, ...] = (
    "실행", "적용", "도입", "운영", "개선", "달성", "완료", "수상", "인증"
)


def extract_list_items(raw_html: str, base_url: str) -> tuple[tuple[str, str, str, str], ...]:
    """목록 카드의 원문·제목·날짜·href를 추가 조회 없이 함께 읽는다."""
    try:
        soup = html.fromstring(raw_html)
    except (etree.ParserError, ValueError):
        return ()
    for node in soup.xpath("//nav|//header|//footer|//aside|//script|//style"):
        if node.getparent() is not None:
            node.drop_tree()
    date_pattern = re.compile(r"(?<!\d)(20\d{2})\s*(?:[.-]|년)\s*(\d{1,2})\s*(?:[.-]|월)\s*(\d{1,2})(?:\s*일)?(?!\d)")
    items: dict[str, tuple[str, str, str, str]] = {}
    has_list_container = False
    for anchor in soup.xpath(".//a[@href]"):
        href = str(anchor.get("href", "")).strip()
        try:
            url = urldefrag(urljoin(base_url, href))[0]
            parsed = urlsplit(url)
        except ValueError:
            continue
        if not href or href.startswith("#") or url == urldefrag(base_url)[0]:
            continue
        if parsed.scheme not in {"http", "https"} or parsed.netloc != urlsplit(base_url).netloc:
            continue
        # 바깥 목록 전체의 날짜를 빌려 다른 링크에 붙이지 않는다.
        card = anchor
        while card is not None and card.tag not in {"body", "html", "main"}:
            text = " ".join(" ".join(card.itertext()).split())
            matches = list(date_pattern.finditer(text))
            try:
                urls = {
                    urldefrag(urljoin(base_url, str(link.get("href", ""))))[0]
                    for link in card.xpath(".//a[@href]")
                }
            except ValueError:
                card = None
                break
            if len(matches) == 1 and len(urls | {url}) == 1:
                break
            if len(matches) > 1 or len(urls | {url}) > 1:
                card = None
                break
            card = card.getparent()
        if card is None or card.tag in {"body", "html", "main"}:
            continue
        match = matches[0]
        try:
            published_on = date(*(int(value) for value in match.groups())).isoformat()
        except ValueError:
            continue
        headings = card.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6|.//*[contains(@class, 'title')]")
        title = " ".join(" ".join((headings[0] if headings else anchor).itertext()).split())
        title = date_pattern.sub("", title).strip()
        if not title:
            continue
        has_list_container |= card.tag in {"li", "article"} or any(
            parent.tag in {"li", "article", "ul", "ol"} for parent in card.iterancestors()
        )
        items[url] = (text[:WIDE_MAX_CHARS_PER_RANGE], title, published_on, url)
    # 날짜 한 개가 있는 상세 페이지를 목록으로 오인하지 않는다.
    return tuple(items.values()) if len(items) > 1 or has_list_container else ()


def build_fragments(document: WideDocumentIdentity, *, company_id: str) -> tuple[WideFragment, ...]:
    """문서 하나의 usable_ranges 전부에서 슬롯 태그된 fragment를 만든다.

    Args:
        document: 이미 검증을 통과한 ``WideDocumentIdentity``.
        company_id: 이 조회가 실제로 대상으로 한 회사 식별자(필수 — 계약
            generation=8). ``document.company_id``와 다르게 넘겨도 이
            함수는 막지 않는다(그 판정은 앱 계약의 몫이다) — 호출자가
            «생각 없이 document 값을 그대로 베끼는» 습관을 없애기 위해
            의도적으로 독립된 인자로 뒀다.

    Returns:
        본문 신호가 확인된 슬롯의 ``WideFragment`` 튜플.
    """
    page_classification = classify_official_page_url(document.canonical_url)
    page_slots = page_classification.slot_ids
    if (
        document.source_kind
        in {WIDE_SOURCE_KIND_WEB_PAGE, WIDE_SOURCE_KIND_RECRUIT_PAGE}
        and document.source_kind != page_classification.source_kind
    ):
        return ()
    # 수집된 formal 문서와 Writer 자격은 다르다. 종류별 신뢰·필수
    # provenance 정본을 통과하지 못한 문서는 감사 차선에만 남긴다.
    if not formal_document_is_writer_eligible(document):
        return ()

    # IR PDF의 URL(`/ir/...pdf`)은 문서 내용의 의미 범위를 말해 주지 않는다.
    # HTML 페이지에만 URL 힌트를 쓰고 IR은 source_kind 정본의 광역 슬롯에서
    # 실제 본문 신호를 찾는다. 그렇지 않으면 고객·매출 본문이 있어도 URL의
    # `ir` 글자 때문에 과거/미래 슬롯으로만 축소되어 전부 사라진다.
    uses_page_slot_hint = document.source_kind != WIDE_SOURCE_KIND_IR_PDF
    if uses_page_slot_hint:
        candidate_slots = page_slots or WIDE_REQUIRED_SLOT_IDS
    else:
        owned_slots = document_slots_for_formal_source_kind(document.source_kind)
        candidate_slots = tuple(
            slot_id for slot_id in WIDE_REQUIRED_SLOT_IDS if slot_id in owned_slots
        )
    challenge_evidence = classify_challenge_evidence(document.usable_ranges)

    fragments: list[WideFragment] = []
    for index, text in enumerate(document.usable_ranges):
        slots_for_range = _matched_body_slots(
            text,
            candidate_slots,
            range_index=index,
            challenge_evidence=challenge_evidence,
        )
        if not slots_for_range:
            continue
        score = _SCORE_BODY_KEYWORD_MATCH
        reason_codes = (
            ((_REASON_PAGE_TYPE_SIGNAL,) if page_slots and uses_page_slot_hint else ())
            + (_REASON_BODY_KEYWORD_MATCH,)
        )

        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        item = next((item for item in document.list_items if item[0] == text), None)
        location = item[3] if item else f"{document.canonical_url} · 목록 {index + 1}번째 항목"
        slots_by_section: dict[str, list[str]] = {}
        for slot_id in slots_for_range:
            slots_by_section.setdefault(slot_id.split(":", 1)[0], []).append(slot_id)
        for section_id, covered_slot_ids in slots_by_section.items():
            primary_slot_id = covered_slot_ids[0]
            fragments.append(
                WideFragment(
                    company_id=company_id,
                    fragment_id=_fragment_id(document.document_id, index, section_id),
                    document_id=document.document_id,
                    location=location,
                    text_sha256=text_sha256,
                    text=text,
                    section_id=section_id,
                    slot_id=primary_slot_id,
                    score_millis=score,
                    reason_codes=reason_codes,
                    covered_slot_ids=tuple(covered_slot_ids),
                    range_index=index,
                    item_title=item[1] if item else "",
                    item_published_on=item[2] if item else "",
                    item_url=item[3] if item else "",
                )
            )
    return tuple(fragments)


def build_fragments_for_collection(result: WideCollectionResult) -> tuple[WideFragment, ...]:
    """수집 결과의 모든 문서에서 fragment를 한 번에 만드는 얇은 편의 함수.

    ★ 계약 generation=8 마지막 고리 — 운영 결합부는
      이 함수를 쓴다(``build_fragments``에 지역 거부를 추가하는 대신
      구조로 막는다). ``result.company_id``를 정본으로 모든 문서에 그대로
      싣는다 — **documents에서 역산하지 않는다.** documents에서 역산하면
      문서 생성부에 버그가 생겨 문서들이 일제히 엉뚱한 회사 값을 갖게 될
      때 서로는 일치하므로 대조할 정본이 없어 아무도 못 잡는다.
      ``result.company_id``는 ``collect_official_web_documents(company_id=...)``
      가 호출 인자로 받은 값이라 documents와 독립된 정본이고,
      ``WideCollectionResult.__post_init__``이 이미 모든 document·attempt의
      company_id가 이 값과 같은지 생성 시점에 확인해 뒀다(다르면 그
      시점에 ValueError로 걸린다) — 그래서 여기서는 다시 검증하지 않고
      곧바로 신뢰해 쓴다.

      실서비스 FULL 조립부도 이 함수로 공식 웹 문서 전체를 변환한다.

    Args:
        result: ``collect_official_web_documents``가 돌려준 수집 결과.

    Returns:
        모든 문서의 fragment를 이어붙인 튜플(결정론 순서 — documents
        순서·문서 내 구간 순서를 그대로 따른다). 문서가 0건이면 빈 튜플.
    """
    return tuple(
        fragment
        for document in result.documents
        for fragment in build_fragments(document, company_id=result.company_id)
    )


def _matched_body_slots(
    text: str,
    candidate_slots: tuple[str, ...],
    *,
    range_index: int,
    challenge_evidence: ChallengeEvidence,
) -> tuple[str, ...]:
    """후보 슬롯 중 구간 본문에 직접 신호가 있는 것만 고른다."""
    lowered = text.lower()
    return tuple(
        slot_id
        for slot_id in candidate_slots
        if _has_slot_body_signal(
            slot_id,
            lowered,
            range_index=range_index,
            challenge_evidence=challenge_evidence,
        )
    )


def _has_slot_body_signal(
    slot_id: str,
    lowered_text: str,
    *,
    range_index: int,
    challenge_evidence: ChallengeEvidence,
) -> bool:
    if slot_id == "past_changes:completed_execution" and re.search(
        r"투자(?:를)?\s*유치(?:했|하였|에\s*성공)", lowered_text
    ):
        return True
    if slot_id == "current_challenges:issue":
        return challenge_evidence.has_issue(range_index)
    if slot_id == "current_challenges:response":
        return challenge_evidence.has_response(range_index)
    if slot_id == "culture:verified_case":
        # 「사례」라는 메뉴/제목만으로 실제 사례가 존재한다고 주장하지 않는다.
        # 사례 표지와 실행·결과 중 하나가 함께 있어야 하며, 수상·인증은 그
        # 자체가 검증 가능한 행동·결과라 양쪽 신호를 동시에 충족한다.
        return (
            any(marker in lowered_text for marker in _VERIFIED_CASE_MARKERS)
            and any(action in lowered_text for action in _VERIFIED_CASE_ACTIONS)
        )
    return any(
        keyword.lower() in lowered_text
        for keyword in WIDE_SLOT_BODY_KEYWORDS.get(slot_id, ())
    )


def _fragment_id(document_id: str, index: int, section_id: str) -> str:
    """문서ID·구간 위치·장으로 결정론적 fragment_id를 만든다."""
    digest = hashlib.sha256(f"{document_id}:{index}:{section_id}".encode("utf-8")).hexdigest()
    return f"frag-{digest[:24]}"
