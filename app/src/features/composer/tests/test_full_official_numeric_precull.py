"""FULL 공식 산문의 자기 인용 숫자 증명 경계."""

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer import logic as composer_logic
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.structured_claims import enforce_public_numeric_safety
from src.features.composer.tests.injected_program_fixture import (
    make_numeric_performance_evidence,
)
from src.features.composer.structured_claims import build_past_changes_numeric_claims
from src.shared.report_evidence.constants import (
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_NEWS,
)
from src.shared.report_quality.models import VerificationState


def _sentence(text: str, citation: str = "1", **changes) -> ComposedSentence:
    return ComposedSentence(
        text=text,
        citations=(citation,),
        grade=GRADE_CONFIRMED,
        verification_state=VerificationState.VERIFIED.value,
        **changes,
    )


def _fragment(number: str, text: str, *, kind: str = SOURCE_KIND_DART_BUSINESS_REPORT):
    return CollectedFragment(
        fragment_id=number,
        kind="공식 원문" if kind != SOURCE_KIND_NEWS else SOURCE_KIND_NEWS,
        text=text,
        formal_source_kind=kind,
    )


def _filter(
    sentence: ComposedSentence,
    fragments: tuple[CollectedFragment, ...],
    *,
    program_ids: frozenset[str] = frozenset(),
):
    report = ComposedReport((ComposedSection("past_changes", (sentence,)),))
    return enforce_public_numeric_safety(
        report,
        strict_cited_fragments={fragment.fragment_id: fragment for fragment in fragments},
        verified_program_fact_ids=program_ids,
    )


def test_자기_공식_인용에_같은_날짜_표기가_있으면_유지한다():
    sentence = _sentence("가나다전자는 2024년 3월에 작업을 마쳤다.")
    result, filtering = _filter(
        sentence, (_fragment("1", "2024년 3월에 작업을 마쳤다."),)
    )
    assert result.sections[0].sentences == (sentence,)
    assert filtering.removed_total == 0


def test_다른_조각이나_표에서_연도를_빌려도_자기_인용에_없으면_제외한다():
    sentence = _sentence("가나다전자는 2024년에 작업을 마쳤다.")
    result, filtering = _filter(
        sentence,
        (
            _fragment("1", "가나다전자는 작업을 마쳤다."),
            _fragment("2", "2024년 실적표를 공시했다."),
        ),
    )
    assert result.sections[0].sentences == ()
    assert filtering.removed_section_counts == (("past_changes", 1),)
    # 명시적인 FULL 문맥이 없으면 기존 관측/부분 경로의 바이트 계약을 유지한다.
    unchanged, _ = enforce_public_numeric_safety(
        ComposedReport((ComposedSection("past_changes", (sentence,)),))
    )
    assert unchanged.sections[0].sentences == (sentence,)


def test_같은_값이어도_원문의_날짜_표기를_바꾸면_제외한다():
    sentence = _sentence("가나다전자는 2024년 3월에 작업을 마쳤다.")
    result, filtering = _filter(
        sentence, (_fragment("1", "가나다전자는 2024.03에 작업을 마쳤다."),)
    )
    assert result.sections[0].sentences == ()
    assert filtering.removed_total == 1


def test_인용_조각이_실제로_없으면_닫는다():
    sentence = _sentence("가나다전자는 2024년에 작업을 마쳤다.")
    result, _ = _filter(sentence, (_fragment("2", "2024년 작업 기록"),))
    assert result.sections[0].sentences == ()


def test_legacy_공시도_최종_source_분류와_같이_검사한다():
    sentence = _sentence("가나다전자는 2024년에 작업을 마쳤다.")
    legacy = CollectedFragment(
        fragment_id="1", kind="공시", text="가나다전자는 작업을 마쳤다."
    )
    result, _ = _filter(sentence, (legacy,))
    assert result.sections[0].sentences == ()


def test_공식과_뉴스를_섞은_인용은_양쪽_예외를_우회하지_못한다():
    sentence = ComposedSentence(
        text="가나다전자는 2024년에 작업을 마쳤다.",
        citations=("1", "2"),
        grade=GRADE_CONFIRMED,
        verification_state=VerificationState.VERIFIED.value,
    )
    result, _ = _filter(
        sentence,
        (
            _fragment("1", "작업을 마쳤다."),
            _fragment("2", "2024년 보도", kind=SOURCE_KIND_NEWS),
        ),
    )
    assert result.sections[0].sentences == ()


def test_뉴스와_프로그램은_각자의_검산_경로를_유지한다():
    news = _sentence("가나다전자에 관한 2024년 보도가 나왔다.")
    result, _ = _filter(news, (_fragment("1", "별도 기사 본문", kind=SOURCE_KIND_NEWS),))
    assert result.sections[0].sentences == (news,)

    program = _sentence(
        "가나다전자는 2024년 계획을 확인했다.", verified_fact_id="program-fact-1"
    )
    result, _ = _filter(
        program,
        (_fragment("1", "별도 공식 원문"),),
        program_ids=frozenset({"program-fact-1"}),
    )
    assert result.sections[0].sentences == (program,)

    forged, _ = _filter(program, (_fragment("1", "별도 공식 원문"),))
    assert forged.sections[0].sentences == ()


def test_구조화_실적_문장은_자기_원문_산문_필터로_다시_판정하지_않는다():
    table, fragment, filing_meta = make_numeric_performance_evidence(
        fragment_number=40
    )
    sentences = build_past_changes_numeric_claims(table, (fragment,), filing_meta)
    assert sentences and all(sentence.structured_claim is not None for sentence in sentences)
    report = ComposedReport((ComposedSection("past_changes", sentences),))
    baseline, _ = enforce_public_numeric_safety(report)
    strict, _ = enforce_public_numeric_safety(
        report, strict_cited_fragments={fragment.fragment_id: fragment}
    )
    assert strict == baseline
    assert strict.sections[0].sentences == sentences


def test_숫자_작성지침은_FULL_프롬프트에만_추가한다(monkeypatch):
    fragment = CollectedFragment(
        fragment_id="1",
        kind="공식 원문",
        text="가나다전자는 작업을 마쳤다.",
        supported_claim_slots=("past_changes:completed_execution",),
        formal_source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
    )
    args = ("가나다전자", "past_changes", (fragment,), None)
    shadow = composer_logic.build_section_prompt(*args)
    full = composer_logic.build_section_prompt(*args, show_supported_claim_slots=True)
    assert "근거선택 ID가 가리키는 원문 조각의 표기" in full
    assert "근거선택 ID가 가리키는 원문 조각의 표기" not in shadow
    assert "«2023.04.10»이면 그 표기를 그대로" in full
    assert "«2023년 4월»로 줄이거나" in full
    assert "«1명»으로 쓰지 않는다" in full
    assert "«2023.04.10»이면 그 표기를 그대로" not in shadow

    # FULL 전용 상수를 비워도 SHADOW 프롬프트는 한 바이트도 달라지지 않는다.
    monkeypatch.setattr(composer_logic, "FULL_CITATION_SLOT_GUIDE", "")
    assert composer_logic.build_section_prompt(*args) == shadow


def test_FULL_보충_작성과_병합도_같은_자기인용_문맥으로_재검사한다(monkeypatch):
    from src.features.composer import pipeline as composer_pipeline
    from src.features.composer.tests.test_section_public_manifest import (
        _run_recovering_full,
    )

    seen: list[bool] = []
    original = composer_pipeline.enforce_public_numeric_safety

    def observed_filter(report, **kwargs):
        if kwargs.get("strict_cited_fragments") is not None:
            seen.append(bool(kwargs["strict_cited_fragments"]))
        return original(report, **kwargs)

    monkeypatch.setattr(
        composer_pipeline, "enforce_public_numeric_safety", observed_filter
    )
    output, _writer, _reviewer = _run_recovering_full(("identity",))

    assert output.effective_release_mode == "FULL"
    # 1차 본문, 보충 본문, 보충 병합 전체를 각각 같은 출처 문맥으로 검사한다.
    assert seen == [True, True, True]
