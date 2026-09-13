"""공식 사업부문 별칭과 공시 법인 연결 근거.

별칭은 회사명을 무조건 바꾸는 치환표가 아니다. 검색어가 등록된 별칭과
정확히 같고, 로컬 DART 색인에 지정한 법인 고유번호가 있을 때만 후보 검색의
추가 근거로 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from src.shared.company_identity import exact_company_name_key


OFFICIAL_ALIAS_MATCH_KIND: Final[str] = "official_alias"
OFFICIAL_ALIAS_SCORE_BONUS: Final[float] = 0.58
OFFICIAL_RELATION_DATA_VERSION: Final[str] = "2026-09-14"


@dataclass(frozen=True)
class OfficialAliasRelation:
    """사업부문 별칭과 확인된 DART 법인 관계 한 건."""

    aliases: tuple[str, ...]
    corp_code: str
    stock_code: str
    relation: str
    report_scope: str
    checked_on: str
    data_version: str
    source_urls: tuple[str, ...]


SK_AX_RELATION: Final[OfficialAliasRelation] = OfficialAliasRelation(
    aliases=("SK AX", "SKAX", "SK㈜ AX"),
    corp_code="00181712",
    stock_code="034730",
    relation="SK AX는 SK㈜의 CIC(Company-in-Company) 사업부문입니다.",
    report_scope="보고서는 SK㈜ 공시 기준으로 작성됩니다.",
    checked_on="2026-09-14",
    data_version=OFFICIAL_RELATION_DATA_VERSION,
    source_urls=(
        "https://www.sk-inc.com/kr/ir/faq.aspx",
        "https://www.skax.co.kr/company/news-room/3284",
    ),
)


OFFICIAL_ALIAS_RELATIONS: Final[tuple[OfficialAliasRelation, ...]] = (
    SK_AX_RELATION,
)

_RELATIONS_BY_ALIAS_KEY: Final[dict[str, OfficialAliasRelation]] = {
    exact_company_name_key(alias): relation
    for relation in OFFICIAL_ALIAS_RELATIONS
    for alias in relation.aliases
}


def official_alias_relation_for(query: object) -> OfficialAliasRelation | None:
    """정확히 등록된 공식 별칭이면 관계를, 아니면 ``None``을 돌려준다."""

    key = exact_company_name_key(query)
    return _RELATIONS_BY_ALIAS_KEY.get(key) if key else None
