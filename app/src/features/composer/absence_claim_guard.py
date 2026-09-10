"""공식 자료에 «없다»고 단언한 문장을 공개 후보에서 뺀다 — 장과 무관하다.

작성부는 «공식 자료 전체»를 본 적이 없다. 수집된 조각만 본다. 게다가 수집
자체가 실패한 갈래(홈페이지 오류·공식 IR 문서 시도 0건)가 있는 실행에서도
작가는 「공식 자료에서 찾을 수 없다」라고 쓸 수 있다. 그 문장은 검증 가능한
주장이 아니라 «작성부가 알 수 없는 사실»이다.

★ 이 가드는 «인용이 없는 문장»에서 가장 필요하다. 의미 검수는 대조할 외부
  자료가 없다는 이유로 인용 없는 문장을 통째로 건너뛴다 — 부재 단언은 바로
  그 자리에서 가장 잘 통과한다. 그래서 호출자는 인용 유무와 «무관하게» 이
  검사를 먼저 돌려야 한다.

★ 저장소에 이미 옳은 방식이 있다 — 부재는 확인 범위 «안내문»이 말하고
  (「자료가 없다는 뜻은 아닙니다」) 문장은 말하지 않는다. 이 가드는 그 경계를
  코드로 굳힌다.
"""

from typing import Final
import unicodedata

from src.features.composer.absence_claim_constants import (
    ABSENCE_CLAIM_UNSUPPORTED,
    ABSENCE_CLAUSE_SPLIT_RE,
    ABSENCE_PREDICATE_RE,
    ABSENCE_SCOPE_GUIDANCE_NOTICE,
    SOURCE_REFERENT_RE,
)

__all__: Final[tuple[str, ...]] = (
    "absence_claim_problem",
    "with_absence_scope_guidance",
)


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def absence_claim_problem(text: str) -> str:
    """자료를 가리키며 «없다»고 단언한 절이 있으면 고정 사유를 반환한다.

    판정 경계는 «같은 절»이다 — 앞 절의 「공시자료에 따르면」과 뒤 절의
    「…하지 않는다」가 우연히 만나 걸리지 않게 한다. 회사명·업종·장·인용
    id·글자수는 조건이 아니다.

    ★ 회사가 스스로 「당사 해당사항 없습니다」라고 «밝힌» 인용 문장은 걸리지
      않는다 — 지시어를 «공식 자료/공시/사업보고서/원문» 쪽으로 좁혔기
      때문이다. 「당사」·「회사」는 지시어로 세지 않는다.
    ★ 빈 문자열은 그 문장이 옳다는 뜻이 아니다. 주어·시점·근거 결속은 기존
      의미 검수가 그대로 판정한다.
    """

    for clause in ABSENCE_CLAUSE_SPLIT_RE.split(text):
        surface_clause = _surface(clause)
        if not surface_clause:
            continue
        if (SOURCE_REFERENT_RE.search(surface_clause)
                and ABSENCE_PREDICATE_RE.search(surface_clause)):
            return ABSENCE_CLAIM_UNSUPPORTED
    return ""


def with_absence_scope_guidance(notice: str) -> str:
    """이 가드가 문장을 뺀 장의 안내문에 확인 범위 한 줄을 «한 번만» 붙인다.

    ★ 왜 문장 대신 안내문인가 — 작성부는 «공식 자료 전체»를 본 적이 없어서
      「없다」를 말할 자격이 없다. 그렇다고 아무 말도 안 하면 독자는 그 장이
      왜 그렇게 생겼는지 알 수 없다. 그래서 «범위»만 말한다.

    ★ 이미 붙어 있으면 다시 붙이지 않는다 — 한 장에서 여러 문장이 걸려도
      안내문은 한 줄이다. 기존 안내문(예: 「검증을 통과하지 못해 싣지
      않았습니다」)이 있으면 그 뒤에 이어 붙인다. 기존 문구는 지우지 않는다.

    ⚠️ 지운 문장의 주제어를 넣지 않는다 — 그 주제가 실제로 없는지는 작성부가
      모르기 때문이다(지어냄 방지).
    """

    if ABSENCE_SCOPE_GUIDANCE_NOTICE in notice:
        return notice
    stripped = notice.strip()
    if not stripped:
        return ABSENCE_SCOPE_GUIDANCE_NOTICE
    return f"{stripped} {ABSENCE_SCOPE_GUIDANCE_NOTICE}"
