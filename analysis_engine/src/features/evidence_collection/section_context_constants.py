"""원문 사업 범위 문맥의 닫힌 형식과 경계."""
import re

CONTEXT_VERSION = "source-section-context-v1"
MAX_HEADING_CHARS = 256
MAX_RETAINED_SCOPES = 4096
CONTEXT_KEYS = frozenset({
    "version", "document_id", "document_sha256", "text", "location",
    "text_sha256", "scope_location", "fragment_location", "fragment_sha256",
})
LOCATION_RE = re.compile(r"([0-9]{1,10})-([0-9]{1,10})")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
BRACKET_HEADING_RE = re.compile(r"(?m)^[ \t]*(\[[^\]\r\n]+\])")
BUSINESS_HEADING_RE = re.compile(r"\[[^\]\r\n]*(?:부문|사업)[^\]\r\n]*\]")
MAJOR_HEADING_RE = re.compile(r"(?m)^[ \t]*(?:[IVXⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.．][ \t]*|[0-9]+[.．][ \t]*)(?=[가-힣A-Za-z])")
