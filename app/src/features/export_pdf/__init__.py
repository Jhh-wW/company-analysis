"""PDF 보고서 내보내기. 공개 편의 API는 실제 접근할 때만 무거운 모듈을 읽는다."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "PDFGenerationError",
    "build_ascii_filename",
    "build_content_disposition",
    "build_download_filename",
    "build_pdf",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module("src.features.export_pdf.logic"), name)
    globals()[name] = value
    return value
