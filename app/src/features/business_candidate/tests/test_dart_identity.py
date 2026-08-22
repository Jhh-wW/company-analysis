from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.features.business_candidate.dart_identity import (
    DartCompanyRecord,
    build_dart_company_index,
    generate_dart_company_matches,
    parse_dart_company_records,
)
from src.features.pipeline import real


_GOLD_PATH = Path(__file__).parent / "fixtures" / "dart_identity_gold.json"


def _gold():
    return json.loads(_GOLD_PATH.read_text(encoding="utf-8"))


def _records(payload=None):
    payload = payload or _gold()
    return tuple(DartCompanyRecord(**row) for row in payload["records"])


def test_official_corpcode_parser_keeps_all_five_raw_fields_and_frozen_identity(
    tmp_path: Path,
):
    xml_path = tmp_path / "CORPCODE.xml"
    xml_path.write_text(
        "<result><list><corp_code>00258689</corp_code>"
        "<corp_name>JYP Ent.</corp_name>"
        "<corp_eng_name>JYP Entertainment Corporation</corp_eng_name>"
        "<stock_code>035900</stock_code><modify_date>20221206</modify_date>"
        "</list></result>",
        encoding="utf-8",
    )

    records = parse_dart_company_records(xml_path)

    assert records == (
        DartCompanyRecord(
            corp_code="00258689",
            corp_name="JYP Ent.",
            corp_eng_name="JYP Entertainment Corporation",
            stock_code="035900",
            modify_date="20221206",
        ),
    )
    with pytest.raises(FrozenInstanceError):
        records[0].corp_code = "00535454"  # type: ignore[misc]


def test_exact_derived_acronym_and_typo_blocks_preserve_intent_and_abstain():
    index = build_dart_company_index(_records())

    for typed in ("YG", "yg", "Yg", "ｙＧ"):
        yg = generate_dart_company_matches(index, typed, limit=3)
        assert [item.record.corp_code for item in yg] == ["00613318"]
        assert yg[0].record.corp_name == "와이지엔터테인먼트"
        assert yg[0].match_kind == "acronym_token"
        assert yg[0].matched_field == "corp_eng_name"

    jyp = generate_dart_company_matches(index, "JYP", limit=3)
    assert [item.record.corp_code for item in jyp[:2]] == ["00258689", "00535454"]
    assert {item.match_kind for item in jyp[:2]} == {
        "acronym_token",
        "acronym_reading",
    }

    old_exact = generate_dart_company_matches(index, "제이와이피", limit=3)
    assert old_exact[0].record.corp_code == "00535454"
    assert old_exact[0].match_kind == "exact_name"

    for typed in ("SM", "S.M.", "s.m"):
        sm = generate_dart_company_matches(index, typed, limit=5)
        codes = [item.record.corp_code for item in sm]
        assert codes[0] == "00136689"
        assert "00999999" not in codes

    typo = generate_dart_company_matches(index, "JYP Entertainmnt", limit=3)
    assert typo[0].record.corp_code == "00258689"
    assert typo[0].match_kind == "trigram"

    for rejected in ("Y G", "YG1", "ҮG", "JY P", "JYP1", "JҮP"):
        assert generate_dart_company_matches(index, rejected, limit=3) == ()


@pytest.mark.local_integration
def test_로컬통합_full_catalog_keeps_YG_target_inside_profile_lookahead():
    app_root = Path(__file__).resolve().parents[4]
    cached_xml = tuple(
        (app_root / ".local_evaluation_runs").glob(
            "*/analysis_engine/corpcode/CORPCODE.xml"
        )
    )
    if not cached_xml:
        pytest.fail(
            "로컬 통합 시험을 선택했지만 DART CORPCODE.xml cache를 찾지 못했습니다"
        )

    newest_xml = max(cached_xml, key=lambda item: item.stat().st_mtime_ns)
    records = parse_dart_company_records(newest_xml)
    index = build_dart_company_index(records)
    matches = generate_dart_company_matches(index, "YG", limit=15)
    codes = [item.record.corp_code for item in matches]

    assert len(records) >= 100_000
    assert "00613318" in codes[:5]
    target = matches[codes.index("00613318")]
    assert target.record.corp_name == "와이지엔터테인먼트"
    assert target.record.stock_code == "122870"
    assert target.match_kind == "acronym_token"

    for rejected in ("ҮG", "JҮP", "ΑG"):
        assert generate_dart_company_matches(index, rejected, limit=15) == ()


def test_full_catalog_slice_rejects_dropped_script_confusables_but_keeps_punctuation():
    fixture_path = Path(__file__).parent / "fixtures" / "dart_yg_full_catalog_slice.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    index = build_dart_company_index(
        DartCompanyRecord(**row) for row in payload["records"]
    )

    for rejected in ("ҮG", "JҮP", "ΑG"):
        assert generate_dart_company_matches(index, rejected, limit=15) == ()

    assert generate_dart_company_matches(index, "ｙＧ", limit=15)[3].record.corp_code == (
        "00613318"
    )
    assert generate_dart_company_matches(index, "와이지-원", limit=5)[
        0
    ].record.corp_code == "00139719"


@pytest.mark.parametrize("reverse", [False, True])
def test_official_acronym_reverse_alias_keeps_qualified_and_homonym_candidates(
    reverse,
):
    records = _records()
    index = build_dart_company_index(reversed(records) if reverse else records)

    for query in ("제이와이피 엔터테인먼트", "JYP 엔터테인먼트"):
        codes = [
            item.record.corp_code
            for item in generate_dart_company_matches(index, query, limit=5)
        ]
        assert codes[:2] == ["00258689", "00535454"]

    old_exact = generate_dart_company_matches(index, "제이와이피", limit=5)
    assert old_exact[0].record.corp_code == "00535454"

    sm_codes = [
        item.record.corp_code
        for item in generate_dart_company_matches(index, "에스엠", limit=5)
    ]
    assert {"00136689", "01000001", "01000002"} <= set(sm_codes)
    assert "00999999" not in sm_codes


def test_reverse_alias_requires_literal_official_uppercase_acronym_token():
    index = build_dart_company_index(
        (
            DartCompanyRecord("03000001", "혼합표기", "Jyp Entertainment"),
            DartCompanyRecord("03000002", "붙임표기", "MYJYP Entertainment"),
            DartCompanyRecord("03000003", "법인접미사", "LTD Company"),
        )
    )

    assert generate_dart_company_matches(index, "제이와이피", limit=5) == ()
    assert generate_dart_company_matches(index, "엘티디", limit=5) == ()


class _GoldCandidateEngine:
    class UsageCounter:
        pass

    MODEL = ""

    def __init__(self, profiles):
        self.profiles = profiles
        self.calls: list[str] = []

    def load_env(self):
        return None

    def get_json(self, _path, params, _counter):
        corp_code = str(params["corp_code"])
        self.calls.append(corp_code)
        return dict(self.profiles[corp_code])


def test_offline_gold_metrics_recall_precision_cost_and_ambiguity(monkeypatch):
    payload = _gold()
    records = _records(payload)
    catalog = tuple(
        (
            row.corp_code,
            row.corp_name,
            row.corp_eng_name,
            row.stock_code,
            row.modify_date,
        )
        for row in records
    )
    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)

    positives = [case for case in payload["cases"] if case["expected"]]
    recalled = 0
    reciprocal_rank = 0.0
    top1_correct = 0
    false_positives = 0
    paid_fallbacks = 0

    for case in payload["cases"]:
        engine = _GoldCandidateEngine(payload["profiles"])
        monkeypatch.setattr(real, "_engine", lambda engine=engine: engine)
        rows = real.RealPipeline().search_business_candidates(
            company=case["query"],
            address_hint=case["address"],
            limit=3,
            timeout_sec=8.0,
        )
        codes = [str(row["candidate_ref"]) for row in rows]
        assert len(engine.calls) <= 3
        assert len(engine.calls) == len(set(engine.calls)), "corp_code만으로 profile을 dedupe"
        assert set(case["also"]) <= set(codes)
        false_positives += len(set(case["forbidden"]) & set(codes))

        if case["expected"]:
            if case["expected"] in codes:
                recalled += 1
                reciprocal_rank += 1.0 / (codes.index(case["expected"]) + 1)
            if codes and codes[0] == case["expected"]:
                top1_correct += 1
            if case["no_paid_fallback"] and not codes:
                paid_fallbacks += 1
        else:
            assert codes == []

    recall_at_3 = recalled / len(positives)
    mean_reciprocal_rank = reciprocal_rank / len(positives)
    top1_accuracy = top1_correct / len(positives)

    assert recall_at_3 == 1.0
    assert mean_reciprocal_rank == 1.0
    assert top1_accuracy == 1.0
    assert false_positives == 0
    assert paid_fallbacks == 0
