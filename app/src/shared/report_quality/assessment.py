"""새 보고서 생성 시점에만 실행하는 품질·공개 안전 평가기."""

from __future__ import annotations

import re
from decimal import Decimal

from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.contract import contract_for_generation
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.dto import ClaimFact, ReportCandidate, SourceDocument
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityAssessment,
    QualityContract,
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
    SafetyAssessment,
    VerificationState,
)
from src.shared.report_quality.numeric_validation import validate_versioned_numeric_claim


_NUMBER_TOKEN = re.compile(r"\d")
# 숫자를 한글로 바꿔 쓰는 것만으로 NumericBinding 경계를 우회하지 못하게 한다.
# 이 문법은 값을 계산하거나 사실을 승인하는 검증기가 아니다. 수량 단위가 바로
# 붙은 표현만 찾아 «구조화 결속 필요»로 보내며, '이 회사'처럼 우연히 수사가
# 들어간 일반 문장은 잡지 않는다.
_KOREAN_NUMBER_END = (
    # 단위 뒤에 다른 낱말이 바로 이어지면 그 단위가 아니라 한 단어일 수 있다.
    # 조+건을 「조건」으로, 한+개를 「한 개념」의 앞부분으로 읽지 않는다.
    # 조사(은/는/으로 등)가 붙은 실제 수량 표현은 그대로 허용한다.
    r"(?=(?:$|[^가-힣]|"
    r"(?:입니다|이었다|이라는|이라고|이다|이며|이고|인|일|"
    r"은|는|이|가|을|를|에|에서|으로|로|과|와|의|도|만|씩|부터|까지|보다)"
    r"(?:$|[^가-힣])))"
)
_SINO_KOREAN_DIGITS = r"(?:영|공|일|이|삼|사|오|육|칠|팔|구)+"
_SINO_KOREAN_WITH_MAGNITUDE = (
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*"
    r"(?:십|백|천|만|억|조)"
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*"
)
# 붙여 쓴 단위 앞에서는 최소 한 자리값(십/백/천/만/억)이 있거나, 조 앞에
# 숫자말이 있어야 한다. 단독 ``조원``은 금액일 수도 있지만 '프로젝트 조원'과
# 구별할 수 없으므로 공백 없는 형태만으로는 수치라고 단정하지 않는다.
_SINO_KOREAN_SAFE_ATTACHED_MAGNITUDE = (
    r"(?:"
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*"
    r"(?:십|백|천|만|억)"
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*"
    r"|(?:영|공|일|이|삼|사|오|육|칠|팔|구)+조"
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*)"
)
_SINO_KOREAN_NUMBER_WITH_UNIT = re.compile(
    # 붙여 쓴 짧은 단위는 일반 단어와 형태가 겹친다(조건=조+건, 사원=사+원).
    # 일반 숫자말은 단위와 실제 공백이 있을 때만 인정한다. 공백 없이 붙은
    # 표현은 자리값을 포함해 수량임이 분명할 때만 인정한다.
    rf"(?<![가-힣])(?:"
    rf"(?:{_SINO_KOREAN_DIGITS}|{_SINO_KOREAN_WITH_MAGNITUDE})\s+"
    rf"(?:퍼센트|프로|배|원|달러|년|개월|분기|자릿수|개|건|명|회|곳|번째)"
    rf"|{_SINO_KOREAN_SAFE_ATTACHED_MAGNITUDE}\s*"
    rf"(?:퍼센트|프로|배|원|달러|년|개월|분기|자릿수))"
    rf"{_KOREAN_NUMBER_END}"
)
_NATIVE_KOREAN_NUMBER = (
    r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|마흔|쉰|예순|일흔|여든|아흔)"
)
_NATIVE_KOREAN_NUMBER_WITH_UNIT = re.compile(
    # 「한 번으로 끝나지 않는다」·「두 번째 이익축」은 양을 주장하는 값이
    # 아니라 관용적 횟수·순서다. 번/번째를 NumericBinding 게이트에 넣으면
    # 정상 기업 설명을 수치 오류로 삭제하므로 제외한다.
    # '세배'·'한배를 탔다'도 일반 단어/관용구이므로 배·자릿수는 공백을
    # 요구한다. 한달·한해처럼 그 자체가 기간인 표현만 붙여 쓰기를 허용한다.
    rf"(?<![가-힣])(?:{_NATIVE_KOREAN_NUMBER}\s*(?:해|달)"
    rf"|{_NATIVE_KOREAN_NUMBER}\s+(?:배|자릿수|개|건|명|곳))"
    rf"{_KOREAN_NUMBER_END}"
)
_QUANTITATIVE_WORD = re.compile(
    r"(?<![가-힣])(?:절반|반토막|두\s*배|몇\s*배|수십\s*퍼센트|수백\s*억|한\s*자릿수|두\s*자릿수)"
)


def has_public_numeric_token(text: str) -> bool:
    """공개 산문에 숫자·날짜·백분율의 바탕인 숫자 토큰이 있는가.

    이것은 문장에서 값을 역추출해 사실 장부를 만드는 함수가 아니다. 오직
    구조화 결속이 필요한 문장인지 보수적으로 분류한다. 따라서 ``2025년``·
    ``24.28%``·``제2공장``·``2025Q1``·``H100``뿐 아니라 ``두 배``·
    ``이십오 퍼센트``처럼 수량 단위가 붙은 한글 수도 참이다. 그 뜻은
    ``NumericBinding``만이 설명할 수 있다.
    """

    value = str(text or "")
    return any(
        pattern.search(value) is not None
        for pattern in (
            _NUMBER_TOKEN,
            _SINO_KOREAN_NUMBER_WITH_UNIT,
            _NATIVE_KOREAN_NUMBER_WITH_UNIT,
            _QUANTITATIVE_WORD,
        )
    )


def _fact_registry(candidate: ReportCandidate) -> tuple[dict[str, ClaimFact], list[str]]:
    registry: dict[str, ClaimFact] = {}
    problems: list[str] = []
    for fact in candidate.facts:
        fact_id = fact.fact_id.strip()
        if not fact_id:
            problems.append("빈 fact_id가 있습니다")
        elif fact_id in registry:
            problems.append(f"fact_id {fact_id}가 중복됐습니다")
        else:
            registry[fact_id] = fact
    return registry, problems


def _source_registry(
    candidate: ReportCandidate,
) -> tuple[dict[str, SourceDocument], list[str]]:
    registry: dict[str, SourceDocument] = {}
    problems: list[str] = []
    for source in candidate.sources:
        source_id = source.source_id.strip()
        identity = source.document_identity.strip()
        if not source_id or not identity:
            problems.append("출처의 source_id 또는 독립 문서 identity가 비었습니다")
        elif source_id in registry:
            problems.append(f"source_id {source_id}가 중복됐습니다")
        else:
            hashes = tuple(value.strip() for value in source.exact_evidence_hashes)
            if len(hashes) != len(set(hashes)) or any(
                re.fullmatch(r"[0-9a-f]{64}", value) is None for value in hashes
            ):
                problems.append(f"source_id {source_id}의 원문 조각 해시가 손상됐습니다")
            registry[source_id] = source
    return registry, problems


def _public_fact_ids(candidate: ReportCandidate) -> tuple[list[str], list[str]]:
    public: list[str] = []
    problems: list[str] = []
    section_ids: set[str] = set()
    owner_by_fact: dict[str, str] = {}
    for section in candidate.sections:
        section_id = section.section_id.strip()
        if not section_id:
            problems.append("빈 section_id가 있습니다")
            continue
        if section_id in section_ids:
            problems.append(f"section_id {section_id}가 중복됐습니다")
        section_ids.add(section_id)
        if section.has_unbound_public_content:
            problems.append(f"{section_id}장에 fact_id와 결속되지 않은 공개 내용이 있습니다")
        if section.notice_only and section.fact_ids:
            problems.append(f"{section_id}장이 안내문 전용인데 fact_id도 함께 있습니다")
        if len(section.fact_ids) != len(set(section.fact_ids)):
            problems.append(f"{section_id}장 안에서 fact_id가 중복됐습니다")
        for fact_id in section.fact_ids:
            normalized = str(fact_id or "").strip()
            if not normalized:
                problems.append(f"{section_id}장에 빈 fact_id가 있습니다")
                continue
            previous = owner_by_fact.get(normalized)
            if previous is not None and previous != section_id:
                problems.append(
                    f"fact_id {normalized}가 {previous}장과 {section_id}장에 중복 공개됐습니다"
                )
            else:
                owner_by_fact[normalized] = section_id
            public.append(normalized)
    return public, problems


def _has_numeric_payload(fact: ClaimFact) -> bool:
    return bool(
        fact.numeric_checks
        or fact.raw_value.strip()
        or fact.display_value.strip()
        or has_public_numeric_token(fact.claim)
    )


def assess_safety(
    candidate: ReportCandidate,
    contract: QualityContract,
) -> SafetyAssessment:
    """모든 공개 claim이 원문·상태·수치 결속을 가졌는지 판정한다."""

    facts, fact_problems = _fact_registry(candidate)
    sources, source_problems = _source_registry(candidate)
    public_ids, public_problems = _public_fact_ids(candidate)
    problems = [*fact_problems, *source_problems, *public_problems]
    if not public_ids:
        problems.append("공개할 원자 claim이 없습니다")

    verified: list[str] = []
    unverified: list[str] = []
    rejected: list[str] = []
    claim_owners: dict[tuple[str, str], str] = {}
    public_set = set(public_ids)
    if candidate.has_unbound_summary_content:
        problems.append("요약에 본문 fact_id와 결속되지 않은 공개 내용이 있습니다")
    if len(candidate.summary_fact_ids) != len(set(candidate.summary_fact_ids)):
        problems.append("요약 fact_id가 중복됐습니다")
    for summary_fact_id in candidate.summary_fact_ids:
        if summary_fact_id not in public_set:
            problems.append(
                f"요약 fact_id {summary_fact_id}가 검증 본문의 부분집합이 아닙니다"
            )

    for fact_id in dict.fromkeys(public_ids):
        fact = facts.get(fact_id)
        if fact is None:
            problems.append(f"공개 fact_id {fact_id}가 사실 장부에 없습니다")
            continue
        owner_sections = {
            section.section_id
            for section in candidate.sections
            if fact_id in section.fact_ids
        }
        if owner_sections != {fact.section_owner}:
            problems.append(f"{fact_id}의 소유 장과 공개 장이 일치하지 않습니다")
        claim_slot = fact.claim_slot.strip()
        if not claim_slot:
            problems.append(f"{fact_id}의 계획된 claim slot이 비었습니다")
        elif claim_slot not in CLAIM_SLOTS_BY_SECTION.get(fact.section_owner, ()):
            problems.append(
                f"{fact_id}의 claim slot이 {fact.section_owner}장 정책에 없습니다"
            )
        else:
            # claim_slot은 고유 번호가 아니라 범주다. 8~12문장 장이 5개 범주를
            # 쓰므로 같은 범주의 서로 다른 원자 사실을 거절하면 생성 계약과
            # 품질 계약을 동시에 만족할 수 없다. 대신 같은 장에서 같은 주장을
            # 공백·대소문자만 바꿔 여러 fact_id로 부풀리는 일을 막는다.
            normalized_claim = " ".join(fact.claim.split()).casefold()
            if not normalized_claim:
                problems.append(f"{fact_id}의 공개 claim이 비었습니다")
                normalized_claim = f"__empty__:{fact_id}"
            claim_key = (fact.section_owner, normalized_claim)
            previous = claim_owners.get(claim_key)
            if previous is not None and previous != fact_id:
                problems.append(
                    f"{fact.section_owner}장의 같은 원자 claim을 "
                    f"{previous}와 {fact_id}가 중복 공개했습니다"
                )
            else:
                claim_owners[claim_key] = fact_id
        if not fact.evidence_binding_valid:
            problems.append(f"{fact_id}의 원문·주장 결속 지문이 유효하지 않습니다")

        try:
            state = VerificationState(fact.verification_state)
        except ValueError:
            state = VerificationState.UNVERIFIED
            problems.append(f"{fact_id}의 검증 상태를 알 수 없습니다")
        if state is VerificationState.VERIFIED:
            verified.append(fact_id)
        elif state is VerificationState.REJECTED:
            rejected.append(fact_id)
        else:
            unverified.append(fact_id)

        source = sources.get(fact.source_id)
        if source is None:
            problems.append(f"{fact_id}가 존재하지 않는 source_id를 참조합니다")
        elif source.document_identity != fact.source_identity:
            problems.append(f"{fact_id}의 독립 문서 identity가 출처 장부와 다릅니다")

        supporting = (
            fact.supporting_source_ids,
            fact.supporting_source_identities,
            fact.supporting_evidence_hashes,
        )
        # v1은 발급 중인 레거시 생성 경로와의 호환을 지킨다. 반면 FULL은
        # source_id/URL만으로 원문을 승인하지 않는다. 모든 공개 fact가 정확한
        # 원문 조각 해시까지 세 열로 운반해야 같은 주소의 내용 교체도 잡힌다.
        if (
            contract.version == STRICT_QUALITY_CONTRACT_VERSION
            and not all(supporting)
        ):
            problems.append(f"{fact_id}의 정확한 원문 조각 결속이 비었습니다")
        if any(supporting):
            lengths = {len(values) for values in supporting}
            if lengths != {len(fact.supporting_source_ids)} or not fact.supporting_source_ids:
                problems.append(f"{fact_id}의 다중 출처 결속 열 길이가 다릅니다")
            elif len(fact.supporting_source_ids) != len(
                set(fact.supporting_source_ids)
            ):
                problems.append(f"{fact_id}의 다중 출처 source_id가 중복됐습니다")
            else:
                if (
                    fact.supporting_source_ids[0] != fact.source_id
                    or fact.supporting_source_identities[0] != fact.source_identity
                ):
                    problems.append(f"{fact_id}의 대표 출처가 다중 출처 첫 항목과 다릅니다")
                for source_id, identity, evidence_hash in zip(*supporting):
                    bound_source = sources.get(source_id)
                    if bound_source is None:
                        problems.append(
                            f"{fact_id}가 존재하지 않는 보조 source_id {source_id}를 참조합니다"
                        )
                        continue
                    if bound_source.document_identity != identity:
                        problems.append(
                            f"{fact_id}의 보조 출처 {source_id} 문서 identity가 다릅니다"
                        )
                    if evidence_hash not in bound_source.exact_evidence_hashes:
                        problems.append(
                            f"{fact_id}의 보조 출처 {source_id} 원문 조각 해시가 다릅니다"
                        )

        if _has_numeric_payload(fact):
            numeric_labels = (
                fact.metric,
                fact.period_start,
                fact.period_end,
                fact.sign,
                fact.unit,
                fact.unit_dimension,
                fact.formula,
            )
            if not all(str(value).strip() for value in numeric_labels):
                problems.append(f"{fact_id}의 구조화 수치 이름표가 비었습니다")
            numeric_problems = validate_versioned_numeric_claim(fact)
            if numeric_problems is None:
                problems.append(f"{fact_id}의 수치에 versioned NumericBinding이 없습니다")
            else:
                problems.extend(
                    f"{fact_id}: {problem}" for problem in numeric_problems
                )

    if unverified:
        problems.append("검증하지 못한 공개 claim이 있습니다")
    if rejected:
        problems.append("거절된 claim이 공개 후보에 남아 있습니다")
    unique_problems = tuple(dict.fromkeys(problems))
    return SafetyAssessment(
        contract_version=contract.version,
        decision=(
            ReleaseDecision.BLOCKED
            if unique_problems
            else ReleaseDecision.RELEASE_ALLOWED
        ),
        verified_fact_ids=tuple(verified),
        unverified_fact_ids=tuple(unverified),
        rejected_fact_ids=tuple(rejected),
        problems=unique_problems,
    )


def assess_quality(
    candidate: ReportCandidate,
    contract: QualityContract,
) -> QualityAssessment:
    """장별 coverage·전역 하한·독립 문서 수로 충분성을 판정한다."""

    facts, _ = _fact_registry(candidate)
    sources, _ = _source_registry(candidate)
    by_section = {section.section_id: section for section in candidate.sections}
    section_counts: list[tuple[str, int]] = []
    public_sentence_counts: list[tuple[str, int]] = []
    public_fact_ids: list[str] = []
    notice_only: list[str] = []
    one_claim: list[str] = []
    for section_id in contract.required_section_ids:
        section = by_section.get(section_id)
        fact_ids = tuple(dict.fromkeys(section.fact_ids)) if section is not None else ()
        claim_slots = {
            facts[fact_id].claim_slot.strip()
            for fact_id in fact_ids
            if fact_id in facts and facts[fact_id].claim_slot.strip()
        }
        count = len(claim_slots)
        section_counts.append((section_id, count))
        public_count = (
            count
            if section is None or section.public_sentence_count is None
            else max(0, int(section.public_sentence_count))
        )
        public_sentence_counts.append((section_id, public_count))
        public_fact_ids.extend(fact_id for fact_id in fact_ids if fact_id in facts)
        if section is None or section.notice_only or public_count == 0:
            notice_only.append(section_id)
        elif public_count == 1:
            one_claim.append(section_id)

    # 조건부 9장이 있으면 전체 실질 claim·검증 비율에는 포함하되, 미존재 자체는
    # COMPLETE 하한 위반으로 세지 않는다.
    for section in candidate.sections:
        if section.section_id in contract.required_section_ids:
            continue
        public_fact_ids.extend(
            fact_id for fact_id in dict.fromkeys(section.fact_ids) if fact_id in facts
        )

    unique_public_ids = tuple(dict.fromkeys(public_fact_ids))
    substantive = len(unique_public_ids)
    verified = sum(
        1
        for fact_id in unique_public_ids
        if facts[fact_id].verification_state == VerificationState.VERIFIED.value
    )
    ratio = (
        Decimal(verified) / Decimal(substantive)
        if substantive
        else Decimal(0)
    )
    # URL만 다른 복제 페이지 여덟 개를 독립 문서 여덟 건으로 세면 출처 하한을
    # 쉽게 부풀릴 수 있다. 정확한 근거 바이트 묶음이 같으면 신원이 달라도 한
    # 문서로 센다. 아직 해시 계약이 없는 레거시 호출자만 identity로 폴백한다.
    document_evidence_keys: set[tuple[str, ...]] = set()
    for fact_id in unique_public_ids:
        fact = facts[fact_id]
        source_ids = fact.supporting_source_ids or (fact.source_id,)
        for source_id in source_ids:
            source = sources.get(source_id)
            if source is not None and source.document_identity:
                evidence_hashes = tuple(
                    sorted(
                        dict.fromkeys(
                            value.strip()
                            for value in source.exact_evidence_hashes
                            if value.strip()
                        )
                    )
                )
                document_evidence_keys.add(
                    ("evidence", *evidence_hashes)
                    if evidence_hashes
                    else ("identity", source.document_identity)
                )

    shortfalls: list[str] = []
    problem_codes: list[QualityProblemCode] = []
    if len(notice_only) > contract.max_notice_only_sections:
        problem_codes.append(QualityProblemCode.TOO_MANY_NOTICE_ONLY_SECTIONS)
        shortfalls.append(
            f"안내문 전용 장이 {len(notice_only)}개로 허용 {contract.max_notice_only_sections}개를 넘었습니다"
        )
    if one_claim:
        problem_codes.append(QualityProblemCode.ONE_CLAIM_SECTIONS)
        shortfalls.append(
            "실질 claim이 한 건뿐인 장이 있습니다: " + ", ".join(one_claim)
        )
    low_semantic_coverage = [
        section_id
        for section_id, count in section_counts
        if 0 < count < contract.min_claims_per_covered_section
    ]
    if low_semantic_coverage:
        problem_codes.append(QualityProblemCode.LOW_SEMANTIC_COVERAGE)
        shortfalls.append(
            "서로 다른 의미 claim 범주가 부족한 장이 있습니다: "
            + ", ".join(low_semantic_coverage)
        )
    low_coverage = [
        section_id
        for section_id, count in public_sentence_counts
        if count < contract.min_claims_per_covered_section
    ]
    if low_coverage:
        problem_codes.append(QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE)
    if low_coverage and not one_claim:
        shortfalls.append(
            "장별 최소 claim coverage를 충족하지 못했습니다: "
            + ", ".join(low_coverage)
        )
    if substantive < contract.min_substantive_claims:
        problem_codes.append(QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS)
        shortfalls.append(
            f"실질 claim이 {substantive}건으로 하한 {contract.min_substantive_claims}건보다 적습니다"
        )
    if ratio < contract.min_verified_ratio:
        problem_codes.append(QualityProblemCode.LOW_VERIFIED_RATIO)
        shortfalls.append(
            f"검증 claim 비율이 {ratio:.2%}로 하한 {contract.min_verified_ratio:.0%}보다 낮습니다"
        )
    if len(document_evidence_keys) < contract.min_document_sources:
        problem_codes.append(QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES)
        shortfalls.append(
            f"독립 문서 출처가 {len(document_evidence_keys)}건으로 하한 {contract.min_document_sources}건보다 적습니다"
        )

    grade = (
        QualityGrade.INCOMPLETE
        if substantive == 0
        else QualityGrade.PARTIAL
        if shortfalls
        else QualityGrade.COMPLETE
    )
    return QualityAssessment(
        contract_version=contract.version,
        grade=grade,
        substantive_claims=substantive,
        verified_claims=verified,
        verified_ratio=ratio,
        document_sources=len(document_evidence_keys),
        notice_only_sections=tuple(notice_only),
        one_claim_sections=tuple(one_claim),
        section_claim_counts=tuple(section_counts),
        shortfall_reasons=tuple(shortfalls),
        section_public_sentence_counts=tuple(public_sentence_counts),
        underfilled_sections=tuple(low_coverage),
        problem_codes=tuple(dict.fromkeys(problem_codes)),
    )


def assess_generation(
    candidate: ReportCandidate,
    *,
    contract_version: str = "",
) -> GenerationAssessment:
    """새 보고서를 versioned 계약으로 평가한다. 과거 조회에는 사용하지 않는다."""

    contract = contract_for_generation(contract_version)
    quality = assess_quality(candidate, contract)
    safety = assess_safety(candidate, contract)
    publication_grade = (
        quality.grade
        if safety.decision is ReleaseDecision.RELEASE_ALLOWED
        else QualityGrade.INCOMPLETE
    )
    return GenerationAssessment(
        contract_version=contract.version,
        quality=quality,
        safety=safety,
        publication_grade=publication_grade,
    )
