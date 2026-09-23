"""같은 사건 키의 다른 기사라도 시점·값·부정·조건이 다르면 다른 사실이다."""

from hashlib import sha256
from dataclasses import replace

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.dedupe import (
    _document_keys, _overlap, _signature,
    drop_cross_section_duplicates, duplicates_kept_sentence,
)
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence


BASE = (
    "가나다전자는 2025년 국내 기업 고객을 대상으로 보안 진단 솔루션을 공급했다. "
    "계약 규모는 10억원이며 현장 점검과 유지보수 서비스를 함께 제공했다. "
    "보호자 동의를 얻은 경우에만 추가 인증 절차를 생략하고 고객에게 결과 안내서를 전달했다."
)
VARIANTS = (
    ("다른연도", BASE.replace("2025년", "2026년")),
    ("다른금액", BASE.replace("10억원", "20억원")),
    ("부정", BASE.replace("솔루션을 공급했다", "솔루션을 공급하지 않았다")),
    ("조건유실", BASE.replace("보호자 동의를 얻은 경우에만 ", "")),
    ("조건반대", BASE.replace("보호자 동의를 얻은 경우에만", "보호자 동의를 얻지 않은 경우에만")),
    ("다른주체", BASE.replace("가나다전자", "라마바전자")),
)


def _inputs(left_text, right_text):
    fragments = tuple(CollectedFragment(
        str(number), "news", text,
        source_url=f"https://fixtures.invalid/article/{number}",
        document_title=f"독립 기사 {number}", document_date="2026-09-08",
        document_identity=f"url:https://fixtures.invalid/article/{number}",
        # 서로 다른 기사 전체가 우연히 같은 문서 지문으로 연결되지 않게 한다.
        document_content_sha256=sha256(f"기사 {number}\n{text}".encode("utf-8")).hexdigest(),
        formal_source_kind="news", source_publisher="시험신문",
        news_claim_kind="reported_fact", news_temporal_status="completed",
        news_event_key="보안 진단 솔루션 공급 계약", news_source_category="news_report",
        news_grounded=True,
    ) for number, text in enumerate((left_text, right_text), start=1))
    documents = _document_keys(fragments)
    assert not documents["1"] & documents["2"], "같은 문서의 기존 경로를 검사하고 있습니다"
    sentences = tuple(ComposedSentence(
        text, (str(number),), "확인", verification_state="verified",
    ) for number, text in enumerate((left_text, right_text), start=1))
    return fragments, sentences


@pytest.mark.parametrize("_name,other", VARIANTS, ids=[name for name, _ in VARIANTS])
def test_다른기사의_다른사실은_장간중복으로_지우지않는다(_name, other):
    fragments, sentences = _inputs(BASE, other)
    assert _overlap(_signature(BASE), _signature(other)) >= 0.85
    by_section = {"business_model": (sentences[0],), "operations_partners": (sentences[1],)}
    report = ComposedReport(tuple(ComposedSection(sid, by_section.get(sid, ())) for sid in SECTION_IDS))

    result, dropped = drop_cross_section_duplicates(report, fragments=fragments)

    assert dropped == 0
    assert tuple(sentence.text for section in result.sections for sentence in section.sentences) == (BASE, other)


@pytest.mark.parametrize("_name,other", VARIANTS, ids=[name for name, _ in VARIANTS])
def test_다른기사의_다른사실은_이동대상의_기존문장과도_합치지않는다(_name, other):
    fragments, sentences = _inputs(BASE, other)

    assert not duplicates_kept_sentence(sentences[1], (sentences[0],), fragments=fragments)


def test_다른기사라도_전체_사실문장이_동일하면_중복이다():
    fragments, sentences = _inputs(BASE, BASE)
    assert duplicates_kept_sentence(sentences[1], (sentences[0],), fragments=fragments)


def test_전체문장의_공백폭만_다르면_같은_사실로_비교한다():
    fragments, sentences = _inputs(BASE, BASE.replace(" ", "  "))
    assert duplicates_kept_sentence(sentences[1], (sentences[0],), fragments=fragments)


def test_제품명_대소문자가_다르면_다른_사실을_보존한다():
    left = BASE.replace("솔루션", "제품 Alpha")
    right = BASE.replace("솔루션", "제품 alpha")
    fragments, sentences = _inputs(left, right)
    assert not duplicates_kept_sentence(sentences[1], (sentences[0],), fragments=fragments)


def test_typed_운반종류는_정식뉴스종류와_승인메타로_판정한다():
    fragments, sentences = _inputs(BASE, BASE)
    fragments = tuple(replace(fragment, kind="typed-evidence-v3:" + sha256(str(index).encode()).hexdigest())
                      for index, fragment in enumerate(fragments))
    assert duplicates_kept_sentence(sentences[1], (sentences[0],), fragments=fragments)


def test_사건키가_다르면_새로운_기사간경로가_열리지않는다():
    fragments, sentences = _inputs(BASE, BASE)
    fragments = (fragments[0], replace(fragments[1], news_event_key="다른 사건"))
    assert not duplicates_kept_sentence(sentences[1], (sentences[0],), fragments=fragments)


@pytest.mark.parametrize("changes", (
    {"news_grounded": False},
    {"formal_source_kind": "official_identity_verified_web_page"},
    {"news_temporal_status": "unregistered"},
    {"news_claim_kind": "unregistered"},
    {"news_claim_kind": "company_plan"},
    {"kind": "typed-evidence-v3:short"},
    {"kind": "typed-evidence-v1:" + "a" * 64},
    {"kind": "회사 공식 자료"},
))
def test_사건메타만_있는_미승인_조각으로_기사간경로를_열지않는다(changes):
    fragments, sentences = _inputs(BASE, BASE)
    fragments = tuple(replace(fragment, **changes) for fragment in fragments)
    assert not duplicates_kept_sentence(sentences[1], (sentences[0],), fragments=fragments)
