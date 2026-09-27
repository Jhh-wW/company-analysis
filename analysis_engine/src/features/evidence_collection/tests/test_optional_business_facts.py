"""공식 공시의 선택 사업 사실은 원문 결속으로만 운반한다."""

import hashlib

from features.evidence_collection import constants as c, relevance
from features.evidence_collection.tests.test_streaming_collection import _collect


def _slots(text: str, source_kind: str = c.SOURCE_KIND_BUSINESS_REPORT) -> set[str]:
    allowed = frozenset(c.SOURCE_KIND_CANDIDATE_SLOT_SCOPE[source_kind])
    scores, _ = relevance.score_fragment_slots_with_signal(text, allowed_slot_ids=allowed)
    return {score.slot_id for score in scores}


def test_매출유형_복합표현과_판매경로는_직접근거일때만_선택한다() -> None:
    assert "business_model:revenue_model" in _slots(
        "매출 유형은 제품, 상품, 임대, 공임으로 구성됩니다."
    )
    assert "business_model:sales_channel" in _slots(
        "회사는 제품을 직판 및 대리점 경로로 판매합니다."
    )
    assert "business_model:sales_channel" not in _slots(
        "유통주식수는 주식의 총수에서 자기주식을 제외하여 계산합니다."
    )
    assert "business_model:sales_channel" not in _slots(
        "판매경로라는 제목만 있고 실제 경로는 아직 공시되지 않았습니다."
    )
    assert "business_model:revenue_model" not in _slots(
        "매출 유형별 회계정책은 제품과 상품의 수익을 인식하고 측정합니다."
    )


def test_판매경로_표는_머리글과_소비자도달_행이_같은표에_있을때만_선택한다() -> None:
    table = (
        "부문/제품 | 판매경로 | 판매경로 | 소비 ; "
        "가상제품 | 직거래매장 | 도/소매점 | 최종 소비자"
    )
    scores, _ = relevance.score_fragment_slots_with_signal(
        table, allowed_slot_ids=frozenset(c.SOURCE_KIND_CANDIDATE_SLOT_SCOPE[c.SOURCE_KIND_BUSINESS_REPORT])
    )
    sales = next(score for score in scores if score.slot_id == "business_model:sales_channel")
    assert "direct_pattern:sales_channel_table" in sales.reason_codes
    assert scores[0].slot_id == "business_model:sales_channel"
    assert "business_model:sales_channel" in _slots(
        "(2) 해외 부문/제품 | 판매경로 | 판매경로 | 소비 ; "
        "가상제품 | 현지 판매법인 | 판매법인 | 최종 소비자"
    )
    for invalid in (
        "부문/제품 | 판매경로 | 소비",
        "부문/제품 | 판매경로 | 소비 ; 가상제품 | 미정 | 최종 소비자",
        "판매경로라는 제목만 있습니다. 별개 문장에서 고객을 소개합니다.",
        "부문/제품 | 판매경로 | 소비 ; 가상제품 | 회계정책상 수익인식 | 최종 소비자",
        "부문/제품 | 판매경로 | 소비 ; 가상제품 | 직거래매장 | 미정",
    ):
        assert "business_model:sales_channel" not in _slots(invalid)


def test_원재료_계약은_조달행위가_있을때만_선택한다() -> None:
    assert "operations_partners:supply_relation" in _slots(
        "회사는 원재료 확보를 위해 공급업체와 구매 계약을 맺고 연단위로 갱신합니다."
    )
    assert "operations_partners:supply_relation" not in _slots(
        "원재료는 저가법으로 평가하고 재고자산평가손실을 인식합니다."
    )
    assert "operations_partners:supply_relation" not in _slots(
        "일반적인 용역 계약의 수익인식 기준을 회계정책에서 설명합니다."
    )
    assert "operations_partners:supply_relation" not in _slots(
        "원재료 재고자산은 저가평가를 검토한다. 거래처에는 제품을 공급한다."
    )
    assert "operations_partners:supply_relation" not in _slots(
        "원재료 재고자산은 저가법으로 평가한다 ; 거래처에는 제품을 공급한다"
    )
    assert "operations_partners:supply_relation" not in _slots(
        "원재료 재고자산은 저가법으로 평가하고 거래처에는 제품을 공급한다."
    )


def test_공식설비절의_미래투자_실행만_계획후보에_추가한다() -> None:
    allowed = frozenset(c.SOURCE_KIND_CANDIDATE_SLOT_SCOPE[c.SOURCE_KIND_BUSINESS_REPORT])

    def scored(text: str, heading: str) -> tuple[str, ...]:
        scores, _ = relevance.score_fragment_slots_with_signal(
            text, heading, allowed_slot_ids=allowed,
        )
        return tuple(score.slot_id for score in scores)

    plan = "현재 진행 중인 투자가 있고 향후 투자 계획을 수행할 예정입니다."
    assert "future_strategy:stated_plan" in scored(plan, "생산설비의 현황")
    for text, heading in (
        (plan, "위원회 운영 현황"),
        ("위원회는 현재 투자 방향을 논의하고 운영합니다.", "생산설비의 현황"),
        ("향후 투자 계획을 검토 중이며 수행할 예정입니다.", "생산설비의 현황"),
        ("향후 타사의 투자 계획을 수행할 예정입니다.", "생산설비의 현황"),
        ("향후 회계기준서 도입 투자 계획을 수행할 예정입니다.", "생산설비의 현황"),
        ("향후 투자 계획을 수행하지 않을 예정입니다.", "생산설비의 현황"),
        ("‘향후 투자 계획을 수행할 예정’이라고 인용합니다.", "생산설비의 현황"),
    ):
        assert "future_strategy:stated_plan" not in scored(text, heading)


def test_반기분기_소유범위와_필수조회범위는_넓어지지_않는다() -> None:
    text = "회사는 원재료 확보를 위해 공급업체와 구매 계약을 맺고 연단위로 갱신합니다."
    assert "operations_partners:supply_relation" not in _slots(
        text, c.SOURCE_KIND_SEMIANNUAL_REPORT
    )
    assert "operations_partners:supply_relation" not in _slots(
        text, c.SOURCE_KIND_QUARTERLY_REPORT
    )
    for source_kind in c.SOURCE_KIND_SLOT_SCOPE:
        assert "business_model:sales_channel" not in c.SOURCE_KIND_SLOT_SCOPE[source_kind]
        assert "operations_partners:supply_relation" not in c.SOURCE_KIND_SLOT_SCOPE[source_kind]


def test_원문위치_해시_기존상한_필수보관몫을_보존한다() -> None:
    body = "\n\n".join((
        "당사는 정밀부품을 생산하는 주식회사이며 법인입니다.",
        "매출 유형은 제품, 상품, 임대, 공임으로 구성됩니다.",
        "회사는 원재료 확보를 위해 공급업체와 구매 계약을 맺고 연단위로 갱신합니다.",
        "회사는 제품을 직판 및 대리점 경로로 판매합니다.",
    ))
    harvest = _collect(body)
    covered = {slot for fragment in harvest.fragments for slot in fragment.covered_slot_ids}
    assert "business_model:revenue_model" in covered
    assert "business_model:sales_channel" in covered
    assert "operations_partners:supply_relation" in covered
    assert "identity:corporate_identity" in covered
    assert len(harvest.fragments) <= c.MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT
    assert sum(len(fragment.text) for fragment in harvest.fragments) <= c.MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT
    for fragment in harvest.fragments:
        start, end = map(int, fragment.location.split("-"))
        assert body[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
        assert any(
            document.document_id == fragment.document_id
            and document.content_sha256 == hashlib.sha256(body.encode()).hexdigest()
            and any(span.start <= start and end <= span.end for span in document.usable_ranges)
            for document in harvest.documents
        )

    allowed_count = len(c.SOURCE_KIND_CANDIDATE_SLOT_SCOPE[c.SOURCE_KIND_BUSINESS_REPORT])
    per_slot_count = c.MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT // allowed_count
    per_slot_chars = c.MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT // allowed_count
    lane_total = c.RETAINED_TOP_PER_SLOT + c.RETAINED_RECENT_PER_SLOT + c.RETAINED_CHANGE_PER_SLOT
    assert per_slot_count >= lane_total
    for lane_count in (c.RETAINED_TOP_PER_SLOT, c.RETAINED_RECENT_PER_SLOT, c.RETAINED_CHANGE_PER_SLOT):
        assert per_slot_chars * lane_count // lane_total >= lane_count * c.MAX_CANDIDATE_WINDOW_CHARS
