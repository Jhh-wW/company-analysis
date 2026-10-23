"""자기 뉴스가 열거한 보상 주체를 보상 수령자로 바꾸지 못하게 한다."""

from collections.abc import Mapping

from src.features.composer import news_compensation_scope_constants as c
from src.features.composer.news_block import _is_news_fragment
from src.features.composer.port import CollectedFragment


def _roles(text: str, pattern) -> set[tuple[str, str]]:
    return {
        (match['actor'], ''.join(match['method'].split()))
        for unit in c.COMPENSATION_UNIT_RE.split(text)
        if c.COMPENSATION_ACTION_RE.search(unit)
        for match in pattern.finditer(unit)
    }


def news_compensation_scope_problem(
    text: str, sources: Mapping[str, str], fragments: Mapping[str, CollectedFragment],
) -> str:
    """자기 인용의 같은 주체·수단에서 명시된 역할이 뒤집힌 경우만 거절한다.

    새 주체나 수령자를 추론하지 않는다. 후보·인용·검수 문장을 고쳐 쓰지 않는다.
    """
    if not sources or any(fid not in fragments or not _is_news_fragment(fragments[fid])
                          for fid in sources):
        return ''
    recipients = _roles(text, c.COMPENSATION_RECIPIENT_RE)
    issuers = set().union(*(_roles(source, c.COMPENSATION_ISSUER_RE) for source in sources.values()))
    supported_recipients = set().union(*(_roles(source, c.COMPENSATION_RECIPIENT_RE)
                                        for source in sources.values()))
    return 'scope_condition_unbound' if (recipients & issuers) - supported_recipients else ''
