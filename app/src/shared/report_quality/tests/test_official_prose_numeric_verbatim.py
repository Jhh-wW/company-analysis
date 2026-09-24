"""FULL 안전 판정의 공식 원문 산문 숫자 예외(ADR 0005) — assess_safety 단위 시험.

확인 등급 공식 원문 산문은 문장의 숫자 표현이 «모두» 그 사실이 인용한 조각의 정확
원문에 같은 표기로 있을 때만 NumericBinding 없이 통과한다(FULL v3 계약만). 하나라도
어긋나면 예전 두 문구로 막힌다. 음성은 여기서 assess_safety를 직접 불러 단정한다 —
끝-끝에서는 앞 단계 숫자 필터가 먼저 빼는 경우가 많다. 문장·원문은 전부 합성이다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.assessment import (
    _official_prose_cited_texts,
    assess_safety,
)
from src.shared.report_quality.constants import (
    INTERPRETATION_CLAIM_TYPE,
    LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    OFFICIAL_PROSE_EXACT_TEXT_KEY,
    QUALITY_CONTRACT_VERSION,
    STRICT_QUALITY_CONTRACT_VERSION,
    VERIFIED_PROSE_CLAIM_TYPE,
)
from src.shared.report_quality.contract import contract_for_generation
from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)

_FACT_ID = "v2-prose-" + "a" * 32
_SLOT = CLAIM_SLOTS_BY_SECTION["identity"][0]
_LABELS_MISSING = f"{_FACT_ID}의 구조화 수치 이름표가 비었습니다"
_BINDING_MISSING = f"{_FACT_ID}의 수치에 versioned NumericBinding이 없습니다"
_BLOCKED = (_LABELS_MISSING, _BINDING_MISSING)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidate(
    claim: str,
    cited_texts: tuple[str, ...],
    *,
    carried: tuple[str | None, ...] | None = None,
    sibling_texts: tuple[str, ...] = (),
    claim_type: str = VERIFIED_PROSE_CLAIM_TYPE,
    source_kinds: tuple[str, ...] | None = None,
    extra_sources: tuple[SourceDocument, ...] = (),
) -> ReportCandidate:
    """공식 산문 사실 한 건. 인용 조각 ``cited_texts`` 마다 출처 한 건.

    ``carried`` 는 증거 목록에 실린 원문(None이면 원문 칸 없음, 기본은 인용 원문).
    ``sibling_texts`` 는 첫 출처 문서의 «인용하지 않은» 형제 조각 — 지문은 출처 목록에
    있지만 사실은 인용하지 않았다.
    """

    carried = cited_texts if carried is None else carried
    kinds = source_kinds or ("filing",) * len(cited_texts)
    sources: list[SourceDocument] = []
    manifest: list[dict[str, str]] = []
    rows = zip(cited_texts, carried, kinds)
    for index, (cited, carried_text, kind) in enumerate(rows):
        source_id = f"source-official-{index}"
        hashes = (_sha(cited), *(_sha(text) for text in sibling_texts if index == 0))
        sources.append(
            SourceDocument(
                source_id,
                f"document:example.com:official-{index}",
                exact_evidence_hashes=hashes,
                document_content_sha256=_sha(f"문서 {index}"),
                counts_toward_document_floor=kind != "news",
                source_kind=kind,
            )
        )
        record = {
            "fragment_id": str(index + 1),
            "source_id": source_id,
            "document_identity": f"document:example.com:official-{index}",
            "exact_sha256": _sha(cited),
        }
        if carried_text is not None:
            record[OFFICIAL_PROSE_EXACT_TEXT_KEY] = carried_text
        manifest.append(record)
    fact = ClaimFact(
        fact_id=_FACT_ID,
        section_owner="identity",
        source_id=sources[0].source_id,
        source_identity=sources[0].document_identity,
        verification_state="verified",
        claim_slot=_SLOT,
        evidence_binding_valid=True,
        claim=claim,
        subject_scope="연결",
        supporting_source_ids=tuple(source.source_id for source in sources),
        supporting_source_identities=tuple(
            source.document_identity for source in sources
        ),
        supporting_evidence_hashes=tuple(_sha(text) for text in cited_texts),
        claim_type=claim_type,
        state_evidence=json.dumps(manifest, ensure_ascii=False),
    )
    return ReportCandidate(
        (ReportSectionCandidate("identity", (_FACT_ID,), public_sentence_count=1),),
        (fact,),
        (*sources, *extra_sources),
    )


def _problems(
    candidate: ReportCandidate, version: str = STRICT_QUALITY_CONTRACT_VERSION,
):
    return assess_safety(candidate, contract_for_generation(version)).problems


def _sources(candidate: ReportCandidate) -> dict[str, SourceDocument]:
    return {source.source_id: source for source in candidate.sources}


# ── 양성 ─────────────────────────────────────────────────────────────


def test_P1_연도가_인용원문에_그대로_있으면_FULL은_통과한다():
    candidate = _candidate("가나다전자는 2019년 설립됐다.", ("가나다전자는 2019년 설립됐다.",))
    assert _problems(candidate) == ()


def test_P2_연도와_개수가_모두_원문에_있으면_통과한다():
    candidate = _candidate(
        "가나다전자는 2019년 3월 15일 설립됐고 국내외 12개 사업장을 둔다.",
        ("가나다전자는 2019년 3월 15일 설립됐다. 국내외 12개 사업장을 운영한다.",),
    )
    assert _problems(candidate) == ()


def test_P3_두_출처_조각에_나뉜_숫자는_조각별_합집합으로_통과한다():
    candidate = _candidate(
        "가나다전자는 2019년 설립됐고 12개 사업장을 둔다.",
        ("가나다전자는 2019년 설립됐다.", "국내외 12개 사업장을 운영한다."),
    )
    assert _problems(candidate) == ()


def test_띄어_쓴_크기어와_단위는_같은_값이면_통과한다():
    candidate = _candidate("가나다전자의 설비 투자는 3억 원이다.", ("설비 투자 3억원",))
    assert _problems(candidate) == ()


# ── 음성: 원문에 그대로가 아니다 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("claim", "cited"),
    [
        # N1 연도가 인용 원문에 없다
        ("가나다전자는 2019년 3월 15일 설립됐다.", "가나다전자는 2019년 3월 설립됐다."),
        # N8 환산 수 — 원 단위 원값을 억원으로 바꾸고 반올림했다
        ("가나다전자의 매출은 1,683억원이다.", "매출액 168,312,345,678원"),
        # N8b 반올림 수
        ("가나다전자의 영업이익률은 24.3%다.", "영업이익률 24.28%"),
        # N9 계산 증감률
        ("가나다전자의 매출은 25.0% 늘었다.", "매출액은 1,000억원에서 1,250억원이 됐다."),
        # N9b 계산 합계
        ("가나다전자의 두 부문 매출 합계는 2,250억원이다.", "가 부문 1,000억원, 나 부문 1,250억원이다."),
        # N10 띄어 쓴 크기어를 가르면 통과하던 것
        ("가나다전자의 투자는 3억 원이다.", "제3공장 증설에 5억 원을 썼다."),
        # N11 같은 방식의 큰 수
        ("가나다전자의 투자는 1조 2천억 원이다.", "제1·제2공장에 총 4천억 원을 투자했다."),
        # N12 자릿수 부분 일치
        ("가나다전자는 12월에 공시했다.", "가나다전자는 2012년에 공시했다."),
        # N19 부호가 다르다(원문은 양수·음수 표기)
        ("가나다전자의 이익률은 -3.5%로 집계됐다.", "이익률은 3.5%로 집계됐다."),
        ("가나다전자의 이익률은 3.5%로 집계됐다.", "이익률은 △3.5%로 집계됐다."),
        # N13 한글 수량이 원문에 없다
        ("가나다전자의 생산 능력은 두 배로 늘었다.", "생산 능력이 크게 늘었다."),
        # 표기를 바꾼 날짜(최소 정규화)
        ("가나다전자는 2019년 3월 15일 설립됐다.", "설립일 2019.03.15"),
    ],
    ids=(
        "N1_연도없음", "N8_환산", "N8b_반올림", "N9_증감률", "N9b_합계", "N10_3억원",
        "N11_1조2천억원", "N12_부분일치", "N19_음수부호", "N19b_삼각음수", "N13_한글수량",
        "날짜표기변경",
    ),
)
def test_원문에_그대로_없는_숫자는_예전_두_문구로_막힌다(claim: str, cited: str):
    assert _problems(_candidate(claim, (cited,))) == _BLOCKED


def test_N17_조각_경계를_이어_없던_토큰을_만들지_않는다():
    candidate = _candidate("가나다전자는 3개 사업을 영위한다.", ("부문 합계 3", "개 사업을 영위한다."))
    assert _problems(candidate) == _BLOCKED


# ── 음성: 인용한 그 조각이 아니다 ───────────────────────────────────


def test_N2_숫자가_같은_문서의_형제_조각에만_있으면_막힌다():
    """형제 조각의 지문은 출처 목록에 있지만 사실이 인용한 조각이 아니다."""

    candidate = _candidate(
        "가나다전자는 2019년 설립됐다.",
        ("가나다전자는 공식 원문을 공개했다.",),
        sibling_texts=("가나다전자는 2019년 설립됐다.",),
    )
    assert _problems(candidate) == _BLOCKED


def test_N3_숫자가_인용하지_않은_다른_출처에만_있으면_막힌다():
    other_text = "가나다전자는 2019년 설립됐다."
    other = SourceDocument(
        "source-other",
        "document:example.com:other",
        exact_evidence_hashes=(_sha(other_text),),
        document_content_sha256=_sha("다른 문서"),
        source_kind="filing",
    )
    candidate = _candidate(
        "가나다전자는 2019년 설립됐다.",
        ("가나다전자는 공식 원문을 공개했다.",),
        extra_sources=(other,),
    )
    assert _problems(candidate) == _BLOCKED


def test_N4_실린_원문의_숫자_한_글자를_바꾸면_지문이_어긋나_막힌다():
    candidate = _candidate(
        "가나다전자는 2019년 설립됐다.",
        ("가나다전자는 2018년 설립됐다.",),
        carried=("가나다전자는 2019년 설립됐다.",),
    )
    assert _problems(candidate) == _BLOCKED


def test_N5_실린_원문을_형제_조각으로_바꾸고_기록_지문도_맞추면_사실의_조각_지문과_달라_막힌다():
    sibling = "가나다전자는 2019년 설립됐다."
    candidate = _candidate(
        "가나다전자는 2019년 설립됐다.",
        ("가나다전자는 공식 원문을 공개했다.",),
        sibling_texts=(sibling,),
    )
    manifest = json.loads(candidate.facts[0].state_evidence)
    manifest[0][OFFICIAL_PROSE_EXACT_TEXT_KEY] = sibling
    manifest[0]["exact_sha256"] = _sha(sibling)
    forged = replace(
        candidate,
        facts=(
            replace(
                candidate.facts[0],
                state_evidence=json.dumps(manifest, ensure_ascii=False),
            ),
        ),
    )
    assert _problems(forged) == _BLOCKED


def test_N16_원문_칸이_없는_옛_형식_공식_사실은_막힌다():
    candidate = _candidate(
        "가나다전자는 2019년 설립됐다.",
        ("가나다전자는 2019년 설립됐다.",),
        carried=(None,),
    )
    assert _problems(candidate) == _BLOCKED


def test_증거_목록이_깨졌거나_기록_수가_다르면_막힌다():
    candidate = _candidate("가나다전자는 2019년 설립됐다.", ("가나다전자는 2019년 설립됐다.",))
    broken = replace(
        candidate, facts=(replace(candidate.facts[0], state_evidence="{깨진 JSON"),),
    )
    manifest = json.loads(candidate.facts[0].state_evidence)
    doubled = replace(
        candidate,
        facts=(
            replace(
                candidate.facts[0],
                state_evidence=json.dumps(manifest * 2, ensure_ascii=False),
            ),
        ),
    )
    assert _problems(broken) == _BLOCKED
    assert _problems(doubled) == _BLOCKED


def test_기록의_source_id가_인용_출처와_다르면_막힌다():
    candidate = _candidate("가나다전자는 2019년 설립됐다.", ("가나다전자는 2019년 설립됐다.",))
    manifest = json.loads(candidate.facts[0].state_evidence)
    manifest[0]["source_id"] = "source-elsewhere"
    forged = replace(
        candidate,
        facts=(
            replace(
                candidate.facts[0],
                state_evidence=json.dumps(manifest, ensure_ascii=False),
            ),
        ),
    )
    assert _problems(forged) == _BLOCKED


# ── 음성: 대상이 아니다 ─────────────────────────────────────────────


def test_N6_해석_등급은_원문에_그대로_있어도_막힌다():
    candidate = _candidate(
        "가나다전자는 2019년 이후 사업을 넓힌 것으로 볼 수 있다.",
        ("가나다전자는 2019년 이후 사업을 넓혔다.",),
        claim_type=INTERPRETATION_CLAIM_TYPE,
    )
    problems = _problems(candidate)
    assert _LABELS_MISSING in problems and _BINDING_MISSING in problems


@pytest.mark.parametrize(
    "structured",
    [
        {"raw_value": "2019"},
        {"numeric_checks": ("numeric-binding-v1:x",)},
        {"metric": "설립연도"},
        {"display_value": "2019년"},
        {"calculation": "x"},
    ],
    ids=("raw_value", "numeric_checks", "metric", "display_value", "calculation"),
)
def test_N7_구조화_수치_칸이_있는_사실은_이_예외를_타지_않는다(structured):
    candidate = _candidate("가나다전자는 2019년 설립됐다.", ("가나다전자는 2019년 설립됐다.",))
    fact = replace(candidate.facts[0], **structured)
    assert _official_prose_cited_texts(fact, _sources(candidate)) is None


def test_N14_뉴스와_공식_출처를_섞어_인용하면_막힌다():
    candidate = _candidate(
        "가나다전자는 2019년 설립됐고 12개 사업장을 둔다.",
        ("가나다전자는 2019년 설립됐다.", "국내외 12개 사업장을 운영한다."),
        source_kinds=("filing", "news"),
    )
    assert _problems(candidate) == _BLOCKED


@pytest.mark.parametrize(
    ("field", "value", "extra"),
    [
        ("evidence_binding_valid", False, "의 원문·주장 결속 지문이 유효하지 않습니다"),
        ("verification_state", "unverified", ""),
    ],
    ids=("결속지문무효", "미검증"),
)
def test_N15_결속_지문이_무효거나_미검증이면_막힌다(field, value, extra):
    candidate = _candidate("가나다전자는 2019년 설립됐다.", ("가나다전자는 2019년 설립됐다.",))
    forged = replace(candidate, facts=(replace(candidate.facts[0], **{field: value}),))
    problems = _problems(forged)
    assert _LABELS_MISSING in problems and _BINDING_MISSING in problems
    if extra:
        assert f"{_FACT_ID}{extra}" in problems


def test_공식_문서_수에_들지_않는_출처는_대상이_아니다():
    candidate = _candidate("가나다전자는 2019년 설립됐다.", ("가나다전자는 2019년 설립됐다.",))
    source = replace(candidate.sources[0], counts_toward_document_floor=False)
    assert _problems(replace(candidate, sources=(source,))) == _BLOCKED


# ── 범위: FULL(v3) 계약에서만 연다 ──────────────────────────────────


@pytest.mark.parametrize(
    "version",
    [LEGACY_STRICT_QUALITY_CONTRACT_VERSION, QUALITY_CONTRACT_VERSION],
    ids=("ENFORCE_NO_PARTIAL_v2", "SHADOW_v1"),
)
def test_FULL이_아닌_계약은_예전처럼_막는다(version: str):
    candidate = _candidate("가나다전자는 2019년 설립됐다.", ("가나다전자는 2019년 설립됐다.",))
    assert _problems(candidate, version) == _BLOCKED
    assert _problems(candidate, STRICT_QUALITY_CONTRACT_VERSION) == ()


def test_숫자_없는_산문은_예전과_같다():
    candidate = _candidate("가나다전자는 공식 자료를 공개했다.", ("가나다전자는 공식 자료를 공개했다.",))
    assert _problems(candidate) == ()


# ── 뉴스 규칙 불변 ─────────────────────────────────────────────────


def test_N18_뉴스_출처는_뉴스_규칙이_먼저고_공식_예외를_타지_않는다():
    candidate = _candidate(
        "가나다전자는 2019년 설립됐다.",
        ("가나다전자는 2019년 설립됐다.",),
        source_kinds=("news",),
    )
    assert _official_prose_cited_texts(candidate.facts[0], _sources(candidate)) is None
    problems = _problems(candidate)
    # 뉴스 판정이 수치 검사를 대신하므로 두 수치 문구는 없고 뉴스 문구가 남는다.
    assert _LABELS_MISSING not in problems and _BINDING_MISSING not in problems
    assert any(problem.startswith(f"{_FACT_ID}: 뉴스 산문") for problem in problems)
