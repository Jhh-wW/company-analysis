"""조건 0 공공기관 사업자번호 대조 단위 테스트."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))

from core.paths import PUBLIC_ORG_REGISTRY
from features.public_org.logic import (
    load_public_org_registry,
    match_public_org,
    normalize_bizno,
)


@pytest.fixture(scope="module")
def registry() -> dict[str, str]:
    return load_public_org_registry(PUBLIC_ORG_REGISTRY)


def test_명단_전체가_원본에서_옮겨졌다(registry):
    canonical = json.dumps(
        registry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(registry) == 355
    assert hashlib.sha256(canonical).hexdigest() == (
        "57af011b1b6a15d415ed9f1ed98ce5f874837d78c12bc51d081deb4ba072b047"
    )


def test_한국전력이_잡힌다(registry):
    assert "한국전력" in (match_public_org("120-82-00052", registry) or "")


def test_한국가스공사가_잡힌다(registry):
    assert "가스공사" in (match_public_org("120-82-00557", registry) or "")


def test_강원랜드가_잡힌다(registry):
    assert "강원랜드" in (match_public_org("225-81-10770", registry) or "")


def test_수자원공사가_잡힌다(registry):
    assert "수자원" in (match_public_org("306-82-00471", registry) or "")


def test_붙임표_없는_표기도_같은_결과(registry):
    with_dash = match_public_org("120-82-00052", registry)
    without_dash = match_public_org("1208200052", registry)
    assert with_dash == without_dash and with_dash is not None


def test_명단에_없는_번호는_통과(registry):
    assert match_public_org("0000000000", registry) is None


@pytest.mark.parametrize("bad", [None, "", "12345", "120-82", "가나다라마바사아자차"])
def test_대조_불가_값은_전부_None(registry, bad):
    assert match_public_org(bad, registry) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("120-82-00052", "1208200052"),
        ("1208200052", "1208200052"),
        (" 120 82 00052 ", "1208200052"),
        ("12-34", None),
        (None, None),
    ],
)
def test_사업자번호_정규화(raw, expected):
    assert normalize_bizno(raw) == expected


def _write_registry(tmp_path: Path, organizations: object) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "as_of_year": 2026,
                "source": {"name": "test"},
                "organizations": organizations,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "organizations",
    [
        [],
        [{"bizno": "123", "name": "가나다"}],
        [{"bizno": "1234567890", "name": ""}],
        [{"bizno": "1234567890", "name": "가나다", "extra": "금지"}],
        [
            {"bizno": "1234567890", "name": "가나다"},
            {"bizno": "1234567890", "name": "라마바"},
        ],
    ],
)
def test_깨진_명단은_조용히_통과하지_않는다(tmp_path, organizations):
    with pytest.raises(ValueError):
        load_public_org_registry(_write_registry(tmp_path, organizations))


def test_명단_파일이_없으면_즉시_실패한다(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_public_org_registry(tmp_path / "missing.json")


def test_잘못된_json은_즉시_실패한다(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        load_public_org_registry(path)
