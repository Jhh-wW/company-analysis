"""확보자료 작성도 근거 소유권과 동시 호출 안전성을 함께 지킨다."""

import json
import threading
from collections import Counter
from dataclasses import replace

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.logic import compose_sections
from src.features.composer.partial_evidence import build_partial_evidence_view
from src.features.composer.port import CollectedFragment
from src.shared.report_quality.composition_diagnostics import observed_composition_steps


WORKERS = 3
WAIT_SECONDS = 5


def _sources():
    return (
        CollectedFragment("1", "사업내용", "가나다전자는 센서 검사 장비를 제조한다."),
        CollectedFragment("2", "뉴스", "센서 검사 서비스의 구독 계약을 제공한다.",
                          supported_claim_slots=("business_model:revenue_model",)),
        CollectedFragment("3", "분류 불명", "근거 종류가 확인되지 않은 자료다."),
    )


def _seven_section_sources():
    return (*_sources(), CollectedFragment(
        "4", "공시", "회사는 원재료 조달 지연을 현재 운영 과제로 밝혔다.",
        supported_claim_slots=("current_challenges:issue",),
    ))


def test_등록되지_않은_근거와_지원하지_않는_장은_승격하지_않는다():
    view = build_partial_evidence_view(_sources())
    assert "3" not in {fragment.fragment_id for fragment in view.fragments}
    assert "1" in view.allowed_fragment_ids_by_section["identity"]
    assert "2" in view.allowed_fragment_ids_by_section["business_model"]
    assert "2" not in view.allowed_fragment_ids_by_section["current_challenges"]
    assert not view.allowed_fragment_ids_by_section["culture"]
    assert not view.allowed_fragment_ids_by_section["future_strategy"]
    with pytest.raises(TypeError):
        view.allowed_fragment_ids_by_section["culture"] = frozenset({"1"})


def test_같은_id의_충돌은_작성_전에_거절한다():
    source = _sources()[0]
    with pytest.raises(ValueError, match="서로 다른"):
        build_partial_evidence_view((source, replace(source, text="다른 원문")))


def test_빈_장을_호출하지_않고_동일_근거로_직렬과_병렬을_작성한다():
    sources = _seven_section_sources()
    view = build_partial_evidence_view(sources)
    eligible = sum(bool(values) for values in view.packets.values())
    assert eligible == 7
    prompts_serial = []
    prompts_parallel = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)
    entered = 0
    active = 0
    peak = 0

    def response():
        return json.dumps({"문장들": [{
            "글": "가나다전자는 센서 검사 장비를 제조한다.",
            "인용": ["1"], "등급": "확인",
        }]}, ensure_ascii=False)

    def serial(prompt):
        prompts_serial.append(prompt)
        return response()

    def parallel(prompt):
        nonlocal entered, active, peak
        with lock:
            prompts_parallel.append(prompt)
            entered += 1
            batch = entered
            active += 1
            peak = max(peak, active)
        if batch <= WORKERS:
            barrier.wait(timeout=WAIT_SECONDS)
        with lock:
            active -= 1
        return response()

    parallel.parallel_safe = True
    parallel.max_parallel_calls = WORKERS
    serial_diagnostics = []
    parallel_diagnostics = []
    a = compose_sections("가나다전자", sources, None, serial, partial_evidence=view,
                         composition_diagnostics=serial_diagnostics)
    b = compose_sections("가나다전자", sources, None, parallel, partial_evidence=view,
                         composition_diagnostics=parallel_diagnostics)
    assert a == b
    assert tuple(section.section_id for section in b.sections) == SECTION_IDS
    assert len(prompts_serial) == len(prompts_parallel) == eligible
    assert peak == WORKERS
    assert Counter(prompts_serial) == Counter(prompts_parallel)
    # ★ 확보자료 경로는 «장별 packet»만 싣는다 (2026-09-23 4차 실측 B1) —
    #   전체 조각을 캐시용 공통 앞부분으로 실었더니 작가가 허용 밖 조각의
    #   내용을 옮겨 쓰고 허용된 번호를 인용으로 붙였다. 공유 앞부분이
    #   없으므로 캐시 표식도 없어야 한다 — 표식이 남으면 공급자 호출부가
    #   장마다 다른 근거를 틀린 경계로 자른다.
    assert not any(
        hasattr(prompt, "cache_prefix_chars") for prompt in prompts_parallel
    )
    assert all("이 장에서 사용할 수 있는 근거 조각 id:" in prompt for prompt in prompts_parallel)
    # 5장 전용 조각(4)의 원문은 5장 프롬프트 «한 곳»에만 실린다.
    challenge_text = "원재료 조달 지연"
    assert sum(challenge_text in prompt for prompt in prompts_parallel) == 1
    for diagnostics, workers in ((serial_diagnostics, 1), (parallel_diagnostics, WORKERS)):
        execution = next(item for item in diagnostics if item["step"] == "v2_작성_실행방식")
        assert {key: value for key, value in execution.items() if key != "소요_ms"} == {
            "step": "v2_작성_실행방식", "동시상한": workers, "장수": 9,
            "작성대상장수": 7, "빈근거생략장수": 2,
        }
        assert observed_composition_steps([execution]) == (execution,)


@pytest.mark.parametrize("workers", [1, WORKERS])
def test_작성대상이_없는_부분보고서도_목차와_생략수를_구분한다(workers):
    view = build_partial_evidence_view((_sources()[-1],))

    def ask(_prompt):
        pytest.fail("빈 근거 장의 작가를 호출하면 안 됩니다")

    def prepare():
        pytest.fail("작성 대상이 없으면 공급자 준비를 호출하면 안 됩니다")

    ask.parallel_safe = True
    ask.max_parallel_calls = workers
    ask.prepare_parallel = prepare
    diagnostics = []
    result = compose_sections("시험법인", (), None, ask, partial_evidence=view,
                              composition_diagnostics=diagnostics)
    assert tuple(section.section_id for section in result.sections) == SECTION_IDS
    assert not any(section.sentences for section in result.sections)
    execution = next(item for item in diagnostics if item["step"] == "v2_작성_실행방식")
    assert {key: value for key, value in execution.items() if key != "소요_ms"} == {
        "step": "v2_작성_실행방식", "동시상한": workers, "장수": 9,
        "작성대상장수": 0, "빈근거생략장수": 9,
    }
    assert observed_composition_steps([execution]) == (execution,)


@pytest.mark.parametrize("workers", [1, WORKERS])
def test_파싱재시도는_작성대상장수를_실제호출수로_바꾸지_않는다(workers):
    view = build_partial_evidence_view(_seven_section_sources())
    prompts = []
    lock = threading.Lock()

    def ask(prompt):
        with lock:
            prompts.append(prompt)
            first = len(prompts) == 1
        return "잘못된 JSON" if first else json.dumps({"문장들": []}, ensure_ascii=False)

    ask.parallel_safe = True
    ask.max_parallel_calls = workers
    diagnostics = []
    compose_sections("시험법인", (), None, ask, partial_evidence=view,
                     composition_diagnostics=diagnostics)
    assert len(prompts) == 8
    execution = next(item for item in diagnostics if item["step"] == "v2_작성_실행방식")
    assert execution["장수"] == 9
    assert execution["작성대상장수"] == 7
    assert execution["빈근거생략장수"] == 2
    assert set(execution) == {
        "step", "동시상한", "장수", "소요_ms", "작성대상장수", "빈근거생략장수",
    }
    assert observed_composition_steps([execution]) == (execution,)


def test_확보자료_작가는_자기_장_packet_밖의_원문을_보지_못한다():
    """★ 4차 실측 B1 — 7장 허용 조각이 1/4뿐인데 전체 34조각이 프롬프트에
    노출돼, 작가가 허용 밖 조각(2/18)의 수익인식 내용을 [1]로 잘못 인용했다.
    장별 packet만 실으면 허용 밖 «원문» 자체가 프롬프트에 없다."""
    sources = _seven_section_sources()
    view = build_partial_evidence_view(sources)
    prompts_by_section = {}

    class _Recorder:
        parallel_safe = False

        def __call__(self, prompt):
            return json.dumps({"문장들": []}, ensure_ascii=False)

        def for_section(self, section_id):
            def ask(prompt):
                prompts_by_section[section_id] = prompt
                return json.dumps({"문장들": []}, ensure_ascii=False)
            return ask

    compose_sections("가나다전자", sources, None, _Recorder(), partial_evidence=view)

    challenge_prompt = prompts_by_section["current_challenges"]
    identity_prompt = prompts_by_section["identity"]
    # 5장 프롬프트: 자기 packet(조각 4)의 원문은 있고, 허용 ID 목록도 4뿐이다.
    assert "원재료 조달 지연" in challenge_prompt
    assert "이 장에서 사용할 수 있는 근거 조각 id: 4" in challenge_prompt
    # 다른 장 전용 조각의 «원문»은 5장 프롬프트에 아예 없다 — 목록 우회가
    # 아니라 노출 자체가 없어야 한다.
    assert "센서 검사 서비스의 구독 계약" not in challenge_prompt
    # 반대 방향도 같다 — 1장 프롬프트에 5장 전용 원문이 없다.
    assert "원재료 조달 지연" not in identity_prompt
    # 검수·복구용 전체 근거는 이 함수 밖(파이프라인)에서 따로 받는다 — 여기서
    # 줄인 것은 «작가 프롬프트»뿐이다.


def test_legacy_flat_경로는_전체_조각과_공유_캐시_앞부분을_유지한다():
    """★ 대칭 보존 — 확보자료 격리가 legacy flat의 캐시 공유를 건드리면
    안 된다. flat은 아홉 장이 같은 조각 전체를 보므로 공유 앞부분이 정당하다."""
    sources = _seven_section_sources()
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return json.dumps({"문장들": []}, ensure_ascii=False)

    compose_sections("가나다전자", sources, None, ask)

    assert len(prompts) == len(SECTION_IDS)
    boundaries = {prompt.cache_prefix_chars for prompt in prompts}
    assert len(boundaries) == 1
    prefixes = {str(prompt)[: prompt.cache_prefix_chars] for prompt in prompts}
    assert len(prefixes) == 1
    # flat 작가는 조각 «전체»를 본다 — 정보량을 줄이지 않았다.
    assert all("원재료 조달 지연" in prompt for prompt in prompts)
    assert all("센서 검사 장비를 제조한다" in prompt for prompt in prompts)


def test_장에_배정되지_않은_인용은_반환값에서_제외한다():
    sources = (CollectedFragment("1", "수익인식", "용역은 진행기준에 따라 수익으로 인식한다."),)
    view = build_partial_evidence_view(sources)
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return json.dumps({"문장들": [{"글": "없는 근거의 문장이다.", "인용": ["99"], "등급": "확인"}]}, ensure_ascii=False)

    result = compose_sections("가나다전자", sources, None, ask, partial_evidence=view)
    assert len(calls) == 1
    assert not any(section.sentences for section in result.sections)


@pytest.mark.parametrize("citations,slot,kept", (
    (["2"], "business_model:revenue_model", True),
    (["2"], "business_model:customer_type", False),
    (["1", "2"], "business_model:customer_type", False),
    (["1"], "business_model:customer_type", True),
))
def test_명시된_지원슬롯은_빈슬롯_자료를_추가해_우회하지_않는다(citations, slot, kept):
    sources = _sources()
    view = build_partial_evidence_view(sources)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return json.dumps({"문장들": [{
            "글": "회사는 센서 검사 서비스의 구독 계약을 제공한다.",
            "인용": citations, "등급": "확인", "주장슬롯": slot,
        }]}, ensure_ascii=False)

    result = compose_sections("가나다전자", sources, None, ask, partial_evidence=view)
    section = next(section for section in result.sections if section.section_id == "business_model")
    assert bool(section.sentences) is kept
    # 장별 격리 뒤에는 조각 2가 자기 장(business_model) 프롬프트에만 실린다 —
    # 실리는 곳마다 지원 주장슬롯 표기는 그대로 보여야 한다.
    with_fragment2 = [p for p in prompts if "구독 계약을 제공한다" in p]
    assert with_fragment2, "조각 2를 실은 프롬프트가 없습니다"
    assert all(
        "지원 주장슬롯: business_model:revenue_model" in prompt
        for prompt in with_fragment2
    )
    assert all("FULL 필수 의미칸" not in prompt for prompt in prompts)
