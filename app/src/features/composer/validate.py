"""웹·PDF·composer가 공유하는 v2 출력 검사의 출처 DTO 어댑터."""

from src.features.provenance.sources import visible_citations
from src.core.citations import citation_number
from src.shared.report_quality.output_validation import V2ValidationError
from src.shared.report_quality.output_validation import v2_validation_problems as _problems


def v2_validation_problems(rendered):
    return _problems(rendered, visible_sources=tuple(visible_citations(rendered.citations)), citation_number=citation_number)


def validate_v2(rendered):
    problems = v2_validation_problems(rendered)
    if problems:
        raise V2ValidationError(problems)
