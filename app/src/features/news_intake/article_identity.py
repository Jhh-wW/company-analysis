"""원문 안의 전체 법인명(명칭) 선언을 그 기사 인용의 문맥에만 결속한다."""

from dataclasses import replace
import re

from src.features.news_intake import article_identity_constants as c
from src.features.news_intake.identity_names import company_query_names
from src.features.news_intake.models import NewsCompanyContext
from src.features.news_intake.select import normalize_company_name


def article_company_context(body: str, company: NewsCompanyContext) -> NewsCompanyContext:
    """기사 인용 검수에만 쓰는 임시 복사본이며 공식 명칭 목록은 바꾸지 않는다.

    이 복사본을 공식 별칭 저장·회사 prompt 메타·다른 기사 입력에 전달하면 안 된다.
    호출자는 원래 company를 계속 사용하며 같은 기사 body의 닫힌 명칭 선언만 읽는다.
    법인 신원·모델 주어·사실·날짜 판정의 승인을 대신하지 않는다.
    """
    names = company_query_names(company)
    official = {normalize_company_name(name) for name in names}
    declarations = []
    for match in c.ARTICLE_ALIAS_DECLARATION_RE.finditer(body):
        full, alias = match.group("full").strip(), match.group("alias").strip()
        if (not c.ARTICLE_ALIAS_MIN_CHARS <= len(alias) <= c.ARTICLE_ALIAS_MAX_CHARS
                or c.ARTICLE_ALIAS_NAME_RE.fullmatch(alias) is None
                or c.ARTICLE_ALIAS_ROLE_RE.search(alias)):
            continue
        declarations.append((normalize_company_name(full), alias, match.span("alias")))
    aliases = []
    targets = []
    for name in names:
        exact_name = r"(?<!\w)" + r"\s*".join(re.escape(char) for char in name if not char.isspace())
        for match in re.finditer(exact_name + r"\s*" + c.ARTICLE_ALIAS_DECLARATION_SUFFIX, body, re.I):
            targets.append((normalize_company_name(name), match.group("alias").strip(), match.span("alias")))
    for full, alias, span in targets:
        key = normalize_company_name(alias)
        if (not c.ARTICLE_ALIAS_MIN_CHARS <= len(alias) <= c.ARTICLE_ALIAS_MAX_CHARS
                or c.ARTICLE_ALIAS_NAME_RE.fullmatch(alias) is None
                or c.ARTICLE_ALIAS_ROLE_RE.search(alias) or key in official):
            continue
        if any(other_span != span and other != full and normalize_company_name(value) == key
               for other, value, other_span in declarations):
            continue
        if (re.search(c.ARTICLE_ALIAS_FOREIGN_ROLE_RE.format(name=re.escape(alias)), body, re.I)
                or re.search(c.ARTICLE_ALIAS_REVERSE_FOREIGN_ROLE_RE.format(name=re.escape(alias)), body, re.I)):
            continue
        aliases.append(alias)
    return replace(company, aliases=tuple(dict.fromkeys((*company.aliases, *aliases)))) if aliases else company
