"""출처 feature의 정본 표시 함수를 보고서 표시 조립부에 연결한다."""

from collections.abc import Iterable


def is_news_source_kind(source: object) -> bool:
    from src.features.provenance.sources import SourceKind

    return getattr(source, "kind", None) is SourceKind.NEWS


def source_label_display(source: object) -> str:
    from src.features.provenance.sources import source_label_display as display

    return display(source)


def source_status_display(source: object) -> str:
    from src.features.provenance.sources import source_status_display as display

    return display(source)


def visible_citations(sources: Iterable[object]) -> list:
    from src.features.provenance.sources import visible_citations as visible

    return visible(sources)
