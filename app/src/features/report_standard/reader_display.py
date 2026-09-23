"""원장과 출고 판정을 바꾸지 않는 공개 표시 파생값."""

from __future__ import annotations

import re

from src.core.citations import citation_number, reader_location_display
from src.core.source_display_adapter import (
    is_news_source_kind, source_label_display, source_status_display, visible_citations,
)
from src.features.report_standard.reader_display_constants import (
    COLLECTION_SOURCE_LABELS, LEGACY_SECTION_NOTICES, PARTIAL_REPORT_NOTE,
    SOURCE_STATUS_LABELS, VERIFICATION_LABELS,
)
from src.features.report_standard.constants import SECTION_BY_ID
from src.features.report_standard.section_content import source_verification_label
from src.shared.report_generation.public_projection import PublicCitationGroup
from src.shared.report_quality.source_identity import canonical_url, document_identity


def citation_groups(report: object) -> tuple[PublicCitationGroup, ...]:
    """같은 문서 지문·발표일인 뉴스만 묶는다. 원장 번호는 모두 보존한다."""
    grouped: dict[tuple[object, ...], list[object]] = {}
    seen: set[int] = set()
    for source in visible_citations(getattr(report, "citations", ())):
        if source.number in seen:
            continue
        seen.add(source.number)
        url = canonical_url(source.url)
        digest = source.document_content_sha256
        is_news = is_news_source_kind(source) or source.source_type in {"언론 보도", "외부 보도"}
        key = ("news", document_identity(source), url, digest, source.published_at) if is_news and url and digest else ("source", source.number)
        grouped.setdefault(key, []).append(source)
    rows = []
    for sources in grouped.values():
        source = sources[0]
        labels = list(dict.fromkeys(source_verification_label(report, item.source_id) for item in sources))
        verification = " · ".join(VERIFICATION_LABELS.get(label, label) for label in labels)
        status = source_status_display(source)
        if is_news_source_kind(source) or source.source_type in {"언론 보도", "외부 보도"}:
            # 매체는 자료 제목에, 기사 성격은 날짜 뒤 '보도'에 이미 담겼다.
            status = f"{source.published_at} 보도" if source.published_at else status
        elif source.disclosed_at and source.fact_status == "공시 실제값":
            status = f"{source.disclosed_at} 공시 · 원문 수치"
        else:
            for before, after in SOURCE_STATUS_LABELS.items():
                status = status.replace(before, after)
        locations = tuple(dict.fromkeys(reader_location_display(item.location.strip()) or "—" for item in sources))
        used = tuple(dict.fromkeys(SECTION_BY_ID[section].display_number if section in SECTION_BY_ID else str(section) for item in sources for section in item.used_in))
        rows.append(PublicCitationGroup(
            numbers=tuple(item.number for item in sources), label_display=source_label_display(source),
            url=source.url, status_display=status, verification_label=verification,
            location=" · ".join(locations), used_in_display=" · ".join(f"{value}장" for value in used) or "—",
        ))
    return tuple(rows)


def reader_notes(report: object) -> tuple[str, ...]:
    """SHADOW 관측값을 출고 권한으로 해석하지 않고 자료 범위만 알린다."""
    notes = []
    grade = getattr(report, "grade", "")
    if getattr(grade, "value", grade) in {"부분 완성", "미완성"}:
        notes.append(PARTIAL_REPORT_NOTE)
    failed = tuple(dict.fromkeys(
        COLLECTION_SOURCE_LABELS[source.name]
        for source in getattr(report, "sources", ())
        if source.state == "failed" and source.name in COLLECTION_SOURCE_LABELS
    ))
    if failed:
        notes.append(f"수집 범위: {'·'.join(failed)} 수집을 끝까지 완료하지 못했습니다. 확보한 자료만 반영했으며, 해당 자료 전체가 없다는 뜻은 아닙니다.")
    return tuple(notes)


def audit_notes(section: object, text: str = "") -> tuple[str, ...]:
    """연도·출처를 인용한 본문에 typed 재무표의 감사 상태를 붙인다."""
    if not text:
        text = " ".join(getattr(section, "prose_paragraphs", ()) or (line for line, _ in getattr(section, "prose_lines", ())))
    cited = set(re.findall(r"\[(\d+)\]", text))
    notes = []
    for table in getattr(section, "tables", ()):
        years = tuple(year for year in getattr(table, "unaudited_years", ()) if str(year) in text)
        number = citation_number(getattr(table, "cite", ""))
        if years and number and number in cited:
            notes.append(f"자료 주석: {'·'.join(years)}년 수치는 감사받지 않은 비교 재무제표에 기재된 값입니다. [{number}]")
    return tuple(dict.fromkeys(notes))


def summary_notes(report: object) -> tuple[tuple[str, str], ...]:
    sections = {section.cell: section for section in getattr(report, "sections", ())}
    return tuple(
        (f"{index:02d}", " ".join(audit_notes(sections[item.section_id], item.text)))
        for index, item in enumerate(getattr(report, "summary_items", ()), 1)
        if item.section_id in sections and audit_notes(sections[item.section_id], item.text)
    )


def section_display_content(section: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """구형 무봉인 자료의 알려진 안내문만 사실 문단에서 분리한다."""
    paragraphs = tuple(getattr(section, "prose_paragraphs", ()))
    guidance = list(getattr(section, "guidance_lines", ()))
    if not paragraphs:
        facts = []
        for text, cite in getattr(section, "prose_lines", ()):
            if not cite and text in LEGACY_SECTION_NOTICES:
                guidance.append(text)
            else:
                facts.append(text)
        paragraphs = (" ".join(facts),) if facts else ()
    facts = []
    for text in paragraphs:
        if text in LEGACY_SECTION_NOTICES:
            guidance.append(text)
        else:
            facts.append(text)
    guidance.extend(audit_notes(section))
    return tuple(facts), tuple(dict.fromkeys(guidance))
