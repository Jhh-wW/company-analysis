# -*- coding: utf-8 -*-
"""«확인» 산문이 자기 인용 원문의 뒷받침 없이 공개되던 두 자리를 좁게 막는다.

둘 다 «그 문장이 스스로 단 인용»의 원문만 쓴다. 다른 인용의 원문으로 메우지 않는다.

① 자기 원문 지지가 계약 최소치에 못 미치는 «확인» 산문
   `build_verified_prose_fact` 는 근거어가 계약 최소치에 못 미치면 사실을 만들지
   않는다. 그런데 그 문장은 본문에도 요약에도 그대로 실렸다. 「사실로 결속하지
   못했다」와 「독자에게 확인으로 보인다」가 어긋나 있던 자리다.
   ★ 문턱을 새로 만들지 않는다 — 기존 계약값을 그대로 쓴다.

② 「…가 회사의 주요 수익원이다」를 «순수 회계 인식 설명»만으로 세운 경우
   자기 원문이 «언제·어떻게 수익으로 인식하는가»만 말하는데 후보가 그 수익을
   회사의 앞자리에 놓았을 때만 막는다.

⚠️ 이 검사가 «판정하지 않는» 것 — 정직하게 적는다
   · **순위의 참·거짓을 증명하지 않는다.** 어떤 언어에서도 그렇다.
   · 원문이 크기를 «조금이라도» 말하면(1%든 70%든, 다른 사업의 70%든,
     다른 대상의 명시적 「주요 수익원」이든) 이 검사는 **물러난다**. 그 셋을
     가려내는 일은 여기서 하지 않는다 — 대상 결속을 정직하게 증명할 수단이
     이 문자열 계층에 없기 때문이다. 낱말 하나가 겹친다는 것은 대상 결속의
     증명이 아니다.
   · 그래서 이 규칙의 참뜻은 좁다: **「인식 시점만 적힌 원문으로 앞자리를
     단정했다」는 한 모양**만 막는다.

⚠️ 규칙 ①이 «할 수 있는» 것도 적는다
   기존 최소 지지어 계약을 공개 단계에도 적용하므로, 지지어가 모자란 정상
   의역도 새로 빠질 수 있다. 「의역은 절대 안 지운다」고 단정하지 않는다.

이 파일이 하지 않는 것: 회사 이름·조각 번호 사용, 원문 전체 부분문자열 면제,
AI 호출 추가. 상수·정규식은 `prose_own_source_constants` 에 따로 둔다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence

from src.features.composer.prose_own_source_constants import (
    CLAUSE_SPLIT_RE,
    GENERIC_ORG_UNIT_HEAD_SUFFIXES,
    GENERIC_SUPPORT_STEMS,
    MIN_SUPPORT_STEM_CHARS,
    SUPPORT_TERM_TAIL_RE,
    PROSE_OWN_SOURCE_UNSUPPORTED,
    PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY,
    RECOGNITION_CLAUSE_RE,
    REVENUE_MAGNITUDE_MARKER_RE,
    REVENUE_PRIMACY_CLAIM_RE,
    REVENUE_WORD_RE,
    SUPPORT_TERM_LOG_LIMIT,
    SUPPORT_WORD_RE,
)
from src.shared.report_quality.constants import VERIFIED_PROSE_CLAIM_TYPE
from src.shared.report_quality.evidence_support import prose_evidence_support_ready

def _surface(value: object) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _support_stem(token: str) -> str:
    """근거어 하나에서 «끝에 붙은 조사·어미 한 벌»만 벗긴다.

    ★ 닫힌 목록으로 «한 번만» 벗긴다. 임의 접미사를 벗기지 않으므로
      「마케팅」이 「마케팅화」와 같은 낱말이 되지 않는다.
    ⚠️ 벗긴 결과가 최소 길이에 못 미치면 벗기지 않는다 — 조사 조각만 남은
      글자를 근거어로 세지 않기 위해서다.
    """

    stripped = SUPPORT_TERM_TAIL_RE.sub("", token, count=1)
    if len(stripped) < MIN_SUPPORT_STEM_CHARS:
        return token
    return stripped


def _is_generic_org_unit_stem(word: str) -> bool:
    """벗겨서 새로 얻은 낱말이 «조직단위 머리명사»로 끝나는가.

    ★ 「영업부문」처럼 수식어(「기업금융」/「개인금융」)가 공백으로 갈린 별도
      낱말이라 이 비교에 들어오지 않는 조직단위 복합어는, 머리명사만 같아도
      «어느 부문»인지 아무것도 뒷받침하지 않는다(실측 [14][240]).
    ⚠️ 특정 회사·부문 이름을 나열하지 않는다 — 구조(머리명사가 끝에 오는가)만
      본다. `GENERIC_ORG_UNIT_HEAD_SUFFIXES` 참고.
    """

    return any(word.endswith(suffix) for suffix in GENERIC_ORG_UNIT_HEAD_SUFFIXES)


def own_source_support_terms(claim: str, source_texts: Iterable[object]) -> list[str]:
    """주장과 «자기 인용 원문» 양쪽에 있는 낱말.

    ★ 결속(`prose_facts`)과 공개(이 파일)가 «같은» 계산을 보게 하려고 여기 한
      곳에만 둔다. 두 곳이 각자 세면 이번 결함이 다시 난다.
    ★ 글자 그대로 찾지 못하면 «양쪽에 같은 꼬리 규칙»을 적용한 낱말끼리
      맞춰 본다. 원문 「가람은 음반을 제작합니다」가 그대로 뒷받침하는
      「가람이 음반을 제작한다」가 근거어 1개로 세어져 사라지던 자리다(실측).
    ⚠️ 두 번째 단계는 «부분 문자열이 아니라 낱말 대 낱말»이다. 후보의 「제작」이
      원문의 「제작비」에 걸리지 않는다 — 꼬리를 뗀 뒤 서로 «같아야» 센다.
    ⚠️ 첫 단계(글자 그대로 포함)는 예전 그대로 둔다. 그래서 지금 세어지던 낱말은
      계속 세어지고, 이 변경으로 «새로 막히는» 문장은 없다.
    """

    evidence_text = " ".join(str(text or "") for text in source_texts).casefold()
    # 원문 쪽도 «같은» 꼬리 규칙으로 낱말을 만든다. 한쪽만 벗기면 비교가 성립하지 않는다.
    evidence_stems = {
        _support_stem(token.casefold())
        for token in SUPPORT_WORD_RE.findall(evidence_text)
    }
    out: list[str] = []
    for token in SUPPORT_WORD_RE.findall(str(claim or "")):
        normalized = token.casefold()
        if normalized not in evidence_text:
            normalized = _support_stem(normalized)
            if normalized in GENERIC_SUPPORT_STEMS:
                # 「회사는」→「회사」처럼 아무것도 뒷받침하지 않는 일반 주어는
                # 벗겨서 새로 얻었을 때 세지 않는다(실측 F3 이 이 자리로 샜다).
                continue
            if _is_generic_org_unit_stem(normalized):
                # 「영업부문은」→「영업부문」처럼 조직단위 머리명사만 같고
                # 수식어(부문 이름)가 다른 경우도 세지 않는다(실측 [14][240]).
                continue
            if normalized not in evidence_stems:
                continue
        if normalized not in out:
            out.append(normalized)
    return out[:SUPPORT_TERM_LOG_LIMIT]


def _unrecoverable(source_texts: Sequence[object]) -> bool:
    """원문을 복원하지 못한 인용이 섞여 있는가.

    복원 실패를 «0단어»로 세면 거짓 차단이 된다. 뉴스 정확 원문·API 응답처럼
    다른 자리에 보관된 근거가 있으므로, 그런 인용이 하나라도 있으면 판정하지 않는다.
    """

    return not source_texts or any(not str(text or "").strip() for text in source_texts)


def prose_own_source_problem(claim: str, own_sources: Mapping[str, str]) -> str:
    """«확인» 산문 하나를 자기 인용 원문에만 대조한다. 빈 문자열은 승인이 아니다.

    호출자는 등급이 «확인»이고 구조화 주장이 아닌 산문만 넘긴다 — 해석 등급과
    수치·뉴스는 각자 다른 결속 계약을 가진다.
    """

    source_texts = list(own_sources.values())
    if _unrecoverable(source_texts):
        return ""
    problem = _revenue_primacy_problem(claim, source_texts)
    if problem:
        return problem
    if not prose_evidence_support_ready(
        VERIFIED_PROSE_CLAIM_TYPE, own_source_support_terms(claim, source_texts)
    ):
        return PROSE_OWN_SOURCE_UNSUPPORTED
    return ""


def _revenue_primacy_problem(claim: str, source_texts: Sequence[object]) -> str:
    """자기 원문이 «인식 설명뿐»인데 앞자리를 단정했는가.

    ★ 크기를 조금이라도 말한 원문에는 판정하지 않는다(위 머리말의 한계).
      그래서 1%·70%·다른 사업의 70%·다른 대상의 명시적 「주요 수익원」은 모두
      이 검사를 지나간다. 그것들을 가려내는 일은 여기서 하지 않는다.
    """

    if not REVENUE_PRIMACY_CLAIM_RE.search(_surface(claim)):
        return ""
    recognition_seen = False
    for text in source_texts:
        for clause in CLAUSE_SPLIT_RE.split(str(text or "")):
            surface_clause = _surface(clause)
            if not surface_clause:
                continue
            if (REVENUE_MAGNITUDE_MARKER_RE.search(surface_clause)
                    and REVENUE_WORD_RE.search(surface_clause)):
                # 원문이 크기를 말한다 — 이 검사의 범위 밖이다.
                return ""
            if RECOGNITION_CLAUSE_RE.search(surface_clause):
                recognition_seen = True
    if not recognition_seen:
        # 인식 설명조차 아니면 이 좁은 모양이 아니다. 다른 검수에 남긴다.
        return ""
    return PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY


__all__ = [
    "own_source_support_terms",
    "prose_own_source_problem",
]
