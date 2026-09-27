"""공시 표의 보수적 평문 정규화 상한."""

from typing import Final

MAX_TABLE_MARKUP_CHARS: Final[int] = 64 * 1024
MAX_TABLE_ROWS: Final[int] = 128
MAX_TABLE_COLUMNS: Final[int] = 32
MAX_TABLE_CELLS: Final[int] = 2_048
MAX_CELL_SPAN: Final[int] = 32
MAX_TABLES_PER_DOCUMENT: Final[int] = 4_096
MAX_REPLACEMENTS_PER_DOCUMENT: Final[int] = 1_024
MAX_REPLACEMENT_CHARS_PER_DOCUMENT: Final[int] = 4 * 1024 * 1024
CELL_LINE_BREAK_TAGS: Final[frozenset[str]] = frozenset({"br", "p", "div", "li"})
NUMERIC_JOIN_CHARS: Final[frozenset[str]] = frozenset({".", ",", "%", "+", "-", "−", "/"})
