"""최종 결속 뒤 첫 사업 소개가 없으면 해당 장에만 누락을 고지한다."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from src.features.composer.identity_content_constants import IDENTITY_SECTION_ID
from src.features.composer.identity_opening_policy_constants import (
    IDENTITY_OPENING_MISSING_NOTICE,
    IDENTITY_OPENING_NOTICE_BY_REASON,
    IDENTITY_OPENING_POLICY_NOTICES,
)
from src.features.composer.identity_opening_recovery import (
    BoundIdentityOpeningFact, has_bound_identity_opening,
)
from src.features.composer.port import ComposedReport


@dataclass(frozen=True)
class IdentityOpeningPolicyResult:
    report: ComposedReport
    missing: bool
    shortfall_reasons: tuple[str, ...]


def apply_identity_opening_policy(
    report: ComposedReport,
    bound_facts: Sequence[BoundIdentityOpeningFact],
    *,
    reason: str = "",
) -> IdentityOpeningPolicyResult:
    """입력은 최종 공개 결속 결과다. 등급·FULL 출고 결정은 호출자가 담당한다.

    사실이나 본문을 만들지 않으며 기존 장과 안내를 보존한다. 이후 단계에서
    유효한 첫 소개가 사라졌다면 다시 호출해 마지막 공개 상태를 판정한다.
    """

    if has_bound_identity_opening(report, bound_facts):
        return IdentityOpeningPolicyResult(report, False, ())
    notice = IDENTITY_OPENING_NOTICE_BY_REASON.get(reason, IDENTITY_OPENING_MISSING_NOTICE)
    sections = tuple(
        replace(section, notice="\n".join(filter(None, (section.notice, notice))))
        if section.section_id == IDENTITY_SECTION_ID
        and (section.sentences or not section.notice)
        and not any(existing in section.notice for existing in IDENTITY_OPENING_POLICY_NOTICES)
        else section
        for section in report.sections
    )
    updated = report if all(a is b for a, b in zip(sections, report.sections)) else replace(report, sections=sections)
    return IdentityOpeningPolicyResult(updated, True, (notice,))

