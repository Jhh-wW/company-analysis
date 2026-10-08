"""공식 산업 보조 탐색의 원문 표지. 회사 과제 승인 기준은 아니다."""

import re

DISCOVERY_REASON = "official_industry_discovery"
DISCOVERY_RETAINED_COUNT = 6
DISCOVERY_RETAINED_CHARS = 12_000
INDUSTRY_SCOPE_RE = re.compile(r"산업|시장|업계")
OBSERVED_CHANGE_RE = re.compile(r"감소|하락|둔화|위축|침체|부족|차질|지연|상승|급등|부담|피해|손실|악화|규제|경쟁\s*심화")
