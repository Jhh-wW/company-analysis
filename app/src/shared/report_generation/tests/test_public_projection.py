"""공개 봉인 블록 자료형·digest·canonical 왕복의 적대 회귀.

이 파일은 ``shared/report_generation/public_projection.py``만 시험한다.
원본 ``Report``를 만들지 않고, S1 자료형이 자기 완결적으로 검증할 수 있는
불변식(I1~I8)과 두 digest(content/display)의 구분만 확인한다. builder가
실제 Report에서 이 값을 채우는 정합성(예: report.source_grades와의 완전한
병합 동치)은 S2 시험(``test_public_projection_builder.py``)의 몫이다.
"""

from __future__ import annotations

import dataclasses

import pytest

import src.shared.report_generation.public_projection as public_projection
from src.shared.report_generation.models import canonical_json
from src.shared.report_generation.public_projection import (
    PUBLIC_PROJECTION_VERSION,
    SECTION_IDS,
    PublicCitationRow,
    PublicCoverMetricsBlock,
    PublicPeriodSummaryBlock,
    PublicProjectionError,
    PublicReportDigest,
    PublicReportProjection,
    PublicSectionContentBlock,
    PublicSectionDisplay,
    PublicSectionLedger,
    PublicSummaryRow,
    PublicTableBlock,
    PublicVisualBlock,
    build_report_digest,
    public_report_digest_from_dict,
    public_report_digest_to_dict,
    public_report_projection_from_dict,
    public_report_projection_to_dict,
)


# ══════════════════════════════════════════════════════════
# 최소 유효 값 factory — 전부 이 파일 안에서만 쓰는 시험 전용 fixture
# ══════════════════════════════════════════════════════════


def _table_block(*, manifest_ref: str | None = None, presentation: str = "table", **overrides) -> PublicTableBlock:
    base = dict(
        caption="매출 구성",
        headers=("부문", "매출"),
        rows=(("반도체", "100"), ("기타", "50")),
        cite="[1]",
        numeric=True,
        presentation=presentation,
        display_unit="억원",
        manifest_ref=manifest_ref or ("a" * 64),
    )
    base.update(overrides)
    return PublicTableBlock(**base)


def _visual_block(*, table_index: int = 0, **overrides) -> PublicVisualBlock:
    base = dict(
        table_index=table_index,
        kind="구성",
        caption="매출 구성 도식",
        unit="%",
        note="괄호는 비중",
        reading="반도체 비중이 가장 큽니다.",
        items=(
            ("반도체", "100억원", "66.7", False),
            ("기타", "50억원", "33.3", False),
        ),
        series=(),
        flows=(),
        cards=(),
    )
    base.update(overrides)
    return PublicVisualBlock(**base)


def _period_summary_block(**overrides) -> PublicPeriodSummaryBlock:
    base = dict(
        title="3개년 변화",
        cite="[2]",
        items=(("매출", "2023", "80", "2025", "100", "억원", "+20", "증가", "up", "비고"),),
    )
    base.update(overrides)
    return PublicPeriodSummaryBlock(**base)


def _cover_metrics_block(**overrides) -> PublicCoverMetricsBlock:
    base = dict(title="핵심 실적", cite="[1]", items=(("매출액", "100", "억원"),))
    base.update(overrides)
    return PublicCoverMetricsBlock(**base)


def _summary_row(section_id: str, *, ordinal: str = "01", **overrides) -> PublicSummaryRow:
    base = dict(
        ordinal=ordinal,
        topic="정체성",
        section_display_number="1장",
        text="요약 문장",
        section_id=section_id,
    )
    base.update(overrides)
    return PublicSummaryRow(**base)


def _source_dict(*, number: int = 1, **overrides) -> dict[str, object]:
    base: dict[str, object] = {
        "number": number,
        "kind": "공시",
        "label": "사업보고서",
        "disclosed_at": "2026-03-01",
        "collected_at": "2026-03-02",
        "published_at": "2026-03-01",
        "domain": "dart.fss.or.kr",
        "source_id": f"src-{number}",
        "title": "사업보고서",
        "publisher": "금융감독원",
        "host": "dart.fss.or.kr",
        "url": "https://dart.fss.or.kr/x",
        "document_id": "doc-1",
        "location": "1장",
        "source_type": "공시",
        "fact_status": "확인",
        "used_in": ("identity",),
        "evidence_hashes": ("a" * 64,),
        "exact_evidence_hashes": ("a" * 64,),
        "domain_attestation_source_id": "",
        "domain_attestation_evidence": "",
        "provenance_seal": "",
        "provenance_role": "citation",
        "reporting_period": "2025",
        "ir_metadata_verification": "",
        "attachment_url": "",
        "domain_redirect_verification": "",
        "domain_redirect_from_host": "",
        "domain_redirect_to_host": "",
        "formal_source_kind": "",
        "identity_binding": "",
        "document_content_sha256": "",
    }
    base.update(overrides)
    return base


def _citation_row(*, number: int = 1, **overrides) -> PublicCitationRow:
    base = dict(
        number=number,
        label_display="사업보고서",
        url="https://dart.fss.or.kr/x",
        status_display="공시",
        verification_label="확인",
        location="1장",
        used_in_display="1장",
        source=_source_dict(number=number),
    )
    base.update(overrides)
    return PublicCitationRow(**base)


def _section_display(cell: str, **overrides) -> PublicSectionDisplay:
    base = dict(
        cell=cell,
        display_number="1",
        title="장 제목",
        tag="",
        paragraphs=(("1", "이 회사는 반도체를 만든다."),),
        sentences=(("이 회사는 반도체를 만든다.", "[1]"),),
        empty_reason="",
        guidance_lines=(),
        tables=(
            _table_block(presentation="bar"),
            _table_block(presentation="table", manifest_ref="b" * 64),
        ),
        visuals=(_visual_block(table_index=0),),
        period_summary=None,
    )
    base.update(overrides)
    return PublicSectionDisplay(**base)


def _section_ledger(*, fact_ids: tuple[str, ...] = ("fact-1",), numbers: tuple[str, ...] = ("1",), **overrides) -> PublicSectionLedger:
    base = dict(
        fact_ids=fact_ids,
        fact_records=tuple({"fact_id": fact_id, "text": "근거"} for fact_id in fact_ids),
        source_grade_contribution=tuple((number, ("확인",)) for number in numbers),
    )
    base.update(overrides)
    return PublicSectionLedger(**base)


def _section_block(
    cell: str,
    *,
    fact_ids: tuple[str, ...] | None = None,
    numbers: tuple[str, ...] = ("1",),
    display_overrides: dict | None = None,
) -> PublicSectionContentBlock:
    fact_ids = fact_ids if fact_ids is not None else (f"fact-{cell}",)
    display = _section_display(cell, **(display_overrides or {}))
    ledger = _section_ledger(fact_ids=fact_ids, numbers=numbers)
    return PublicSectionContentBlock(
        version=PUBLIC_PROJECTION_VERSION, display=display, ledger=ledger
    )


def _full_projection(*, section_overrides: dict | None = None, **overrides) -> PublicReportProjection:
    section_overrides = section_overrides or {}
    sections = tuple(
        _section_block(
            cell,
            display_overrides=section_overrides.get(cell, {}).get("display_overrides"),
        )
        for cell in SECTION_IDS
    )
    summary = tuple(
        _summary_row(cell, ordinal=f"{index + 1:02d}")
        for index, cell in enumerate(SECTION_IDS)
    )
    base = dict(
        version=PUBLIC_PROJECTION_VERSION,
        header={"company": "테스트기업", "release_mode": "FULL"},
        cover_metrics=_cover_metrics_block(),
        summary=summary,
        sections=sections,
        citations=(_citation_row(number=1),),
        summary_source_grade_contribution=(("1", ("확인",)),),
        grade_notice=("완전 공개", "모든 장이 공개됩니다"),
    )
    base.update(overrides)
    return PublicReportProjection(**base)


def _replace_section(
    projection: PublicReportProjection,
    cell: str,
    *,
    display_overrides: dict | None = None,
    ledger_overrides: dict | None = None,
) -> PublicReportProjection:
    sections = []
    for block in projection.sections:
        if block.display.cell == cell:
            display = block.display
            if display_overrides:
                display = dataclasses.replace(display, **display_overrides)
            ledger = block.ledger
            if ledger_overrides:
                ledger = dataclasses.replace(ledger, **ledger_overrides)
            block = PublicSectionContentBlock(
                version=block.version, display=display, ledger=ledger
            )
        sections.append(block)
    return dataclasses.replace(projection, sections=tuple(sections))


def _section_by_cell(projection: PublicReportProjection, cell: str) -> PublicSectionContentBlock:
    for block in projection.sections:
        if block.display.cell == cell:
            return block
    raise KeyError(cell)  # pragma: no cover - 시험 fixture 방어


# ══════════════════════════════════════════════════════════
# 먼저 빨간불부터 확인한 시험 6개
# ══════════════════════════════════════════════════════════


def test_content_digest는_fact_records_source_grades_section_fact_ids를_모두_덮는다() -> None:
    projection = _full_projection()
    baseline = build_report_digest(projection).content_sha256

    # 1) fact_records 값만 바꾼다(fact_id는 그대로) — FactRecord 본문 필드 변경.
    changed_records = _replace_section(
        projection,
        "identity",
        ledger_overrides={
            "fact_records": ({"fact_id": "fact-identity", "text": "달라진 근거"},)
        },
    )
    assert build_report_digest(changed_records).content_sha256 != baseline

    # 2) source_grades(등급 기여)만 바꾼다.
    changed_grades = _replace_section(
        projection,
        "identity",
        ledger_overrides={"source_grade_contribution": (("1", ("확인", "해석")),)},
    )
    assert build_report_digest(changed_grades).content_sha256 != baseline

    # 3) section.fact_ids만 바꾼다(I3를 지키려면 fact_records도 같이 바뀐다).
    changed_fact_ids = _replace_section(
        projection,
        "identity",
        ledger_overrides={
            "fact_ids": ("fact-identity-2",),
            "fact_records": ({"fact_id": "fact-identity-2", "text": "근거"},),
        },
    )
    assert build_report_digest(changed_fact_ids).content_sha256 != baseline


def test_display_digest는_ledger만_바꾸면_불변이다() -> None:
    projection = _full_projection()
    baseline = build_report_digest(projection)

    changed_ledger = _replace_section(
        projection,
        "identity",
        ledger_overrides={
            "fact_ids": ("fact-identity-2",),
            "fact_records": ({"fact_id": "fact-identity-2", "text": "다른 근거"},),
            "source_grade_contribution": (("1", ("확인", "해석")),),
        },
    )
    digest = build_report_digest(changed_ledger)
    assert digest.display_sha256 == baseline.display_sha256
    assert digest.content_sha256 != baseline.content_sha256

    changed_summary_grades = dataclasses.replace(
        projection,
        summary_source_grade_contribution=(("1", ("확인", "해석")),),
    )
    digest2 = build_report_digest(changed_summary_grades)
    assert digest2.display_sha256 == baseline.display_sha256
    assert digest2.content_sha256 != baseline.content_sha256


def test_block_sha256은_ledger를_덮고_display_sha256은_안_덮는다() -> None:
    """§02-1 원칙 2 — block_sha256은 {version,display,ledger}를 덮어야 한다.

    ``test_display_digest는_ledger만_바꾸면_불변이다``는 보고서 전체
    digest(content_sha256/display_sha256)만 봐서, ``block_sha256`` 계산에서
    ledger를 통째로 빼도(§02-2 설계의 ``block_sha256`` 정의 위반) content_sha256이
    ``PublicReportProjection`` 전체를 canonical_sha256으로 덮는 한 여전히
    바뀌어 보여 그 결함을 못 잡는다(root 검토에서 실측). 이 시험은 장 블록
    자신의 ``block_sha256``·``display_sha256``과 다른 장의 불변, 그리고
    ``PublicReportDigest.section_sha256s`` 항목 단위 변화까지 직접 본다.
    """

    projection = _full_projection()
    baseline_block = _section_by_cell(projection, "identity")
    baseline_other = _section_by_cell(projection, "business_model")
    baseline_digest = build_report_digest(projection)
    baseline_sections = dict(baseline_digest.section_sha256s)

    changed = _replace_section(
        projection,
        "identity",
        ledger_overrides={
            "fact_ids": ("fact-identity-2",),
            "fact_records": ({"fact_id": "fact-identity-2", "text": "다른 근거"},),
            "source_grade_contribution": (("1", ("확인", "해석")),),
        },
    )
    changed_block = _section_by_cell(changed, "identity")
    changed_other = _section_by_cell(changed, "business_model")
    changed_digest = build_report_digest(changed)
    changed_sections = dict(changed_digest.section_sha256s)

    # block_sha256은 ledger를 덮어야 하므로 바뀐다.
    assert changed_block.block_sha256 != baseline_block.block_sha256
    # display_sha256은 ledger를 안 덮어야 하므로 그대로다.
    assert changed_block.display_sha256 == baseline_block.display_sha256
    # 손대지 않은 다른 장의 block_sha256·display_sha256은 둘 다 불변.
    assert changed_other.block_sha256 == baseline_other.block_sha256
    assert changed_other.display_sha256 == baseline_other.display_sha256
    # 보고서 digest의 section_sha256s는 identity 항목만 바뀐다.
    assert changed_sections["identity"] != baseline_sections["identity"]
    for cell in SECTION_IDS:
        if cell == "identity":
            continue
        assert changed_sections[cell] == baseline_sections[cell]


def test_PUBLIC_CITATION_SOURCE_FIELDS는_canonical_Source_projection_키와_같다() -> None:
    """부록 행 source 키 계약이 canonical.py의 실제 Source projection과 갈리지 않게 감시한다.

    ``_PUBLIC_CITATION_SOURCE_FIELDS``는 private 함수(``_source_public_projection``,
    ``canonical.py`` ``__all__`` 밖)의 키 목록을 손으로 복제한 상수라, 원본이
    필드를 추가·삭제해도 이 파일은 조용히 낡을 수 있다. 시험에서만 그 private
    함수를 직접 불러 두 키 집합이 여전히 같은지 매 실행마다 확인한다.
    """

    from src.shared.report_generation import canonical as canonical_module

    produced_keys = set(canonical_module._source_public_projection({}))
    assert produced_keys == public_projection._PUBLIC_CITATION_SOURCE_FIELDS


def test_표시_파생_블록을_하나_빼면_content와_display_digest가_모두_바뀐다() -> None:
    projection = _full_projection(
        section_overrides={
            "past_changes": {
                "display_overrides": {"period_summary": _period_summary_block()}
            },
        }
    )
    baseline = build_report_digest(projection)

    def _assert_both_changed(variant: PublicReportProjection) -> None:
        digest = build_report_digest(variant)
        assert digest.content_sha256 != baseline.content_sha256
        assert digest.display_sha256 != baseline.display_sha256

    # 1) visuals를 뺀다.
    _assert_both_changed(
        _replace_section(projection, "identity", display_overrides={"visuals": ()})
    )

    # 2) period_summary(3개년 변화 요약 띠)를 뺀다.
    _assert_both_changed(
        _replace_section(
            projection, "past_changes", display_overrides={"period_summary": None}
        )
    )

    # 3) cover_metrics(표지 실적 띠)를 뺀다.
    _assert_both_changed(dataclasses.replace(projection, cover_metrics=None))

    # 4) 도식 reading(읽는 법)을 지운다.
    identity_block = _section_by_cell(projection, "identity")
    reading_removed = dataclasses.replace(identity_block.display.visuals[0], reading="")
    _assert_both_changed(
        _replace_section(
            projection, "identity", display_overrides={"visuals": (reading_removed,)}
        )
    )

    # 5) 문단 번호(ordinal)만 바꾼다 — 글자는 그대로.
    reordinaled = tuple(
        (str(int(ordinal) + 1), text)
        for ordinal, text in identity_block.display.paragraphs
    )
    _assert_both_changed(
        _replace_section(
            projection, "identity", display_overrides={"paragraphs": reordinaled}
        )
    )


_INVARIANT_CASE_IDS = ["I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8"]


@pytest.mark.parametrize("case_id", _INVARIANT_CASE_IDS)
def test_불변식_I1부터_I8은_각각_PublicProjectionError로_닫힌다(case_id, monkeypatch) -> None:
    if case_id == "I1":
        projection = _full_projection()
        with pytest.raises(PublicProjectionError, match="I1"):
            dataclasses.replace(projection, sections=projection.sections[::-1])

    elif case_id == "I2":
        with pytest.raises(PublicProjectionError, match="I2"):
            _section_display("identity", sentences=(("다른 문장입니다.", "[1]"),))

    elif case_id == "I3":
        with pytest.raises(PublicProjectionError, match="I3"):
            PublicSectionLedger(
                fact_ids=("fact-a",),
                fact_records=({"fact_id": "fact-b"},),
                source_grade_contribution=(),
            )

    elif case_id == "I4":
        with pytest.raises(PublicProjectionError, match="I4"):
            _full_projection(summary_source_grade_contribution=(("999", ("확인",)),))

    elif case_id == "I5":
        with pytest.raises(PublicProjectionError, match="I5"):
            _section_display(
                "identity",
                tables=(_table_block(presentation="table"),),
                visuals=(_visual_block(table_index=0),),
            )

    elif case_id == "I6":
        with pytest.raises(PublicProjectionError, match="verification_label"):
            _citation_row(verification_label="   ")

    elif case_id == "I7":
        # I7은 자기 자신의 필드로 table_public_projection을 재계산하는 재사용
        # 검사라 정상 경로에서는 항상 참(자기 값과 자기 값을 비교)이다.
        # table_public_projection이 미래에 갈라지는 회귀를 이 방어가 실제로
        # 잡는지 증명하려면 그 함수를 몽키패치해 불일치를 주입해야 한다.
        monkeypatch.setattr(
            public_projection,
            "table_public_projection",
            lambda table: {"broken": True},
        )
        with pytest.raises(PublicProjectionError, match="I7"):
            _table_block()

    elif case_id == "I8":
        with pytest.raises(PublicProjectionError, match="I8"):
            _visual_block(series=(("추이", "주의", ({"ratio": 1.5},)),))

    else:  # pragma: no cover - parametrize 목록 방어
        pytest.fail(f"알 수 없는 불변식 케이스입니다: {case_id}")


def test_canonical_json_왕복은_exact_bytes와_digest를_보존한다() -> None:
    projection = _full_projection()
    payload = public_report_projection_to_dict(projection)
    first_bytes = canonical_json(payload)

    restored = public_report_projection_from_dict(payload)
    assert restored == projection

    second_bytes = canonical_json(public_report_projection_to_dict(restored))
    assert first_bytes == second_bytes

    digest = build_report_digest(projection)
    assert build_report_digest(restored) == digest

    digest_payload = public_report_digest_to_dict(digest)
    restored_digest = public_report_digest_from_dict(digest_payload)
    assert restored_digest == digest
    assert canonical_json(digest_payload) == canonical_json(
        public_report_digest_to_dict(restored_digest)
    )

    # float 거부 — models.canonical_json이 원천적으로 막는다(새 직렬화기 없음).
    with pytest.raises(TypeError):
        canonical_json({"ratio": 1.5})

    # 사람이 만든 문자열 필드에 float를 흘려 넣으면 I8이 구성 시점에 막는다.
    with pytest.raises(PublicProjectionError):
        PublicPeriodSummaryBlock(
            title="t",
            cite="[1]",
            items=(("a", "b", "c", "d", "e", "f", "g", "h", "i", 1.5),),
        )


def test_section_sha256s는_SECTION_IDS_순서_9개다() -> None:
    projection = _full_projection()
    digest = build_report_digest(projection)
    assert tuple(cell for cell, _digest in digest.section_sha256s) == SECTION_IDS
    assert len(digest.section_sha256s) == 9

    # ★ 유일하게 허용된 생산 상수 import — SECTION_IDS(shared 정본)와
    # composer/constants.py:35(생산 소비자)가 «같은 순서»라는 사실 자체가
    # 지켜야 하는 계약이라, 여기서는 리터럴 오라클이 아니라 두 정본의
    # 동일성만 비교한다.
    from src.features.composer.constants import SECTION_IDS as COMPOSER_SECTION_IDS

    assert SECTION_IDS == COMPOSER_SECTION_IDS
