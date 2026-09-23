"""새 기사 간 중복 비교가 인정하는 승인된 뉴스 주장·시간 상태."""

NEWS_DEDUPE_LEGACY_KINDS = frozenset({"news", "뉴스"})

# 수집 검증의 company_plan ↔ planned 결속을 유지한다. 모르는 어휘나
# 엇갈린 조합은 새 기사 간 삭제 권위를 갖지 않는다.
NEWS_DEDUPE_CLAIM_TIME_PAIRS = frozenset({
    ("company_plan", "planned"),
    ("company_statement", "completed"),
    ("company_statement", "ongoing"),
    ("reported_fact", "completed"),
    ("reported_fact", "ongoing"),
})
