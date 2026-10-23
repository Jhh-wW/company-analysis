"""검수 전 일반적인 자산·역량 추정 칸의 대조 범위."""
import re

BUSINESS_FLOW_ASSET_HEADER = "핵심 자산"
DRAFT_GENERIC_ASSET_HEADS = re.compile(r"시스템|인프라|역량")
