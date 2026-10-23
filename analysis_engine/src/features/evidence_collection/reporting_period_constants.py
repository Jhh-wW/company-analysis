"""공시 원문의 명시 보고기간을 읽는 닫힌 형식."""

import re

CORP_CODE_RE = re.compile(r"[0-9]{8}")
DATE_VALUE_RE = re.compile(r"[0-9]{8}")
DATE_TEXT_RE = re.compile(r"([0-9]{4})\s*년\s*([0-9]{1,2})\s*월\s*([0-9]{1,2})\s*일")
REPORTING_PERIOD_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})/([0-9]{4}-[0-9]{2}-[0-9]{2})")
UNSAFE_XML_DECLARATION_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
