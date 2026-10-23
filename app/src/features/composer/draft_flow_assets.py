"""수익 원문에 없는 일반 자산 추정을 비우고 거래 행은 독립 검수로 보낸다."""
from collections.abc import Mapping
from dataclasses import replace

from src.features.composer.constants import BUSINESS_FLOW_HEADERS
from src.features.composer.draft_flow_asset_constants import (
    BUSINESS_FLOW_ASSET_HEADER, DRAFT_GENERIC_ASSET_HEADS,
)
from src.features.composer.port import CollectedFragment, FlowRow


def omit_unstated_draft_flow_asset(
    row: FlowRow, fragments: Mapping[str, CollectedFragment],
) -> FlowRow:
    """명시된 자산 개념이 없는 typed 자기 인용에만 적용하며 승인된 행은 보존한다."""
    if (row.review_binding is not None or len(row.cells) != len(BUSINESS_FLOW_HEADERS)
            or not row.citations or any(citation not in fragments for citation in row.citations)):
        return row
    sources = tuple(fragments[citation] for citation in row.citations)
    if any(not source.supported_claim_slots for source in sources):
        return row
    asset_index = BUSINESS_FLOW_HEADERS.index(BUSINESS_FLOW_ASSET_HEADER)
    asset = row.cells[asset_index]
    heads = set(DRAFT_GENERIC_ASSET_HEADS.findall(asset))
    if not heads or any(head in source.text for head in heads for source in sources):
        return row
    cells = tuple("" if index == asset_index else cell for index, cell in enumerate(row.cells))
    if not any(cells):
        return row
    return replace(row, cells=cells)
