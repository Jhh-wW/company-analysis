"""뉴스 인용의 장 소유 — 4장 보조 칸 개방·주장 역할 대칭·원문 범위 보존 회귀.

★ 4차 실측: 회사 전체의 지난해 매출·성장률 보도가 2장(수익 모델) 칸에 들어가 같은
  수치가 2장과 4장에 반복됐다. 원인은 둘이다 — ① 뉴스 허용 칸을 수집기 필수 칸과
  교집합해 4장 보조 칸(change_context 등)을 모델이 고를 수 없었다. ② 칸 이름만 주고
  장의 의미를 알려 주지 않았다. 여기서는 그 경계와, 매출·숫자·% 단어만으로 과금·
  구성 설명을 잘못 옮기지 않는 반대쪽 경계를 함께 잠근다.
"""

from __future__ import annotations

import json

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.claim_role import is_period_financial_result, sentence_ranges
from src.features.news_intake.grounded import ALLOWED_SLOTS, build_grounded_prompt
from src.features.news_intake.tests.test_collection import AS_OF, COMPANY, analyzer, collect, item
from src.shared.report_claim_policy import claim_slots_for
from src.shared.report_evidence.policy import injected_slots_for
from src.shared.report_evidence.source_kind_policy import supplementary_slots_for_source_kind

_IDENTITY = "가나다전자는 기업용 산업설비 제조 사업을 운영한다."
_PERIOD_RESULT = "가나다전자는 지난해 매출 120억원을 기록해 전년 대비 20% 늘었다."
_PRICING = "가나다전자는 기업 고객에게 좌석당 월 2만원을 받고, 거래금액의 3%를 수수료로 받는다."
_REGIONAL_MIX = "가나다전자의 2025년 매출 중 북미 비중은 60%, 국내는 40%다."
_SUBSCRIPTION = "가나다전자는 설비 관리 서비스 월 구독료로 9,900원을 받는다."
_TARGET = "가나다전자는 내년 매출 200억원을 목표로 한다."
_PRODUCT_RESULT = "가나다전자가 북미에서 운영하는 설비 관제 서비스 오르빗은 출시 3개월 만에 월매출 10억원을 넘었다."


def _row(article, excerpts):
    return {
        "id": article["id"], "same_company": True, "material": True,
        "entity_evidence": article["body"], "source_type": "news_report", "excerpts": excerpts,
    }


def _excerpt(text, *, section="business_model", slot="business_model:revenue_model",
             kind="reported_fact", temporal="completed", topic="business", event_key="실적 보도"):
    return {"text": text, "section_id": section, "claim_slot": slot, "claim_kind": kind,
            "temporal_status": temporal, "topic": topic, "event_key": event_key,
            "event_on": "", "time_evidence": "", "subject": "", "subject_evidence": ""}


def _collect(body, *excerpts):
    def transform(rows, payload):
        return [_row(article, list(excerpts)) for article in payload["articles"]]

    return collect([item()], fetch=lambda url: body, analyze=analyzer(transform))


# ══════════════════════════════════════════════════════════
# ① 허용 칸 — shared가 이미 허용한 4장 보조 칸은 열고, 공식 주입 칸은 닫는다
# ══════════════════════════════════════════════════════════


def test_4장_보조_산문_칸을_고를_수_있다():
    assert "past_changes:change_context" in ALLOWED_SLOTS["past_changes"]
    assert "past_changes:cumulative_change" in ALLOWED_SLOTS["past_changes"]
    assert "past_changes:completed_execution" in ALLOWED_SLOTS["past_changes"]


def test_공식_구조화_주입_칸은_뉴스가_고를_수_없다():
    for section, slots in ALLOWED_SLOTS.items():
        assert not set(slots) & set(injected_slots_for(section)), section
    assert "past_changes:historical_performance" not in ALLOWED_SLOTS["past_changes"]


def test_허용_칸은_정본_주장_범주와_뉴스_보조_목록의_교집합_안에만_있다():
    news_slots = supplementary_slots_for_source_kind(c.SOURCE_KIND_NEWS)
    for section, slots in ALLOWED_SLOTS.items():
        assert slots, section
        assert set(slots) <= news_slots & set(claim_slots_for(section)), section
    assert "competitive_position" not in ALLOWED_SLOTS
    # 뉴스 산문이 공개되지 않는 1장은 기존 수집 칸만 둔다(몫을 헛되이 쓰지 않게).
    assert ALLOWED_SLOTS["identity"] == ("identity:business_definition",)


def test_프롬프트가_장별_의미와_다중_사실_분리를_알린다():
    prompt = build_grounded_prompt(COMPANY, [], AS_OF)
    payload = json.loads(prompt.split("자료 시작:\n", 1)[1])
    assert payload["allowed_slots"] == {key: list(value) for key, value in ALLOWED_SLOTS.items()}
    for section, guide in c.GROUNDED_SECTION_GUIDE:
        assert f"{section}={guide}" in prompt
    assert "기간 실적은 이 장이 아닙니다" in prompt
    assert "별도 범위로 나누세요" in prompt


# ══════════════════════════════════════════════════════════
# ② 역할 판정 — 참·거짓 대칭 (단어가 아니라 역할)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("sentence", [
    _PERIOD_RESULT,
    "가나다전자의 2025년 영업손실은 30억원으로 전년보다 줄었다.",
    "가나다전자는 올해 상반기 매출 1조2000억원을 기록했다.",
])
def test_기간_실적은_참(sentence):
    assert is_period_financial_result(sentence)


@pytest.mark.parametrize("sentence", [
    _PRICING, _REGIONAL_MIX, _SUBSCRIPTION, _TARGET,
    "가나다전자는 건당 500원의 중개 수수료를 받는다.",
    "가나다전자의 매출은 설비 판매와 유지보수 용역으로 구성된다.",
    "가나다전자는 매출 대부분을 대기업 고객에게서 얻는다.",
    "가나다전자는 설비 관제 서비스를 운영한다.",
])
def test_과금_구성_고객_계획은_거짓(sentence):
    assert not is_period_financial_result(sentence)


def test_문장_위치는_원문_글자를_바꾸지_않는다():
    text = "가나다전자는 매출 1.5억원을 기록했다. 이 회사는 “좌석당 요금. 월 2만원”을 받는다.\n다음 줄이다"
    ranges = sentence_ranges(text)
    assert [text[start:end] for start, end in ranges] == [
        "가나다전자는 매출 1.5억원을 기록했다.",
        "이 회사는 “좌석당 요금. 월 2만원”을 받는다.",
        "다음 줄이다",
    ]


# ══════════════════════════════════════════════════════════
# ③ 실제 본문 검증 경로 — 칸 이동·분할·보존
# ══════════════════════════════════════════════════════════


def test_2장에_배정된_회사_기간_실적은_4장_보조_칸으로_옮긴다():
    body = f"{_IDENTITY} {_PERIOD_RESULT}"
    result = _collect(body, _excerpt(_PERIOD_RESULT))
    (fragment,) = result.fragments
    assert fragment.section_ids == ("past_changes",)
    assert fragment.supported_claim_slots == (c.ROLE_PERFORMANCE_TARGET_SLOT,)
    assert fragment.text == _PERIOD_RESULT
    assert body[fragment.span_start:fragment.span_end] == fragment.text
    assert result.diagnostics["주장역할조정"] == {c.ROLE_DIAGNOSTIC_REROUTED: 1}


@pytest.mark.parametrize("text", [_PRICING, _REGIONAL_MIX, _SUBSCRIPTION])
def test_과금과_구성_설명은_2장에_그대로_둔다(text):
    result = _collect(f"{_IDENTITY} {text}", _excerpt(text))
    (fragment,) = result.fragments
    assert fragment.section_ids == ("business_model",)
    assert fragment.supported_claim_slots == ("business_model:revenue_model",)
    assert result.diagnostics["주장역할조정"] == {}


def test_계획_목표는_과거_실적으로_옮기지_않는다():
    result = _collect(f"{_IDENTITY} {_TARGET}", _excerpt(_TARGET, kind="company_plan", temporal="planned"))
    (fragment,) = result.fragments
    assert fragment.section_ids == ("business_model",)
    assert fragment.temporal_status == "planned"


def test_뉴스가_공식_실적_칸을_주장하면_받지_않는다():
    result = _collect(f"{_IDENTITY} {_PERIOD_RESULT}", _excerpt(
        _PERIOD_RESULT, section="past_changes", slot="past_changes:historical_performance"))
    assert not result.fragments
    assert result.diagnostics["제외"]["grounded_invalid_slot"] == 1


def test_3장_제품_성과는_건드리지_않는다():
    result = _collect(f"{_IDENTITY} {_PRODUCT_RESULT}", _excerpt(
        _PRODUCT_RESULT, section="portfolio", slot="portfolio:revenue_link", topic="products"))
    (fragment,) = result.fragments
    assert fragment.section_ids == ("portfolio",)


def test_한_범위의_서로_다른_역할은_연속_원문대로_나누고_예산은_한_몫으로_센다():
    mixed = f"가나다전자는 지난해 매출 120억원을 기록했다. {_PRICING}"
    other = "가나다전자는 부산에 두 번째 설비 조립 공장을 세웠다."
    body = f"{_IDENTITY} {mixed} {other}"
    result = _collect(body, _excerpt(mixed), _excerpt(
        other, section="operations_partners", slot="operations_partners:operating_role",
        topic="operations", event_key="공장 설립"))
    by_text = {fragment.text: fragment for fragment in result.fragments}
    assert set(by_text) == {"가나다전자는 지난해 매출 120억원을 기록했다.", _PRICING, other}
    assert by_text["가나다전자는 지난해 매출 120억원을 기록했다."].section_ids == ("past_changes",)
    assert by_text[_PRICING].supported_claim_slots == ("business_model:revenue_model",)
    for fragment in result.fragments:
        assert body[fragment.span_start:fragment.span_end] == fragment.text
    # 같은 기사의 두 번째 사실(공장)이 분할 때문에 기사당 예산에서 밀리지 않는다.
    assert "article_excerpt_budget" not in result.diagnostics["제외"]
    assert result.diagnostics["주장역할조정"] == {c.ROLE_DIAGNOSTIC_SPLIT: 1}


def test_주어가_없는_과금_문장이_섞이면_나누지_않고_2장에_둔다():
    mixed = "가나다전자는 지난해 매출 120억원을 기록했다. 기업 고객은 좌석당 월 2만원을 낸다."
    result = _collect(f"{_IDENTITY} {mixed}", _excerpt(mixed))
    (fragment,) = result.fragments
    assert fragment.text == mixed
    assert fragment.section_ids == ("business_model",)


def test_주어가_없는_제품_성과가_섞이면_범위_전체를_4장_보조_칸으로_보존한다():
    mixed = "가나다전자는 지난해 매출 120억원을 기록했다. 올해 들어 북미 서비스 오르빗의 월 매출이 10억원을 넘었다."
    result = _collect(f"{_IDENTITY} {mixed}", _excerpt(mixed))
    (fragment,) = result.fragments
    assert fragment.text == mixed  # 두 사실 모두 한 원문 범위로 남는다
    assert fragment.section_ids == ("past_changes",)


# ══════════════════════════════════════════════════════════
# ④ 상대 시점·역참조 의존이 있으면 나누지 않는다
# ══════════════════════════════════════════════════════════


def test_그해_역참조가_있는_과금_문장은_나누지_않고_2장에_둔다():
    mixed = "가나다전자는 2024년 매출 120억원을 기록했다. 가나다전자는 그해 기업 고객을 대상으로 월 구독료 2만원을 받았다."
    result = _collect(f"{_IDENTITY} {mixed}", _excerpt(mixed))
    (fragment,) = result.fragments
    assert fragment.text == mixed
    assert fragment.section_ids == ("business_model",)


def test_해당_서비스_역참조가_있으면_나누지_않는다():
    mixed = "가나다전자는 지난해 매출 120억원을 기록했다. 가나다전자는 해당 서비스의 월 구독료를 2만원으로 책정했다."
    result = _collect(f"{_IDENTITY} {mixed}", _excerpt(mixed))
    (fragment,) = result.fragments
    assert fragment.text == mixed
    assert fragment.section_ids == ("business_model",)


def test_같은_해_역참조가_있으면_나누지_않고_4장으로_보존한다():
    mixed = "가나다전자는 지난해 매출 120억원을 기록했다. 가나다전자는 같은 해 영업이익 20억원을 거뒀다."
    result = _collect(f"{_IDENTITY} {mixed}", _excerpt(mixed))
    (fragment,) = result.fragments
    assert fragment.text == mixed
    assert fragment.section_ids == ("past_changes",)


def test_역참조_없는_독립_범위는_여전히_나눈다():
    mixed = f"가나다전자는 지난해 매출 120억원을 기록했다. {_PRICING}"
    result = _collect(f"{_IDENTITY} {mixed}", _excerpt(mixed))
    by_text = {fragment.text: fragment for fragment in result.fragments}
    assert "가나다전자는 지난해 매출 120억원을 기록했다." in by_text
    assert _PRICING in by_text
