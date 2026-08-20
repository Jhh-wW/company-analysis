"""조건 0 — 공공기관 제외 대조 (파이프라인 5번 판정 사다리의 1-0).

대조 키는 사업자등록번호로만 한다. 이름 대조나 부분 문자열 검색은
회사형태 표기와 유사 기관명 때문에 오판할 수 있다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

BIZNO_LENGTH = 10  # 사업자등록번호 자릿수
REGISTRY_SCHEMA_VERSION = 1


def normalize_bizno(raw: object) -> Optional[str]:
    """사업자등록번호에서 숫자만 남긴다.

    Args:
        raw: "120-82-00052" 같은 원본 값 (None·숫자형도 허용).

    Returns:
        10자리 숫자 문자열. 10자리가 아니면 None (대조 불가로 본다).
    """
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits if len(digits) == BIZNO_LENGTH else None


def load_public_org_registry(json_path: Path) -> dict[str, str]:
    """배포용 JSON 명단에서 {사업자번호: 기관명} 사전을 만든다.

    명단이 깨졌을 때 일부 기관만 조용히 통과시키지 않도록 형식, 번호, 이름,
    중복을 전부 확인하고 하나라도 이상하면 즉시 실패한다.
    """
    if not json_path.exists():
        raise FileNotFoundError(f"공공기관 명단 파일이 없습니다: {json_path}")

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("공공기관 명단 JSON을 읽을 수 없습니다") from exc

    if not isinstance(payload, dict):
        raise ValueError("공공기관 명단 최상위 값은 객체여야 합니다")
    if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError("지원하지 않는 공공기관 명단 형식입니다")
    if not isinstance(payload.get("as_of_year"), int):
        raise ValueError("공공기관 명단 기준 연도가 올바르지 않습니다")
    if not isinstance(payload.get("source"), dict):
        raise ValueError("공공기관 명단 출처 정보가 없습니다")

    organizations = payload.get("organizations")
    if not isinstance(organizations, list) or not organizations:
        raise ValueError("공공기관 명단 organizations는 비어 있지 않은 배열이어야 합니다")

    registry: dict[str, str] = {}
    for index, item in enumerate(organizations, start=1):
        if not isinstance(item, dict) or set(item) != {"bizno", "name"}:
            raise ValueError(f"공공기관 명단 {index}번째 항목 형식이 올바르지 않습니다")
        bizno = item["bizno"]
        name = item["name"]
        if not isinstance(bizno, str) or len(bizno) != BIZNO_LENGTH or not bizno.isdigit():
            raise ValueError(f"공공기관 명단 {index}번째 사업자번호가 올바르지 않습니다")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"공공기관 명단 {index}번째 기관명이 비어 있습니다")
        if bizno in registry:
            raise ValueError(f"공공기관 명단에 중복 사업자번호가 있습니다: {bizno}")
        registry[bizno] = name.strip()
    return registry


def match_public_org(bizno_raw: object, registry: dict[str, str]) -> Optional[str]:
    """사업자등록번호가 공공기관 명단에 있으면 기관명을, 없으면 None을 돌려준다.

    None = 「명단에 없음」이지 「공공기관이 아님을 보증」이 아니다 —
    공기업 자회사는 이 명단으로 못 거른다 (1차에서 안 거르기로 확정, 할일목록 1번).
    """
    bizno = normalize_bizno(bizno_raw)
    if bizno is None:
        return None
    return registry.get(bizno)
