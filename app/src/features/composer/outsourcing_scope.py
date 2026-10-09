"""외주와 기재 생략을 근거로 직접 수행 부재를 추가한 관계만 제한한다."""

from collections.abc import Mapping
import unicodedata

from src.features.composer.outsourcing_scope_constants import (
    CLAUSE_RE, DIRECT_NEGATIVE_RE, DISCLOSURE_OMISSION_RE, OUTSOURCING_RE,
    OUTSOURCING_SCOPE_PROBLEM, SELF_SUBJECT_RE, SENTENCE_RE,
    TOTAL_OUTSOURCING_TAIL_RE, SUPPORT_PREFIX_RE,
)


def _normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _object(text: str, *, source_support: bool = False) -> str:
    subjects = list(SELF_SUBJECT_RE.finditer(text))
    if subjects:
        if source_support and (
            len(subjects) != 1 or SUPPORT_PREFIX_RE.fullmatch(text[:subjects[0].start()]) is None
        ):
            return ""
        text = text[subjects[-1].end():]
    return _normalized(text.strip())


def _negative_claims(text: str, *, source_support: bool = False):
    for clause in CLAUSE_RE.split(text):
        for match in DIRECT_NEGATIVE_RE.finditer(clause):
            target = _object(match["object"], source_support=source_support)
            if target:
                yield target, match["action"]


def _explicit_support(target: str, action: str, sources: Mapping[str, str]) -> bool:
    for source in sources.values():
        for sentence in SENTENCE_RE.split(unicodedata.normalize("NFKC", source)):
            if (target, action) in set(_negative_claims(sentence, source_support=True)):
                return True
            # 동일 대상에 바로 붙은 전체 외주만 읽는다. 다른 대상이나 다른 절의
            # 전량 표지를 빌리지 않으며, 상품·회사명 목록이나 유사도는 쓰지 않는다.
            for clause in CLAUSE_RE.split(sentence):
                compact = _object(clause, source_support=True)
                if compact.startswith(target) and TOTAL_OUTSOURCING_TAIL_RE.match(compact, len(target)):
                    return True
    return False


def outsourcing_scope_problem(candidate_text: str, sources: Mapping[str, str]) -> str:
    """같은 후보 문장의 외주 설명→명시 직접 수행 부재 확대만 찾는다.

    빈 사유는 전체 사실 승인이 아니다. 외주 언급 없는 부정, 대상의 동의어,
    전량 외주의 복잡한 범위는 기존 의미 검수에 남긴다. 원문은 수정하지 않는다.
    """
    if not any(
        OUTSOURCING_RE.search(sentence) and DISCLOSURE_OMISSION_RE.search(sentence)
        for source in sources.values()
        for sentence in SENTENCE_RE.split(unicodedata.normalize("NFKC", source))
    ):
        return ""
    for sentence in SENTENCE_RE.split(unicodedata.normalize("NFKC", candidate_text)):
        if not OUTSOURCING_RE.search(sentence):
            continue
        for target, action in _negative_claims(sentence):
            if not _explicit_support(target, action, sources):
                return OUTSOURCING_SCOPE_PROBLEM
    return ""
