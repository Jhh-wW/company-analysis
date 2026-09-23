"""실제 작성·검수·공개 결속을 거친 보도표 수와 이동 안내 회귀."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from src.core import news_intake_switch
from src.features.composer.news_block import NEWS_BLOCK_HEADERS, NEWS_BLOCK_STEP, news_block_steps
from src.features.composer.pipeline import run_v2
from src.features.composer.port import CollectedFragment
from src.features.composer.tests.test_pipeline import (
    _FakeReviewer, _FakeWriter, _raw_fragments, section_id_in_prompt,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.provenance.sources import has_valid_provenance_seal
from src.shared.report_evidence.transport_kind import TYPED_TRANSPORT_KIND_PREFIX
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode, SOURCE_KIND_NEWS
from src.shared.report_generation.models import exact_text_sha256
from src.shared.report_quality.source_identity import document_identity_from_parts


_NEWS_A = "가나다전자는 고객 지원센터를 개설해 장비 유지보수 접수 체계를 정비했다."
_NEWS_B = "가나다전자는 해외 고객을 위해 통역 상담 인력을 배치했다."
_MOVED_CLAIM = "가나다전자는 부품 조달과 장비 조립을 사내에서 수행하는 검사 장비 기업이다."
_PAST_SLOT = "past_changes:completed_execution"


@pytest.fixture(autouse=True)
def _news_enabled(monkeypatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def _official_fragments():
    slots = tuple(slot for values in CLAIM_SLOTS_BY_SECTION.values() for slot in values)
    return tuple(CollectedFragment(
        fragment_id=str(number), kind=raw["종류"], text=raw["원문"],
        source_url=raw["출처"], document_title="합성 공식 자료",
        document_date=raw["문서일"],
        document_identity=document_identity_from_parts(url=raw["출처"]),
        document_content_sha256=exact_text_sha256(raw["원문"]),
        supported_claim_slots=slots,
    ) for number, raw in _raw_fragments().items())


def _news_fragments(*, extra=False):
    url = "https://media.example/news/customer-support"
    texts = (_NEWS_A, _NEWS_B) if extra else (_NEWS_A,)
    document_hash = exact_text_sha256(" ".join(texts))
    return tuple(CollectedFragment(
        fragment_id=str(41 + index), kind="typed-evidence-v1:news",
        text=text, source_url=url, document_title="가나다전자 고객 지원체계 정비",
        location=f"기사 본문 {index + 1}문단", document_date="2026-09-08",
        document_identity=document_identity_from_parts(document_id=url, host="media.example", url=url),
        document_content_sha256=document_hash, counts_toward_document_floor=False,
        supported_claim_slots=(_PAST_SLOT,), formal_source_kind=SOURCE_KIND_NEWS,
        source_document_id=url, source_publisher="가상경제", source_collected_on="2026-09-23",
        news_claim_kind="reported_fact", news_temporal_status="completed",
        news_grounded=True, news_source_category="news_report",
        news_event_key="고객 지원체계 정비",
    ) for index, text in enumerate(texts))


class _Writer(_FakeWriter):
    def __init__(self, *, moved=False, omit_news_slot=False):
        super().__init__()
        self.moved = moved
        self.omit_news_slot = omit_news_slot

    def __call__(self, prompt):
        payload = json.loads(super().__call__(prompt))
        section = section_id_in_prompt(prompt)
        if section == "past_changes" and not self.moved:
            payload["문장들"].append({
                "글": _NEWS_A, "인용": ["41"], "등급": "확인", "주장슬롯": _PAST_SLOT,
            })
            if self.omit_news_slot:
                payload["문장들"][-1].pop("주장슬롯")
        if self.moved and section in {"identity", "operations_partners"}:
            claim = {"글": _MOVED_CLAIM, "인용": ["3"], "등급": "확인", "주장슬롯": (
                "identity:business_definition" if section == "identity"
                else "operations_partners:value_chain"
            )}
            if section == "identity":
                payload["문장들"] = [claim]
            else:
                payload["문장들"].append(claim)
                # 동일 근거를 실제로 깊게 다룬 소유 장에 남도록 정상 운영 사실을 묶는다.
                payload["문장들"][0]["인용"] = ["3"]
        return json.dumps(payload, ensure_ascii=False)


def _run(*, extra=False, moved=False, omit_news_slot=False):
    fragments = _official_fragments()
    if moved:
        original = fragments[0]
        text = _MOVED_CLAIM + " 부품 조달 뒤 사내 공장에서 검사 장비를 조립한다."
        url = "https://www.ganada.example/operating-model"
        fragments += (replace(original, fragment_id="3", text=text, source_url=url,
            document_identity=document_identity_from_parts(url=url),
            document_content_sha256=exact_text_sha256(text),
            supported_claim_slots=("identity:business_definition", "operations_partners:value_chain")),)
    else:
        fragments += _news_fragments(extra=extra)
    writer = _Writer(moved=moved, omit_news_slot=omit_news_slot)
    output = run_v2(
        "가나다전자", fragments, None, writer_ask=writer, reviewer_ask=_FakeReviewer(),
        as_of_date="2026-09-23",
    )
    assert writer.section_calls == 9
    assert output.report.fact_records
    assert all(fact.evidence_binding for fact in output.report.fact_records)
    assert all(has_valid_provenance_seal(source) for source in output.report.citations)
    assert not output.quality_observation.safety_problems
    return output


def _section(output, section_id):
    return next(section for section in output.report.sections if section.cell == section_id)


def _news_tables(output):
    return [table for section in output.report.sections for table in section.tables
            if table.headers == list(NEWS_BLOCK_HEADERS)]


def test_본문에_실린_보도는_후보1_출력0으로_진단한다():
    output = _run()
    assert any(_NEWS_A in text for text, _cite in _section(output, "past_changes").prose_lines)
    assert any(_NEWS_A in fact.claim for fact in output.report.fact_records)
    assert not _news_tables(output)
    assert output.news_block_candidate_row_counts_by_section == (("past_changes", 1),)
    assert output.news_block_row_counts_by_section == ()
    step = next(step for step in news_block_steps(output) if step["step"] == NEWS_BLOCK_STEP)
    assert step["후보행수"] == 1 and step["행수"] == 0
    assert step["장별후보행수"] == {"past_changes": 1} and step["장별행수"] == {}


def test_같은_기사의_별도_사실은_본문과_겹친_조각에_딸려_사라지지_않는다():
    output = _run(extra=True)
    section = _section(output, "past_changes")
    assert any(_NEWS_A in text for text, _cite in section.prose_lines)
    assert all(_NEWS_B not in text for text, _cite in section.prose_lines)
    tables = _news_tables(output)
    assert len(tables) == 1
    assert any(_NEWS_B in row[-1] for row in tables[0].rows)
    assert output.news_block_candidate_row_counts_by_section == (("past_changes", 1),)
    assert output.news_block_row_counts_by_section == (("past_changes", 1),)
    assert sum(len(table.rows) for table in tables) == sum(dict(output.news_block_row_counts_by_section).values())
    assert tables[0].source_cites == ["[41]", "[42]"]
    source = next(source for source in output.report.citations if source.number == 42)
    assert source.document_content_sha256 == exact_text_sha256(_NEWS_A + " " + _NEWS_B)


def test_미결속_본문_제외후_동일기사의_정상조각으로_보도표를_다시_만든다():
    output = _run(extra=True, omit_news_slot=True)
    assert all(_NEWS_A not in text for section in output.report.sections for text, _cite in section.prose_lines)
    assert all(_NEWS_A not in fact.claim for fact in output.report.fact_records)
    assert any(entry.get("reason_code") == "public_sentence_fact_unbound" for entry in output.review_diagnostics)
    tables = _news_tables(output)
    assert len(tables) == 1 and tables[0].source_cites == ["[42]"]
    assert any(_NEWS_B in row[-1] for row in tables[0].rows)
    assert all(_NEWS_A not in row[-1] for row in tables[0].rows)
    assert output.news_block_candidate_row_counts_by_section == (("past_changes", 1),)
    assert output.news_block_row_counts_by_section == (("past_changes", 1),)
    assert 41 not in {source.number for source in output.report.citations}


def test_실제_중복제거_후_살아있는_소유장의_번호와_제목을_안내한다():
    output = _run(moved=True)
    owner = _section(output, "operations_partners")
    assert any(_MOVED_CLAIM in text for text, _cite in owner.prose_lines)
    assert any(fact.claim == _MOVED_CLAIM for fact in output.report.fact_records)
    moved = _section(output, "identity")
    assert all(_MOVED_CLAIM not in text for text, _cite in moved.prose_lines)
    visible = " ".join(text for text, _cite in moved.prose_lines)
    visible += " ".join(getattr(moved, "guidance_lines", ()))
    assert "7장 «사업 운영과 파트너 구조»" in visible
    assert "아래" not in visible


@pytest.mark.parametrize("route", ("fallback", "supplement"))
def test_FULL_전환과_보충에서도_최종_보도표와_후보진단을_보존한다(route):
    from src.features.composer.tests.test_section_public_manifest import (
        _BoundGroupedReviewer, _CompletePacketWriter, _packets,
    )

    base = _packets()
    packets = replace(base, packets=tuple(
        replace(packet, fragments=packet.fragments + _news_fragments(extra=True))
        if packet.section_id == "past_changes" else packet for packet in base.packets
    ))
    delegate = _CompletePacketWriter()
    prompts = []
    identity_payload = None

    def writer(prompt):
        nonlocal identity_payload
        prompts.append(prompt)
        section_id = section_id_in_prompt(prompt)
        if delegate.calls < 9:
            payload = json.loads(delegate(prompt))
            if section_id == "identity":
                identity_payload = json.dumps(payload, ensure_ascii=False)
                if route == "supplement":
                    payload["문장들"] = payload["문장들"][:1]
        else:
            assert route == "supplement" and section_id == "identity" and identity_payload
            payload = json.loads(identity_payload)
        if route == "fallback":
            payload["문장들"] = payload["문장들"][:2]
        if section_id == "past_changes":
            payload["문장들"].append({"글": _NEWS_A, "인용": ["41"], "등급": "확인", "주장슬롯": _PAST_SLOT})
        return json.dumps(payload, ensure_ascii=False)

    output = run_v2(
        "가나다전자", (), None, writer_ask=writer, reviewer_ask=_BoundGroupedReviewer(),
        release_mode=ReleaseMode.FULL, section_evidence_packets=packets,
        company_id="00123456", build_identity_sha256="b" * 64,
        evidence_available_fallback=route == "fallback", as_of_date="2026-09-23",
    )
    if route == "fallback":
        assert output.downgraded_from_release_mode == ReleaseMode.FULL.value
        assert len(prompts) == 9
    else:
        assert len(prompts) == 10
        assert output.report.generation_evidence is not None
        assert len(output.report.generation_evidence.validation_receipts) == 2
    assert any(_NEWS_A in fact.claim and fact.evidence_binding for fact in output.report.fact_records)
    tables = _news_tables(output)
    assert len(tables) == 1 and any(_NEWS_B in row[-1] for row in tables[0].rows)
    assert output.news_block_row_counts_by_section == (("past_changes", 1),)
    assert output.news_block_candidate_row_counts_by_section == (("past_changes", 1),)


_POLICY_PUBLICATION_PLAN = "가나다전자는 이달 중 청소년 보호 정책의 세부 내용을 공개할 예정이라고 밝혔다."
_POLICY_IMPLEMENTATION_PLAN = "가나다전자는 보호자 동의를 얻은 경우에만 청소년 보호 정책을 내년 초부터 적용할 계획이다."
_POLICY_PUBLICATION_DONE = "가나다전자는 청소년 보호 정책의 세부 내용을 공개했다."


def _policy_fragment(text, *, planned):
    number = "51" if planned else "52"
    url = f"https://media.example/news/policy/{number}"
    return replace(
        _news_fragments()[0], fragment_id=number, text=text,
        kind=TYPED_TRANSPORT_KIND_PREFIX + exact_text_sha256(text),
        source_url=url, source_document_id=url,
        document_identity=document_identity_from_parts(document_id=url, host="media.example", url=url),
        document_title="가나다전자 청소년 보호 정책", document_content_sha256=exact_text_sha256(text),
        document_date="2026-09-02" if planned else "2026-09-08",
        news_claim_kind="company_plan" if planned else "reported_fact",
        news_temporal_status="planned" if planned else "completed",
        news_source_category="official_release", news_event_key="청소년 보호 정책",
        supported_claim_slots=("future_strategy:stated_plan",) if planned else (_PAST_SLOT,),
    )


def _run_policy(*, implementation=False, completion=True, omit_completion_slot=False):
    plan = _POLICY_IMPLEMENTATION_PLAN if implementation else _POLICY_PUBLICATION_PLAN
    plan_fragment = _policy_fragment(plan, planned=True)
    done_fragment = _policy_fragment(_POLICY_PUBLICATION_DONE, planned=False)

    class PolicyWriter(_FakeWriter):
        def __call__(self, prompt):
            payload = json.loads(super().__call__(prompt))
            section = section_id_in_prompt(prompt)
            if section == "future_strategy":
                payload["문장들"] = [{"글": plan, "인용": ["51"], "등급": "확인", "주장슬롯": "future_strategy:stated_plan"}]
            if section == "past_changes" and completion:
                row = {"글": _POLICY_PUBLICATION_DONE, "인용": ["52"], "등급": "확인", "주장슬롯": _PAST_SLOT}
                if omit_completion_slot:
                    row.pop("주장슬롯")
                payload["문장들"].append(row)
            return json.dumps(payload, ensure_ascii=False)

    class PolicyReviewer(_FakeReviewer):
        def __call__(self, prompt):
            result = json.loads(super().__call__(prompt))
            items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
            matching = {item.number for item in items if item.citations == ("51",) and plan in item.text}
            for verdict in result["판정"]:
                if verdict["번호"] in matching:
                    verdict["검증근거"] = {"미래근거": [{
                        "근거": "51", "대상": "청소년 보호 정책" if implementation else "청소년 보호 정책의 세부 내용",
                        "활동": "적용" if implementation else "공개", "원문": plan, "양태": "계획",
                    }]}
            return json.dumps(result, ensure_ascii=False)

    writer = PolicyWriter()
    fragments = _official_fragments() + (plan_fragment,) + ((done_fragment,) if completion else ())
    output = run_v2("가나다전자", fragments, None, writer_ask=writer, reviewer_ask=PolicyReviewer(), as_of_date="2026-09-23")
    assert writer.section_calls == 9
    assert not output.quality_observation.safety_problems
    assert all(has_valid_provenance_seal(source) for source in output.report.citations)
    assert all(fact.evidence_binding for fact in output.report.fact_records)
    return output


def test_명시된_같은행동이_완료되면_정상_계획만_본문과_보도표에서_제외한다():
    control = _run_policy(completion=False)
    assert any(_POLICY_PUBLICATION_PLAN in fact.claim for fact in control.report.fact_records)
    output = _run_policy()
    assert any(_POLICY_PUBLICATION_DONE in text for text, _cite in _section(output, "past_changes").prose_lines)
    assert any(_POLICY_PUBLICATION_DONE in fact.claim for fact in output.report.fact_records)
    assert all(_POLICY_PUBLICATION_PLAN not in fact.claim for fact in output.report.fact_records)
    assert all(_POLICY_PUBLICATION_PLAN not in text for section in output.report.sections for text, _cite in section.prose_lines)
    assert all(_POLICY_PUBLICATION_PLAN not in row[-1] for table in _news_tables(output) for row in table.rows)
    assert 51 not in {source.number for source in output.report.citations}
    assert 52 in {source.number for source in output.report.citations}


def test_정책발표가_완료되어도_내년시행과_보호자동의조건_계획은_보존한다():
    output = _run_policy(implementation=True)
    assert any(_POLICY_IMPLEMENTATION_PLAN in text for text, _cite in _section(output, "future_strategy").prose_lines)
    assert any(_POLICY_PUBLICATION_DONE in text for text, _cite in _section(output, "past_changes").prose_lines)
    claims = [fact.claim for fact in output.report.fact_records]
    assert any(_POLICY_IMPLEMENTATION_PLAN in claim for claim in claims)
    assert any(_POLICY_PUBLICATION_DONE in claim for claim in claims)


def test_완료문장이_사실결속에서_탈락하면_정상_공개계획을_지우지_않는다():
    output = _run_policy(omit_completion_slot=True)
    claims = [fact.claim for fact in output.report.fact_records]
    assert any(_POLICY_PUBLICATION_PLAN in claim for claim in claims)
    assert all(_POLICY_PUBLICATION_DONE not in claim for claim in claims)
    assert any(_POLICY_PUBLICATION_PLAN in text for text, _cite in _section(output, "future_strategy").prose_lines)
    assert any(entry.get("reason_code") == "public_sentence_fact_unbound" for entry in output.review_diagnostics)
    assert all(_POLICY_PUBLICATION_DONE not in row[-1] for table in _news_tables(output) for row in table.rows)
    assert 51 in {source.number for source in output.report.citations}
    assert 52 not in {source.number for source in output.report.citations}
