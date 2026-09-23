"""뉴스 원문 대체 후보의 정보 보존과 진짜 중복 정리 경계 (실제 run_v2 경로).

★ 조기 독립 검증 반례 — 검수 전에 «다른 문장과 수치 두 개가 겹친다»는 이유로 정확
  원문 후보를 빼면, 그 기사를 인용한 작가 문장이 검수에서 떨어질 때 조각이 «검수
  탈락»으로 분류돼 보도표에서도 막힌다. 다른 기간·제품·신규 계약 사실이 모두 사라진다.
  그래서 사전 수치 교집합 생략은 쓰지 않는다. 진짜 중복은 검수를 통과해 «실제로 남은»
  문장이 후보 원문의 모든 문장을 글자 그대로 담을 때만 정리한다.
"""

from __future__ import annotations

import pytest

from src.core import news_intake_switch
from src.features.composer.news_block import NEWS_BLOCK_HEADERS
from src.features.composer.news_usage import supplement_news_candidates
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.test_news_alternatives import _full
from src.features.composer.tests.test_news_block_channels import _news_fragment

_EARLIER = "가나다전자의 제품 갑은 2024년 매출 100억원과 영업이익 10억원을 기록했다."
_LATER = "가나다전자의 제품 을은 2025년 매출 100억원과 영업이익 10억원을 기록했고 신규 계약 3건을 따냈다."
_UNSUPPORTED_CAUSE = " 이는 신규 설비 투자 효과 때문이다."
_SHARED = "가나다전자는 2025년 설비 수주 20건을 기록했다."
_EXTRA = "가나다전자는 같은 해 부산 공장 가동률을 90%로 높였다."
_OTHER_EXTRA = "가나다전자는 같은 해 창원 공장을 새로 가동했다."


@pytest.fixture(autouse=True)
def _enabled_news(monkeypatch):
    monkeypatch.setenv("NEWS_INTAKE", "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def _fragment(fragment_id, text, *, event_key=None):
    fragment = _news_fragment(fragment_id, "2026-09-01", text, section_id="business_model")
    if event_key is None:
        return fragment
    from dataclasses import replace
    return replace(fragment, news_event_key=event_key)


def _body(output):
    return " ".join(text for section in output.report.sections for text, _cite in section.prose_lines)


def _news_rows(output):
    return [row[-1] for section in output.report.sections for table in section.tables
            if table.headers == list(NEWS_BLOCK_HEADERS) for row in table.rows]


def _decision_trail(output, fragment_id):
    row = next(entry for entry in output.news_usage_diagnostics["근거별판정"] if entry["조각"] == fragment_id)
    return row["사유"], {step["사유코드"] for step in row["검증경과"]}


def _rewrite_with_cause(text):
    return text + _UNSUPPORTED_CAUSE if text == _LATER else text


def _reject_cause(text, _entry):
    return "거짓" if _UNSUPPORTED_CAUSE.strip() in text else "참"


# ══════════════════════════════════════════════════════════
# ① 반례 — 수치가 겹치는 다른 기간·제품·추가 사실은 정확 원문으로 살아남는다
# ══════════════════════════════════════════════════════════


def test_수치가_겹쳐도_다른_기간_제품의_추가사실은_정확원문_후보로_보존한다():
    earlier, later = _fragment("881", _EARLIER), _fragment("882", _LATER)
    output, _writer, _reviewer = _full((earlier, later), transform=_rewrite_with_cause, decide=_reject_cause)
    body = _body(output)
    assert _EARLIER in body
    assert _LATER in body  # 2025년·제품 을·신규 계약 3건이 모두 남는다
    assert _UNSUPPORTED_CAUSE.strip() not in body  # 근거 없는 인과는 여전히 막힌다
    reason, trail = _decision_trail(output, "882")
    assert reason == "본문반영" and "review_removed" in trail


def test_같은_반례에서_원문_후보도_검수에서_떨어지면_표로_우회하지_않는다():
    earlier, later = _fragment("881", _EARLIER), _fragment("882", _LATER)
    output, _writer, _reviewer = _full(
        (earlier, later), transform=_rewrite_with_cause,
        decide=lambda text, entry: "거짓" if "제품 을" in text else "참",
    )
    assert "제품 을" not in _body(output)
    assert all("제품 을" not in row for row in _news_rows(output))  # 검수 탈락 차단은 그대로


# ══════════════════════════════════════════════════════════
# ② 진짜 중복 — 실제로 남은 문장이 후보 원문 전부를 글자 그대로 담을 때만 정리
# ══════════════════════════════════════════════════════════


def test_남은_보도가_원문_문장을_모두_담으면_짧은_원문_후보만_정리한다():
    longer = _fragment("881", f"{_SHARED} {_EXTRA}")
    shorter = _fragment("882", _SHARED)
    output, _writer, _reviewer = _full(
        (longer, shorter), transform=lambda text: text + _UNSUPPORTED_CAUSE, decide=_reject_cause,
    )
    body = _body(output)
    assert body.count(_SHARED) == 1
    assert _EXTRA in body
    reason, trail = _decision_trail(output, "882")
    assert "duplicate_claim" in trail and reason != "본문반영"


def test_원문에_새_문장이_하나라도_있으면_둘_다_남긴다():
    first = _fragment("881", f"{_SHARED} {_EXTRA}")
    second = _fragment("882", f"{_SHARED} {_OTHER_EXTRA}")
    output, _writer, _reviewer = _full(
        (first, second), transform=lambda text: text + _UNSUPPORTED_CAUSE, decide=_reject_cause,
    )
    body = _body(output)
    assert _EXTRA in body and _OTHER_EXTRA in body


def test_미승인_문장은_중복_정리의_기준이_되지_않는다():
    longer = _fragment("881", f"{_SHARED} {_EXTRA}")
    shorter = _fragment("882", _SHARED)
    output, _writer, _reviewer = _full(
        (longer, shorter), transform=lambda text: text + _UNSUPPORTED_CAUSE,
        decide=lambda text, entry: "거짓" if _UNSUPPORTED_CAUSE.strip() in text or _EXTRA in text else "참",
    )
    body = _body(output)
    assert _EXTRA not in body
    assert body.count(_SHARED) == 1  # 긴 쪽이 떨어졌으므로 짧은 원문이 남는다


# ══════════════════════════════════════════════════════════
# ③ 사건 키 — 같은 사건의 미사용 조각은 본문 반복 대신 보도표에 남는다(검수 후보 아님)
# ══════════════════════════════════════════════════════════


def _draft():
    return ComposedReport((ComposedSection("business_model", (
        ComposedSentence("가나다전자는 설비를 판매한다.", (), "확인",
                         planned_claim_slot="business_model:revenue_model"),
    )),))


def test_다른_기사의_같은_사건은_본문_후보를_한번만_만들고_나머지는_보도표에_남긴다():
    first = _fragment("881", _SHARED, event_key="같은 보도")
    second = _fragment("882", f"{_SHARED} {_OTHER_EXTRA}", event_key="같은 보도")
    _report, added = supplement_news_candidates(_draft(), (first, second))
    assert added == ("881",)
    output, _writer, _reviewer = _full((first, second))
    assert _SHARED in _body(output)
    assert any(_OTHER_EXTRA in row for row in _news_rows(output))  # 추가 사실은 표에 남는다


# ══════════════════════════════════════════════════════════
# ④ 보도표 — 기사 전체가 아니라 (기사, 정확 원문) 단위로 한 번만 싣는다
# ══════════════════════════════════════════════════════════


def _same_article(fragment, template):
    from dataclasses import replace
    return replace(fragment, source_url=template.source_url, document_identity=template.document_identity,
                   source_document_id=template.source_document_id)


def _rows_by_section(result):
    return {section.section_id: [row.cells[-1] for row in section.news_rows]
            for section in result.report.sections if section.news_rows}


def test_같은_기사의_다른_장_사실은_앞_장_표가_기사를_선점해도_뒤_장_표에_남는다():
    from dataclasses import replace
    from src.features.composer.news_block import augment_news_blocks
    from src.features.composer.constants import SECTION_IDS
    product = replace(_fragment("881", _SHARED), supported_claim_slots=("portfolio:revenue_link",))
    company = _same_article(replace(_fragment("882", _EXTRA),
                                    supported_claim_slots=("past_changes:change_context",)), product)
    report = ComposedReport(tuple(ComposedSection(section_id, ()) for section_id in SECTION_IDS))
    ownership = {section_id: frozenset() for section_id in SECTION_IDS}
    ownership["portfolio"] = frozenset({"881"})
    ownership["past_changes"] = frozenset({"882"})
    result = augment_news_blocks(report, (product, company), allowed_fragment_ids_by_section=ownership)
    assert _rows_by_section(result) == {"portfolio": [_SHARED], "past_changes": [_EXTRA]}
    assert dict(result.blocked_counts_by_reason) == {}


def test_같은_원문_사실이_두_장_소유여도_보도표에는_한_번만_싣는다():
    from dataclasses import replace
    from src.features.composer.news_block import BLOCKED_DUPLICATE_ARTICLE, augment_news_blocks
    from src.features.composer.constants import SECTION_IDS
    shared = replace(_fragment("881", _SHARED),
                     supported_claim_slots=("portfolio:revenue_link", "past_changes:change_context"))
    report = ComposedReport(tuple(ComposedSection(section_id, ()) for section_id in SECTION_IDS))
    ownership = {section_id: frozenset() for section_id in SECTION_IDS}
    ownership["portfolio"] = ownership["past_changes"] = frozenset({"881"})
    result = augment_news_blocks(report, (shared,), allowed_fragment_ids_by_section=ownership)
    assert _rows_by_section(result) == {"portfolio": [_SHARED]}
    assert dict(result.blocked_counts_by_reason) == {BLOCKED_DUPLICATE_ARTICLE: 1}


# ══════════════════════════════════════════════════════════
# ⑤ 작가가 스스로 단 출처 머리말 — 날짜·발행처가 정확히 같을 때만 규격으로 바꾼다
# ══════════════════════════════════════════════════════════

_CLAIM = "가나다전자는 북미 지역에 설비 관제 서비스를 출시했다."


def _attributed(text, fragment):
    draft = ComposedReport((ComposedSection("business_model", (
        ComposedSentence(text, (fragment.fragment_id,), "확인",
                         planned_claim_slot="business_model:revenue_model"),
    )),))
    report, _added = supplement_news_candidates(draft, (fragment,))
    return report.sections[0].sentences[0].text


def test_같은_날짜의_작가_머리말은_규격_머리말로_한_번만_남긴다():
    from src.features.composer.news_usage import attribution_prefix
    fragment = _fragment("881", _CLAIM)
    for lead in ("2026년 9월 1일 보도에 따르면, ", "2026.09.01 보도에 따르면 ",
                 f"2026년 9월 1일 {fragment.source_publisher} 보도에 따르면, "):
        assert _attributed(lead + _CLAIM, fragment) == attribution_prefix(fragment) + _CLAIM


def test_날짜나_발행처가_다른_머리말은_고치지_않고_검수에_맡긴다():
    from src.features.composer.news_usage import attribution_prefix
    fragment = _fragment("881", _CLAIM)
    for lead in ("2026년 8월 30일 보도에 따르면, ", "다른경제 보도에 따르면, "):
        assert _attributed(lead + _CLAIM, fragment) == attribution_prefix(fragment) + lead + _CLAIM


# ══════════════════════════════════════════════════════════
# ⑥ 발행일이 다른 기사의 같은 원문은 다른 사실이다 (상대 시점 보존)
# ══════════════════════════════════════════════════════════


def _fragment_on_date(fragment_id, text, published_on):
    from dataclasses import replace as _r
    return _r(_fragment(fragment_id, text), document_date=published_on)


def test_다른_발행일의_같은_원문은_중복으로_지우지_않는다():
    relative = "가나다전자는 지난해 매출 100억원을 달성했다."
    earlier = _fragment_on_date("881", relative, "2025-06-01")
    later = _fragment_on_date("882", relative, "2026-06-01")
    output, _writer, _reviewer = _full((earlier, later))
    body = _body(output)
    assert body.count(relative) == 2


def test_같은_발행일의_같은_원문은_여전히_중복_정리한다():
    shared = "가나다전자는 지난해 매출 100억원을 달성했다."
    first = _fragment("881", f"{shared} {_EXTRA}")
    second = _fragment("882", shared)
    output, _writer, _reviewer = _full(
        (first, second), transform=lambda text: text + _UNSUPPORTED_CAUSE, decide=_reject_cause,
    )
    body = _body(output)
    assert body.count(shared) == 1
    reason, trail = _decision_trail(output, "882")
    assert "duplicate_claim" in trail
