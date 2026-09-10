"""prefix/substring 부분일치와 종류 우선순위 재정렬의 회귀 방지 시험.

``dart_identity.py`` 에서 실측으로 확인한 세 가지를 지킨다.

1. MATCH_KIND_PRIORITY 동점 제거 — spacing/legal_suffix 가 acronym_cross_script
   같은 약한 증거에 묻혀 15개 자르기에서 밀려나지 않는다
   (``.local-artifacts/alias-recall-probe/rank_depth.json`` 의 "SK 하이닉스" 22위).
2. 한글↔영문 토큰 경계 분리 — "삼성SDS"처럼 붙여 쓴 질의도 기존 약어
   교차표기(acronym_cross_script) 경로를 탄다.
3. prefix/substring 신설 — "당근"→당근마켓처럼 법인 접미사를 생략하거나
   "올리브영"→씨제이올리브영처럼 모회사 이름을 생략한 한글 부분질의를 찾는다.
4. 독식 구제 — prefix/substring 이 짧은 한글 조각과 우연히 겹쳐 limit 을
   통째로 채우면("엔씨"가 "엔씨아이디에스"류 15건과 겹쳐 약어 독음으로
   찾던 "NC"를 완전히 밀어냄, alias-recall-probe 재측정으로 확인된 퇴행)
   다른 가족의 최상위 후보 일부를 구제한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.features.business_candidate.dart_identity import (
    DartCompanyRecord,
    build_dart_company_index,
    company_name_tokens,
    generate_dart_company_matches,
    parse_dart_company_records,
)


def test_match_kind_priority_breaks_ties_so_spacing_survives_the_limit_cutoff():
    """spacing 일치 1건이 acronym_cross_script 150건에 묻혀 15개 자르기에서
    사라지던 실측 버그를 합성 색인으로 재현한다. 노이즈 150건은 전부
    종목코드와 최신 수정일을 갖고 있어, 예전 동점 처리(종목코드 유무 ->
    최종수정일)로는 노이즈가 반드시 이긴다 — 새 우선순위에서는 spacing 이
    그 동점 처리 자체를 타지 않고 항상 이겨야 한다.
    """
    target = DartCompanyRecord(
        corp_code="90000000",
        corp_name="가나다AB",
        corp_eng_name="",
        stock_code="",
        modify_date="20200101",
    )
    noise = [
        DartCompanyRecord(
            corp_code=f"91{i:06d}",
            corp_name=f"노이즈{i}",
            corp_eng_name=f"AB Noise{i} Corp.",
            stock_code=f"{100000 + i:06d}",
            modify_date="20260901",
        )
        for i in range(150)
    ]
    index = build_dart_company_index([target, *noise])

    matches = generate_dart_company_matches(index, "가나다 AB", limit=15)

    assert len(matches) == 15
    assert matches[0].record.corp_code == "90000000"
    assert matches[0].match_kind == "spacing"
    assert {item.match_kind for item in matches[1:]} == {"acronym_cross_script"}


def test_tokenizer_splits_at_hangul_latin_script_boundary_but_keeps_latin_digit_fused():
    """"삼성SDS"가 예전엔 토큰 1개("삼성sds")로 묶여 약어·부분일치 경로를 전부
    놓쳤다. 한글 런과 영문/숫자 런만 분리하고, 영문+숫자는 그대로 묶어
    "GS25" 같은 기존 토큰화는 건드리지 않는다.
    """
    assert company_name_tokens("삼성SDS") == ("삼성", "sds")
    assert company_name_tokens("SSG닷컴") == ("ssg", "닷컴")
    # 영문과 숫자는 계속 한 토큰 — 이번 수정 대상이 아니다(회귀 없음).
    assert company_name_tokens("GS25") == ("gs25",)
    # 이미 공백이 있던 기존 정식명 토큰화는 그대로다.
    assert company_name_tokens("JYP Entertainment Corporation") == (
        "jyp",
        "entertainment",
    )


def test_glued_script_boundary_fix_reaches_the_official_english_acronym():
    """토크나이저 분리만으로(prefix/substring 없이도) "삼성SDS" 같은 질의가
    기존 acronym_cross_script 경로(영문 원문에 그대로 있는 대문자 약어 일치)를
    타는지 실제 매칭까지 확인한다. 색인은 실제 DART 표기 그대로 쓴다.
    """
    index = build_dart_company_index(
        [
            DartCompanyRecord(
                "92000001",
                "삼성에스디에스",
                "SAMSUNG SDS CO., LTD.",
                "018260",
                "20250320",
            ),
            DartCompanyRecord("92000002", "무관회사", "Unrelated Co., Ltd."),
        ]
    )
    matches = generate_dart_company_matches(index, "삼성SDS", limit=5)
    assert matches
    assert matches[0].record.corp_code == "92000001"
    assert matches[0].match_kind == "acronym_cross_script"


def test_prefix_and_substring_partial_name_matches_only_open_for_korean_queries():
    """"당근"→당근마켓(prefix, 법인 접미사 생략)과 "올리브영"→씨제이올리브영
    (substring, 모회사 이름 생략)을 찾되, 한글이 없는 영문 질의에는 이
    두 종류가 절대 나오지 않는다(약어 경로가 이미 그 몫을 맡는다).
    """
    index = build_dart_company_index(
        [
            DartCompanyRecord("93000001", "당근마켓", "Danggeun Market Inc."),
            DartCompanyRecord(
                "93000002", "씨제이올리브영", "CJ Olive Young Corporation"
            ),
        ]
    )

    prefix_hit = generate_dart_company_matches(index, "당근", limit=5)
    assert prefix_hit[0].record.corp_code == "93000001"
    assert prefix_hit[0].match_kind == "prefix"

    substring_hit = generate_dart_company_matches(index, "올리브영", limit=5)
    assert substring_hit[0].record.corp_code == "93000002"
    assert substring_hit[0].match_kind == "substring"

    # 1음절 질의는 노이즈가 커서 문턱(2음절)에 막혀 아무것도 찾지 않는다.
    assert generate_dart_company_matches(index, "당", limit=5) == ()

    # 순수 영문 질의는 prefix/substring 을 타지 않는다. "CJ Olive"/"Danggeun"은
    # 기존 token 경로(다중/4자 이상 단일 토큰 부분집합 일치)로 여전히 후보가
    # 나올 수 있지만, 그 kind 는 prefix/substring 이 아니어야 한다.
    for query in ("CJ Olive", "Danggeun", "Oliv"):
        for item in generate_dart_company_matches(index, query, limit=5):
            assert item.match_kind not in ("prefix", "substring"), (query, item)


def test_partial_match_monopoly_does_not_erase_an_unrelated_acronym_target():
    """실측 퇴행 재현: "엔씨"가 접두 일치 15건과 우연히 겹쳐 약어 독음으로
    찾던 "NC"를 완전히 밀어냈다(alias-recall-probe 재측정, top3->miss).
    prefix 후보 20건 중 20건 모두 진짜 후보(같은 접두를 쓰는 별개 법인)이고,
    별도로 영문명에 "GB"를 literal 로 갖고 있어 acronym_cross_script 로만
    찾을 수 있는 회사 1곳을 합성 색인에 심는다. 순위 원칙(prefix 가 여전히
    acronym_cross_script 보다 앞선다)은 지키되, 그 회사가 limit 안에서
    완전히 사라지지는 않아야 한다.
    """
    target = DartCompanyRecord("80000000", "글로벌비즈니스", "GB Global Inc.")
    noise = [
        DartCompanyRecord(f"81{i:06d}", f"지비노이즈{i}", "") for i in range(20)
    ]
    index = build_dart_company_index([target, *noise])

    matches = generate_dart_company_matches(index, "지비", limit=15)

    assert len(matches) == 15
    # prefix 가 여전히 우세하다 — 순위 원칙 자체를 뒤집지 않는다.
    assert matches[0].match_kind == "prefix"
    codes = [item.record.corp_code for item in matches]
    assert "80000000" in codes
    rescued = matches[codes.index("80000000")]
    assert rescued.match_kind == "acronym_cross_script"

    # 노이즈가 15건 이하(=독식이 아님)면 원래도 다 들어가므로 구제할 게
    # 없다 — 이 대조군은 구제 로직이 "항상 끼워 넣기"가 아니라 진짜
    # 독식일 때만 동작한다는 것을 보인다.
    small_index = build_dart_company_index([target, *noise[:5]])
    small_matches = generate_dart_company_matches(small_index, "지비", limit=15)
    assert len(small_matches) == 6
    assert {item.match_kind for item in small_matches} == {
        "prefix",
        "acronym_cross_script",
    }


@pytest.mark.local_integration
def test_로컬통합_partial_and_reordered_aliases_reach_official_registration():
    """실측(alias-recall-probe)에서 못 찾던 21개 중 이번에 고친 9개가 실제
    DART 전체 목록에서 상위 15위 안(대부분 3위 안)에 들어오는지, 그리고
    잘림 버그로 22위까지 밀렸던 "SK 하이닉스"·"씨제이 올리브영"이 1위로
    올라오는지를 실제 CORPCODE 캐시로 확인한다.
    """
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
    assert len(records) >= 100_000
    index = build_dart_company_index(records)

    # (질의, 등록명, 상위 몇 위 안이어야 하는지). 실측 순위는 보고서에 그대로
    # 옮겼다. "넥슨"만 top3 가 아니라 top15 다 — 실제로 "넥슨OOO" 계열사가
    # 12곳이나 더 있어(넥슨게임즈·넥슨지티 등) 같은 prefix tier 안에서
    # 최종수정일 tie-break 로 9위까지 밀린다.
    partial_match_cases = (
        ("당근", "당근마켓", 3),
        ("여기어때", "여기어때컴퍼니", 3),
        ("넥슨", "넥슨코리아", 15),
        ("티맵", "티맵모빌리티", 3),
        ("아시아나", "아시아나항공", 3),
        ("삼성화재", "삼성화재해상보험", 3),
        ("올리브영", "씨제이올리브영", 3),
    )
    for query, expected_name, top_n in partial_match_cases:
        matches = generate_dart_company_matches(index, query, limit=15)
        names = [item.record.corp_name for item in matches[:top_n]]
        assert expected_name in names, (query, names)

    acronym_bridge_cases = (
        ("삼성SDS", "삼성에스디에스"),
        ("SSG닷컴", "에스에스지닷컴"),
    )
    for query, expected_name in acronym_bridge_cases:
        matches = generate_dart_company_matches(index, query, limit=15)
        assert matches, query
        assert matches[0].record.corp_name == expected_name, [
            (item.record.corp_name, item.match_kind) for item in matches[:3]
        ]

    sk_matches = generate_dart_company_matches(index, "SK 하이닉스", limit=15)
    assert sk_matches
    assert sk_matches[0].record.corp_name == "SK하이닉스"
    assert sk_matches[0].match_kind == "spacing"

    cj_matches = generate_dart_company_matches(index, "씨제이 올리브영", limit=15)
    assert cj_matches
    assert cj_matches[0].record.corp_name == "씨제이올리브영"
    assert cj_matches[0].match_kind == "spacing"


def test_두음절_질의는_등록명_안쪽_부분일치를_열지_않는다():
    """★ 2026-09-10 로컬 CORPCODE 실측: 「토스」가 「와토스코리아」·「비스토스」·
    「미래오토스」를 쳤다. 셋 다 종목코드가 있어 화면에서 아주 그럴듯한데
    전부 무관한 회사다. 기준 동작은 정직하게 「못 찾음」이었다.

    앞부분 일치(prefix)는 그대로 둔다 — 질의로 «시작»하는 이름은 우연이
    훨씬 적고, 실제로 쓰이는 경로다.
    """
    index = build_dart_company_index(
        [
            DartCompanyRecord("95000001", "와토스코리아", "", "079000", "20260101"),
            DartCompanyRecord("95000002", "토스뱅크", "", "", "20260101"),
            DartCompanyRecord("95000003", "씨제이올리브영", "", "", "20260101"),
        ]
    )

    toss = generate_dart_company_matches(index, "토스", limit=15)

    assert [item.record.corp_code for item in toss] == ["95000002"]
    assert toss[0].match_kind == "prefix"
    assert "95000001" not in [item.record.corp_code for item in toss]

    # 대조군: 문턱을 넘는 질의에서는 substring 이 그대로 살아 있다.
    olive = generate_dart_company_matches(index, "올리브영", limit=15)
    assert [item.record.corp_code for item in olive] == ["95000003"]
    assert olive[0].match_kind == "substring"


def test_안쪽_부분일치_문턱은_정확히_세글자다():
    """문턱 바로 아래(2자)와 문턱(3자)을 «같은 등록명»에 대고 잰다."""
    index = build_dart_company_index(
        [DartCompanyRecord("95000010", "무지개해피컴퍼니", "", "", "20260101")]
    )

    assert generate_dart_company_matches(index, "해피", limit=15) == ()

    opened = generate_dart_company_matches(index, "해피컴", limit=15)
    assert [item.record.corp_code for item in opened] == ["95000010"]
    assert opened[0].match_kind == "substring"
