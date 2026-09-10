"""화면에 보이는 후보 «순서»와 «일치 근거 표시»의 회귀 방지 시험.

`dart_identity.MATCH_KIND_PRIORITY` 는 어떤 15건이 살아남는지만 정하고,
사용자가 보는 3장의 순서는 `logic._score` 와 `logic.screen_sort_key` 가 정한다.
두 계층이 어긋나면 matcher 가 정한 의도가 화면에 도달하지 않는다.

여기서 지키는 것 (2026-09-10 독립 검토가 찾은 결함)
1. 새 종류(prefix/substring)에 화면 점수 전용 분기가 있다. 없으면 낙하 분기
   (0.44)로 떨어져 «오타(trigram)보다도 뒤»가 된다.
2. 종류별 화면 점수가 matcher 우선순위와 같은 방향이다.
3. prefix/substring 은 같은 점수일 때 등록명이 짧은 쪽이 앞선다
   (matcher 의 `LENGTH_TIEBREAK_KINDS` 규칙이 화면까지 전달된다).
4. AI 가 추정한 정식명으로 찾아온 후보는 「법인명 일치」 초록 칩과
   「입력한 회사명과 …일치합니다」 근거를 받지 않는다.
"""

from __future__ import annotations

from src.features.budget import logic as budget_logic
from src.features.business_candidate import logic
from src.features.business_candidate.dart_identity import MATCH_KIND_PRIORITY


class _FixtureProvider:
    costs_money = False

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def search(self, **_kwargs):
        self.calls += 1
        return self.rows


def _fresh_rate(monkeypatch):
    monkeypatch.setattr(logic, "_RATE_HISTORY", budget_logic.RateHistory())


def _name_only_score(match_kind: str, *, similarity: float = 0.0) -> float:
    """이름 종류 «하나만» 다르게 한 화면 점수.

    질의와 후보명이 한 글자도 겹치지 않으므로 종류 분기가 없으면 0점이 된다.
    주소·홈페이지·종목코드·갱신일 가점은 전부 0이다.
    """
    score, _evidence = logic.score_business_candidate(
        query="가나다라",
        address_hint="",
        candidate_name="마바사아",
        address="",
        homepage="",
        name_match_kind=match_kind,
        name_similarity=similarity,
    )
    return round(score, 4)


def _candidate(name: str, *, kind: str, score: float, **changes) -> logic.BusinessCandidate:
    values = {
        "candidate_name": name,
        "address": "",
        "homepage": "",
        "source_label": "전자공시(DART) 기업개황 fixture",
        "source_url": "https://opendart.fss.or.kr/",
        "provider_name": "DART",
        "attributions": (),
        "score": score,
        "evidence": (),
        "candidate_ref": "",
        "stock_code": "",
        "modify_date": "",
        "english_name": "",
        "name_match_kind": kind,
        "name_similarity": 0.0,
    }
    values.update(changes)
    return logic.BusinessCandidate(**values)


def test_matcher가_아는_모든_종류는_화면_점수_전용_분기를_가진다():
    """★ 이번 사고의 근본 원인. prefix/substring 이 `MATCH_KIND_PRIORITY` 에만
    추가되고 `_score` 에는 분기가 없어서, 화면에서 낙하 분기로 떨어졌다.

    종류를 새로 만들면서 짝 분기를 빼먹으면 여기서 먼저 빨간불이 난다.
    """
    # 대조군: 종류가 없고 이름도 안 겹치면 이름 점수는 0이다.
    assert _name_only_score("") == 0.0

    for match_kind in MATCH_KIND_PRIORITY:
        assert _name_only_score(match_kind, similarity=1.0) > 0.0, (
            f"{match_kind}: _score 에 전용 분기가 없어 이름 근거가 0점이 됩니다"
        )


def test_종류별_화면_점수는_matcher_우선순위와_같은_방향이다():
    """실측값을 리터럴로 못 박는다(생산 상수를 import 하면 값이 낮아지는
    회귀를 못 잡는다). 아래 두 어긋남은 이번 변경 «전부터» 있던 것이라
    그대로 둔다 — 고치려면 기존 종류의 점수를 바꿔야 해서 별도 승인 사안이다.
      · acronym_token/acronym_reading(0.4553) 이 legal_suffix(0.4390) 보다 높다
      · trigram 최대치(0.4228) 가 token(0.3902) 보다 높다
    """
    assert _name_only_score("exact_id") == 0.5203
    assert _name_only_score("exact_name") == 0.5041
    assert _name_only_score("spacing") == 0.4878
    assert _name_only_score("acronym_token") == 0.4553
    assert _name_only_score("acronym_reading") == 0.4553
    assert _name_only_score("legal_suffix") == 0.4390
    assert _name_only_score("prefix") == 0.4309
    assert _name_only_score("substring") == 0.4268
    assert _name_only_score("acronym_cross_script") == 0.4228
    assert _name_only_score("trigram", similarity=1.0) == 0.4228
    assert _name_only_score("token") == 0.3902

    # 이번에 바로잡은 사슬: matcher 우선순위와 같은 방향이어야 한다.
    assert (
        _name_only_score("spacing")
        > _name_only_score("legal_suffix")
        > _name_only_score("prefix")
        > _name_only_score("substring")
        > _name_only_score("acronym_cross_script")
        > _name_only_score("token")
    )

    # ★ 결함 그 자체: 두 종류가 «낙하 분기»(입력한 회사명이 후보명에 포함됩니다)
    #   보다 낮으면 안 된다. 예전엔 정확히 그 값(0.3577)으로 떨어졌다.
    fallback, evidence = logic.score_business_candidate(
        query="가나다",
        address_hint="",
        candidate_name="가나다라마",
        address="",
        homepage="",
    )
    assert round(fallback, 4) == 0.3577
    assert evidence == ("입력한 회사명이 후보명에 포함됩니다",)
    assert _name_only_score("prefix") > round(fallback, 4)
    assert _name_only_score("substring") > round(fallback, 4)


def test_prefix는_같은_점수면_등록명이_짧은_후보를_화면에서_앞세운다():
    """실측 「아시아나」: matcher 는 짧은 이름을 앞세우라고 정했는데 화면은
    점수 동점 뒤 이름 가나다순이라 아시아나IDT 가 아시아나항공보다 앞섰다.
    """
    idt = _candidate("아시아나IDT", kind="prefix", score=0.5203)
    air = _candidate("아시아나항공", kind="prefix", score=0.5203)

    ordered = sorted([idt, air], key=logic.screen_sort_key)

    assert [item.candidate_name for item in ordered] == ["아시아나항공", "아시아나IDT"]

    # 대조군: 길이 동점 규칙은 prefix/substring 에만 적용된다. 다른 종류는
    # 예전 그대로 «이름 가나다순»이라 긴 이름이 앞설 수 있다.
    long_name = _candidate("가나다라마바사", kind="exact_name", score=0.5203)
    short_name = _candidate("하하", kind="exact_name", score=0.5203)
    kept = sorted([short_name, long_name], key=logic.screen_sort_key)
    assert [item.candidate_name for item in kept] == ["가나다라마바사", "하하"]


def test_화면_1위는_짧은_등록명_규칙까지_거쳐_정해진다(monkeypatch):
    """정렬 키 단위가 아니라 운영 `resolve_candidates` 를 그대로 통과시킨다."""
    _fresh_rate(monkeypatch)
    rows = [
        logic.RawBusinessCandidate(
            candidate_name=name,
            address="",
            homepage="",
            source_label="전자공시(DART) 기업개황",
            source_url="https://opendart.fss.or.kr/",
            provider_name="DART",
            candidate_ref=ref,
            name_match_kind="prefix",
        )
        for name, ref in (("아시아나IDT", "00111111"), ("아시아나항공", "00222222"))
    ]

    result = logic.resolve_candidates(
        _FixtureProvider(rows),
        company="아시아나",
        address_hint="",
        rate_key="screen-order-prefix",
        now=11.0,
    )

    assert result.status is logic.ResolutionStatus.OK
    assert [item.candidate_name for item in result.candidates] == [
        "아시아나항공",
        "아시아나IDT",
    ]
    assert result.candidates[0].evidence[0] == (
        "DART 정식명칭이 입력한 회사명으로 시작합니다"
    )


def test_AI가_추정한_정식명으로_찾은_후보는_확신_칩을_받지_않는다(monkeypatch):
    """★ 사용자는 「배민」이라고 적었는데 화면은 초록 「법인명 일치」 칩과
    「입력한 회사명과 DART 정식명칭이 일치합니다」를 붙였다. 「AI 는 회사를
    확정하지 않는다」는 설계가 화면에서 무력화되던 자리다.
    """
    _fresh_rate(monkeypatch)
    row = logic.RawBusinessCandidate(
        candidate_name="우아한형제들",
        address="",
        homepage="",
        source_label="전자공시(DART) 기업개황 · AI 정식명 추정",
        source_url="https://opendart.fss.or.kr/",
        provider_name="DART",
        candidate_ref="00333333",
        name_match_kind="exact_name",
        alias_source="ai",
        alias_name="우아한형제들",
    )

    result = logic.resolve_candidates(
        _FixtureProvider([row]),
        company="배민",
        address_hint="",
        rate_key="screen-order-alias",
        now=12.0,
    )

    assert result.status is logic.ResolutionStatus.OK
    candidate = result.candidates[0]
    assert candidate.alias_source == "ai"

    chips = logic.candidate_match_chips(candidate, query="배민", address_hint="")
    assert [chip.label for chip in chips] == ["AI 추정명 일치", "주소 불확실"]
    assert chips[0].tone != "ok"
    assert candidate.evidence[0] == (
        "AI가 추정한 정식명(우아한형제들)과 일치합니다 · 사람이 확인해야 합니다"
    )
    assert "입력한 회사명과 DART 정식명칭이 일치합니다" not in candidate.evidence

    # 점수는 표시와 무관하게 그대로다 — 이 수정은 «무엇이 맞았는지»의 설명만
    # 바꾼다.
    deterministic_score, _evidence = logic.score_business_candidate(
        query="우아한형제들",
        address_hint="",
        candidate_name="우아한형제들",
        address="",
        homepage="",
        name_match_kind="exact_name",
    )
    assert candidate.score == deterministic_score


def test_결정적_경로_후보는_예전처럼_초록_법인명_일치를_받는다(monkeypatch):
    """대조군. AI 분기가 결정적 후보의 표시까지 함께 낮추면 안 된다."""
    _fresh_rate(monkeypatch)
    row = logic.RawBusinessCandidate(
        candidate_name="우아한형제들",
        address="",
        homepage="",
        source_label="전자공시(DART) 기업개황",
        source_url="https://opendart.fss.or.kr/",
        provider_name="DART",
        candidate_ref="00333333",
        name_match_kind="exact_name",
    )

    result = logic.resolve_candidates(
        _FixtureProvider([row]),
        company="우아한형제들",
        address_hint="",
        rate_key="screen-order-deterministic",
        now=13.0,
    )

    candidate = result.candidates[0]
    assert candidate.alias_source == ""
    chips = logic.candidate_match_chips(
        candidate, query="우아한형제들", address_hint=""
    )
    assert chips[0].label == "법인명 일치"
    assert chips[0].tone == "ok"
    assert candidate.evidence[0] == "입력한 회사명과 DART 정식명칭이 일치합니다"
