"""장 끝 「최근 보도 (보조)」 표를 «만드는 판단»만 따로 잰다.

AI·네트워크 0회. 조각과 장별 소유권 표만 주고, 어떤 행이 실리고 어떤 행이
빠지는지를 본다. 세 채널에 실제로 그려지는지는 옆 파일
(`test_news_block_channels.py`)이 FULL 실행으로 잰다.
"""

from __future__ import annotations

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.news_block import (
    BLOCKED_EXCLUDED_SECTION,
    BLOCKED_MISSING_META,
    BLOCKED_NOT_SECTION_OWNED,
    BLOCKED_NO_SECTION_OWNERSHIP,
    BLOCKED_ROW_LIMIT,
    BLOCKED_TEXT_MISMATCH,
    NEWS_BLOCK_BLOCKED_STEP,
    NEWS_BLOCK_HEADERS,
    NEWS_BLOCK_STEP,
    augment_news_blocks,
    news_block_caption,
    news_block_steps,
    news_ownership_from_claim_slots,
    oldest_stale_report_year,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    NewsRow,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION


#: 가공 회사·매체 이름 — 실존 회사·언론사를 시험에 박지 않는다.
_PUBLISHER = "가나다경제"
_OTHER_PUBLISHER = "라마바일보"
_SENTENCE = "가나다전자는 물류 자동화 제품군을 현장에 적용한다."
_QUOTED = '김대표는 "고객 곁에서 배우는 문화를 지키겠다"고 말했다.'


def _report() -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(section_id=section_id, sentences=())
            for section_id in SECTION_IDS
        )
    )


def _news(
    fragment_id: str,
    *,
    text: str = _SENTENCE,
    published_on: str = "2026-09-01",
    publisher: str = _PUBLISHER,
    slots: tuple[str, ...] = (),
    kind: str = "news",
    formal_source_kind: str = "news",
) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id,
        kind=kind,
        text=text,
        source_url=f"https://media.example/news/{fragment_id}",
        document_title="가나다전자 물류 자동화",
        location=f"기사 본문 · news-fragment-{fragment_id}",
        document_date=published_on,
        formal_source_kind=formal_source_kind,
        source_publisher=publisher,
        counts_toward_document_floor=False,
        supported_claim_slots=slots,
    )


def _official(fragment_id: str) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="회사 공식 자료",
        text="가나다전자는 공식 자료에서 사업 구조를 밝혔다.",
        source_url="https://company.example/about",
        formal_source_kind="official_identity_verified_web_page",
    )


def _owned(**by_section: tuple[str, ...]) -> dict[str, frozenset[str]]:
    """장별 소유 조각 id 표 — 안 적은 장은 빈 집합이다."""

    return {
        section_id: frozenset(by_section.get(section_id, ()))
        for section_id in SECTION_IDS
    }


def _table_rows(result, section_id: str) -> list[tuple[str, ...]]:
    section = next(
        section
        for section in result.report.sections
        if section.section_id == section_id
    )
    return [tuple(row.cells) for row in section.news_rows]


# ══════════════════════════════════════════════════════════
# ① 만든다 — 조각을 받은 장 끝에 표가 붙는다
# ══════════════════════════════════════════════════════════


def test_조각을_받은_장마다_보도표가_붙는다() -> None:
    fragments = (_official("1"), _news("40"), _news("41"))
    result = augment_news_blocks(
        _report(),
        fragments,
        allowed_fragment_ids_by_section=_owned(
            identity=("1", "40"), portfolio=("41",)
        ),
    )

    assert result.added
    assert dict(result.row_counts_by_section) == {"identity": 1, "portfolio": 1}
    assert _table_rows(result, "identity") == [
        ("2026-09-01", _PUBLISHER + " · 가나다전자 물류 자동화", _SENTENCE)
    ]
    assert _table_rows(result, "portfolio") == [
        ("2026-09-01", _PUBLISHER + " · 가나다전자 물류 자동화", _SENTENCE)
    ]
    # 조각이 안 온 장은 «객체가 그대로»여야 한다.
    assert all(
        not section.news_rows
        for section in result.report.sections
        if section.section_id not in {"identity", "portfolio"}
    )


def test_보도_문장은_조각_원문_그대로다() -> None:
    """따옴표·조사를 한 글자도 바꾸지 않는다 — 5·6장은 인용문만 온다."""

    result = augment_news_blocks(
        _report(),
        (_news("50", text=_QUOTED),),
        allowed_fragment_ids_by_section=_owned(current_challenges=("50",)),
    )

    rows = _table_rows(result, "current_challenges")
    assert rows == [("2026-09-01", _PUBLISHER + " · 가나다전자 물류 자동화", _QUOTED)]
    # 따옴표 자체가 남았는지 별도로 못 박는다(따옴표를 떼면 발언이 기자의
    # 서술로 읽힌다 — 5·6장이 인용문만 받는 이유가 사라진다).
    assert '"고객 곁에서 배우는 문화를 지키겠다"' in rows[0][2]


def test_행은_최신_발행일부터_실린다() -> None:
    fragments = (
        _news("60", published_on="2025-01-02"),
        _news("61", published_on="2026-03-04", publisher=_OTHER_PUBLISHER),
        _news("62", published_on="2025-12-31"),
    )
    result = augment_news_blocks(
        _report(),
        fragments,
        allowed_fragment_ids_by_section=_owned(identity=("60", "61", "62")),
    )

    assert [row[0] for row in _table_rows(result, "identity")] == [
        "2026-03-04",
        "2025-12-31",
        "2025-01-02",
    ]
    assert _table_rows(result, "identity")[0][1] == _OTHER_PUBLISHER + " · 가나다전자 물류 자동화"


def test_수집한_기사에_추가_출력상한을_적용하지_않는다() -> None:
    ids = tuple(str(70 + offset) for offset in range(8))
    fragments = tuple(
        # 두 자리로 적는다 — 상한이 여덟을 넘으면 «2026-09-010» 같은 값이 나와
        # 정렬이 조용히 뒤집힌다.
        _news(fragment_id, published_on=f"2026-09-{index + 1:02d}")
        for index, fragment_id in enumerate(ids)
    )
    result = augment_news_blocks(
        _report(),
        fragments,
        allowed_fragment_ids_by_section=_owned(identity=ids),
    )

    assert len(_table_rows(result, "identity")) == len(ids)
    assert BLOCKED_ROW_LIMIT not in dict(result.blocked_counts_by_reason)



def test_작가가_이미_인용한_조각도_표에_실린다() -> None:
    """작가 인용 여부로 표를 켜고 끄면 실행마다 결과가 갈린다.

    ★ 「이미 썼으니 안 붙인다」로 만들면, 같은 회사를 두 번 돌렸을 때 한 번은
      표가 있고 한 번은 없다. 예측 가능성을 우선해 언제나 붙인다.
    """

    from src.features.composer.port import ComposedSentence

    본문이_인용한_보고서 = ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    (
                        ComposedSentence(
                            text=_SENTENCE, citations=("40",), grade="확인"
                        ),
                    )
                    if section_id == "identity"
                    else ()
                ),
            )
            for section_id in SECTION_IDS
        )
    )

    result = augment_news_blocks(
        본문이_인용한_보고서,
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(identity=("40",)),
    )

    assert _table_rows(result, "identity") == [
        ("2026-09-01", _PUBLISHER + " · 가나다전자 물류 자동화", _SENTENCE)
    ]


def test_두_번_불러도_행이_늘지_않는다() -> None:
    """멱등 — 보충 회차가 도는 회사에서만 행이 두 배가 되면 안 된다."""

    owned = _owned(identity=("40",))
    once = augment_news_blocks(
        _report(), (_news("40"),), allowed_fragment_ids_by_section=owned
    )
    twice = augment_news_blocks(
        once.report, (_news("40"),), allowed_fragment_ids_by_section=owned
    )

    assert _table_rows(twice, "identity") == _table_rows(once, "identity")


# ══════════════════════════════════════════════════════════
# ② 안 만든다 — 어긋난 행은 빼고, 사유를 남긴다
# ══════════════════════════════════════════════════════════


def test_그_장_소유가_아닌_조각은_실리지_않는다() -> None:
    """소유 밖 조각을 인용하면 evidence invariant가 보고서 전체를 막는다."""

    result = augment_news_blocks(
        _report(),
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(portfolio=("40",)),
    )

    assert _table_rows(result, "identity") == []
    assert len(_table_rows(result, "portfolio")) == 1


def test_소유권_표가_없으면_아무_표도_만들지_않는다() -> None:
    result = augment_news_blocks(
        _report(), (_news("40"),), allowed_fragment_ids_by_section=None
    )

    assert not result.added
    assert result.report.sections == _report().sections
    assert dict(result.blocked_counts_by_reason) == {
        BLOCKED_NO_SECTION_OWNERSHIP: 1
    }


def test_결속_못하는_모드면_아무_표도_안_만든다() -> None:
    """표를 결속하지 못하는 실행에서 붙이면 보고서 «전체»가 막힌다."""

    from src.features.composer.news_block import BLOCKED_UNBINDABLE_MODE

    result = augment_news_blocks(
        _report(),
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(identity=("40",)),
        enabled=False,
    )

    assert not result.added
    assert result.report.sections == _report().sections
    assert dict(result.blocked_counts_by_reason) == {BLOCKED_UNBINDABLE_MODE: 1}


def test_9장에는_보도표를_붙이지_않는다() -> None:
    """9장은 «회사가 밝힌 차별점»이라 기자의 서술을 싣지 않는다."""

    result = augment_news_blocks(
        _report(),
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(competitive_position=("40",)),
    )

    assert not result.added
    assert dict(result.blocked_counts_by_reason) == {BLOCKED_EXCLUDED_SECTION: 1}


@pytest.mark.parametrize(
    ("published_on", "publisher"),
    (("", _PUBLISHER), ("2026-09-01", "")),
)
def test_발행일이나_매체가_비면_그_행을_뺀다(
    published_on: str, publisher: str
) -> None:
    result = augment_news_blocks(
        _report(),
        (_news("40", published_on=published_on, publisher=publisher),),
        allowed_fragment_ids_by_section=_owned(identity=("40",)),
    )

    assert not result.added
    assert dict(result.blocked_counts_by_reason) == {BLOCKED_MISSING_META: 1}


def test_뉴스가_아닌_조각은_후보에도_안_든다() -> None:
    result = augment_news_blocks(
        _report(),
        (_official("1"),),
        allowed_fragment_ids_by_section=_owned(identity=("1",)),
    )

    assert not result.added
    assert result.blocked_counts_by_reason == ()


def test_글자를_줄이면_그_행이_빠진다(monkeypatch: pytest.MonkeyPatch) -> None:
    """행을 «짓는 코드»가 원문을 줄이면 «재는 코드»가 그 행을 뺀다.

    ★ 이 시험이 없으면 자체 검사 (a)가 있으나 마나다 — 지금은 원문을 그대로
      옮기니 항상 통과하고, 나중에 누가 줄임말을 넣어도 아무 시험도 안 깨진다.
    """

    from src.features.composer import news_block

    original = news_block._news_row  # noqa: SLF001

    def truncating(fragment):
        row = original(fragment)
        return type(row)(
            cells=(row.cells[0], row.cells[1], row.cells[2][:10] + "…"),
            citations=row.citations,
        )

    monkeypatch.setattr(news_block, "_news_row", truncating)
    result = news_block.augment_news_blocks(
        _report(),
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(identity=("40",)),
    )

    assert not result.added
    assert dict(result.blocked_counts_by_reason) == {BLOCKED_TEXT_MISMATCH: 1}


def test_소유_밖_조각을_행으로_만들면_자체_검사가_뺀다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """후보 거르기를 지나쳐도 «만든 뒤 재는» 검사가 한 번 더 막는다."""

    from src.features.composer import news_block

    result = news_block.augment_news_blocks(
        _report(),
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(identity=("40",)),
    )
    assert result.added, "먼저 정상 경로가 붙는지부터 확인한다"

    row = news_block._news_row(_news("40"))  # noqa: SLF001
    problem = news_block._row_problem(  # noqa: SLF001
        row, _news("40"), allowed_fragment_ids=frozenset({"99"})
    )

    assert problem == BLOCKED_NOT_SECTION_OWNED


# ══════════════════════════════════════════════════════════
# ②-b packet이 없는 부분 보고서 경로 — 의미 칸에서 장을 되찾는다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 — 장별 근거 packet은 FULL에서만 만들어진다. 사전검사가 자료
#   부족을 보고 부분 보고서 갈래를 열면 packet이 없어, 소유권 표가 ``None``이
#   되고 뉴스 조각이 전부 빠졌다(운영 실측 사유 ``no_section_ownership`` 6건).
#   그 경로가 바로 언론 보도가 가장 필요한 실행이다. typed 조각은 자기가
#   지지하는 의미 칸을 이미 들고 다니므로 거기서 장을 되찾는다.


def test_두_장의_의미_칸을_가진_뉴스_조각은_두_장_모두에_속한다() -> None:
    owned = news_ownership_from_claim_slots(
        (
            _news(
                "40",
                slots=(
                    CLAIM_SLOTS_BY_SECTION["identity"][0],
                    CLAIM_SLOTS_BY_SECTION["portfolio"][2],
                ),
            ),
        )
    )

    assert owned["identity"] == frozenset({"40"})
    assert owned["portfolio"] == frozenset({"40"})
    assert owned["business_model"] == frozenset()


def test_뉴스가_아닌_조각은_어느_장에도_안_들어간다() -> None:
    """공식 조각까지 이 표에 넣으면 보도표가 회사 공식 문장을 싣는다."""

    공식_조각 = CollectedFragment(
        fragment_id="1",
        kind="회사 공식 자료",
        text="가나다전자는 공식 자료에서 사업 구조를 밝혔다.",
        formal_source_kind="official_identity_verified_web_page",
        supported_claim_slots=(CLAIM_SLOTS_BY_SECTION["identity"][0],),
    )

    owned = news_ownership_from_claim_slots((공식_조각,))

    assert all(not ids for ids in owned.values())


def test_모르는_의미_칸은_무시하고_예외를_던지지_않는다() -> None:
    """보조 표 하나 때문에 보고서 «전체»를 예외로 막지 않는다."""

    owned = news_ownership_from_claim_slots(
        (
            _news(
                "40",
                slots=("없는장:없는칸", CLAIM_SLOTS_BY_SECTION["culture"][1]),
            ),
        )
    )

    assert owned["culture"] == frozenset({"40"})
    assert sum(len(ids) for ids in owned.values()) == 1


def test_의미_칸이_없는_뉴스_조각은_어느_장에도_안_들어간다() -> None:
    """fail-closed — 모르면 싣지 않는다. 종류 이름으로 장을 추측하지 않는다."""

    owned = news_ownership_from_claim_slots((_news("40"),))

    assert all(not ids for ids in owned.values())


def test_모든_장이_열쇠로_있어_소유_없음과_모름이_섞이지_않는다() -> None:
    """``.get(장)``이 ``None``을 돌려주면 「모름」으로 읽혀 검사가 헐거워진다."""

    owned = news_ownership_from_claim_slots((_news("40"),))

    assert set(owned) == set(SECTION_IDS)
    assert all(owned.get(section_id) is not None for section_id in SECTION_IDS)


def test_되찾은_소유권으로_선언한_장에만_보도표가_붙는다() -> None:
    fragments = (
        _news("40", slots=(CLAIM_SLOTS_BY_SECTION["identity"][0],)),
        _news("41", slots=(CLAIM_SLOTS_BY_SECTION["portfolio"][1],)),
        # 의미 칸이 없는 조각은 어느 장에도 못 붙는다.
        _news("42"),
    )

    result = augment_news_blocks(
        _report(),
        fragments,
        allowed_fragment_ids_by_section=news_ownership_from_claim_slots(fragments),
    )

    assert dict(result.row_counts_by_section) == {"identity": 1, "portfolio": 1}
    assert _table_rows(result, "identity") == [
        ("2026-09-01", _PUBLISHER + " · 가나다전자 물류 자동화", _SENTENCE)
    ]
    assert _table_rows(result, "business_model") == []
    # 의미 칸 없는 조각은 소유 밖이라 «그 행만» 빠진다.
    assert dict(result.blocked_counts_by_reason) == {}


def test_9장_의미_칸만_가진_뉴스_조각은_표가_안_붙는다() -> None:
    """소유권을 되찾아도 9장 제외 규칙은 그대로다."""

    fragments = (
        _news("40", slots=(CLAIM_SLOTS_BY_SECTION["competitive_position"][0],)),
    )

    result = augment_news_blocks(
        _report(),
        fragments,
        allowed_fragment_ids_by_section=news_ownership_from_claim_slots(fragments),
    )

    assert not result.added
    assert dict(result.blocked_counts_by_reason) == {BLOCKED_EXCLUDED_SECTION: 1}


# ══════════════════════════════════════════════════════════
# ③ 실행 기록
# ══════════════════════════════════════════════════════════


class _Output:
    def __init__(self, rows=(), blocked=()):
        self.news_block_row_counts_by_section = rows
        self.news_block_blocked_counts_by_reason = blocked


def test_붙인_실행은_장별_행_수를_남긴다() -> None:
    steps = news_block_steps(_Output(rows=(("identity", 2), ("portfolio", 1))))

    assert steps == [
        {
            "step": NEWS_BLOCK_STEP,
            "행수": 3,
            "장별행수": {"identity": 2, "portfolio": 1},
        }
    ]


def test_못_붙인_실행은_사유별_수를_남긴다() -> None:
    steps = news_block_steps(_Output(blocked=((BLOCKED_MISSING_META, 2),)))

    assert steps == [
        {"step": NEWS_BLOCK_BLOCKED_STEP, "사유별": {BLOCKED_MISSING_META: 2}}
    ]


def test_일부만_붙은_실행은_두_줄을_다_남긴다() -> None:
    steps = news_block_steps(
        _Output(rows=(("identity", 1),), blocked=((BLOCKED_ROW_LIMIT, 1),))
    )

    assert [step["step"] for step in steps] == [
        NEWS_BLOCK_STEP,
        NEWS_BLOCK_BLOCKED_STEP,
    ]


def test_이_필드를_모르는_옛_결과는_아무_줄도_안_남긴다() -> None:
    assert news_block_steps(object()) == []


# ══════════════════════════════════════════════════════════
# ④ 표 모양 계약
# ══════════════════════════════════════════════════════════


def test_표_머리글과_캡션은_한_곳에서만_나온다() -> None:
    assert NEWS_BLOCK_HEADERS == ("발행일", "매체 · 기사", "보도 내용")
    assert news_block_caption(2) == "최근 보도 (보조, 2기사)"


def test_9장_제외_목록은_수집과_싣기가_같은_객체를_쓴다() -> None:
    """값만 같으면 한쪽이 바뀔 때 조용히 갈린다 — «같은 객체»여야 한다.

    ★ 수집하는 쪽(news_intake)과 보고서에 싣는 쪽(composer)이 이 목록을 둘 다
      본다. 정본은 shared 하나뿐이고, feature 끼리 직접 import 하지 않는다.
    """

    from src.features.composer import news_block
    from src.features.news_intake import constants as news_constants
    from src.shared.report_evidence.constants import NEWS_EXCLUDED_SECTION_IDS

    assert news_constants.NEWS_EXCLUDED_SECTIONS is NEWS_EXCLUDED_SECTION_IDS
    assert news_block.NEWS_EXCLUDED_SECTION_IDS is NEWS_EXCLUDED_SECTION_IDS


def test_composer는_news_intake를_직접_import하지_않는다() -> None:
    """feature 간 직접 import 금지 — 공유는 shared를 경유한다.

    ★ 소스를 «다시 읽어» 확인한다 — import한 모듈 객체는 옛 바이트코드를
      쓸 수 있어 같은 길이의 수정을 못 본다.
    """

    import ast
    from pathlib import Path

    feature_dir = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for path in sorted(feature_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if module.startswith("src.features.news_intake"):
                    violations.append(f"{path.name}:{node.lineno}:{module}")

    assert violations == []


# ══════════════════════════════════════════════════════════
# ⑤ 캡션 연도 표시 — 1차 기간창(12개월)보다 오래된 기사가 섞이면
#   「최근」이 거짓이 되지 않도록 발행연도를 덧붙인다
#   (운영 실측: 2023-11-12 기사만 실린 장의 캡션이 「최근 보도」였다)
# ══════════════════════════════════════════════════════════


def _row(published_on: str, fragment_id: str = "40") -> NewsRow:
    return NewsRow(
        cells=(published_on, _PUBLISHER + " · 가나다전자 물류 자동화", _SENTENCE),
        citations=(fragment_id,),
        evidence_texts=(_SENTENCE,),
    )


def test_기간창_밖의_오래된_행이_있으면_그_연도를_돌려준다() -> None:
    assert oldest_stale_report_year((_row("2023-11-12"),), "2026-09-11") == 2023


def test_가장_오래된_행_하나만_기간창_밖이어도_그_연도를_쓴다() -> None:
    rows = (_row("2026-08-01", "40"), _row("2023-11-12", "41"))
    assert oldest_stale_report_year(rows, "2026-09-11") == 2023


def test_전부_기간창_안이면_None이다() -> None:
    rows = (_row("2026-08-01", "40"), _row("2025-10-01", "41"))
    assert oldest_stale_report_year(rows, "2026-09-11") is None


def test_정확히_12개월_전은_기간_안으로_본다() -> None:
    """news_usage.py의 기존 ``cutoff <= published <= today`` 관례와 맞춘다."""

    assert oldest_stale_report_year((_row("2025-09-11"),), "2026-09-11") is None
    assert oldest_stale_report_year((_row("2025-09-10"),), "2026-09-11") == 2025


@pytest.mark.parametrize("as_of_date", ("", "모름", "2026-13-40"))
def test_기준일이_없거나_형식이_다르면_None이다(as_of_date: str) -> None:
    assert oldest_stale_report_year((_row("2023-11-12"),), as_of_date) is None


def test_행_발행일_형식이_다르면_None이다() -> None:
    assert oldest_stale_report_year((_row("2023년 11월"),), "2026-09-11") is None


def test_행이_없으면_None이다() -> None:
    assert oldest_stale_report_year((), "2026-09-11") is None


def test_연도를_주면_캡션에_그_연도를_덧붙인다() -> None:
    assert news_block_caption(1, 2023) == "최근 보도 (보조, 1기사 · 2023년 보도 포함)"


def test_연도가_없으면_기존_캡션_그대로다() -> None:
    assert news_block_caption(2, None) == "최근 보도 (보조, 2기사)"
    assert news_block_caption(2) == "최근 보도 (보조, 2기사)"


def test_실제_렌더_진입점이_오래된_기사에_연도를_붙인다() -> None:
    """배선 시험 — ``render._news_report_table``은 이 보강이 실제로 붙는

    «운영 진입점»이다. 시험 안에서 따로 캡션을 다시 조립하지 않고, 그
    함수가 돌려준 문자열을 그대로 잰다.
    """

    from src.features.composer import render as render_module

    section = ComposedSection(
        section_id="identity", sentences=(), news_rows=(_row("2023-11-12"),)
    )

    table = render_module._news_report_table(  # noqa: SLF001
        section, {"40": 1}, as_of_date="2026-09-11"
    )

    assert table is not None
    assert table.caption == "최근 보도 (보조, 1기사 · 2023년 보도 포함)"


def test_실제_렌더_진입점은_최근_기사면_기존_캡션을_쓴다() -> None:
    from src.features.composer import render as render_module

    section = ComposedSection(
        section_id="identity", sentences=(), news_rows=(_row("2026-08-20"),)
    )

    table = render_module._news_report_table(  # noqa: SLF001
        section, {"40": 1}, as_of_date="2026-09-11"
    )

    assert table is not None
    assert table.caption == "최근 보도 (보조, 1기사)"


def test_기준일을_안_주면_오래된_기사여도_기존_캡션이다() -> None:
    """fail-safe — as_of_date 배선이 없는 옛 호출부를 깨지 않는다."""

    from src.features.composer import render as render_module

    section = ComposedSection(
        section_id="identity", sentences=(), news_rows=(_row("2023-11-12"),)
    )

    table = render_module._news_report_table(section, {"40": 1})  # noqa: SLF001

    assert table is not None
    assert table.caption == "최근 보도 (보조, 1기사)"


def test_FULL_봉인_표_항목도_같은_as_of_date로_같은_캡션을_만든다() -> None:
    """FULL 경로 — ``public_manifest._news_table_payload``는

    ``render._news_report_table``과 «같은 순서·같은 글자»를 만들어야 한다
    (그 함수 자체 docstring). ``as_of_date``를 안 넣으면 캡션만 renderer와
    달라져 봉인 대조가 보고서 «전체»를 막는다 — 이 시험은 그 대조가 실제로
    같은 글자를 내는지를 잰다.
    """

    from src.features.composer.public_manifest import _FragmentBinding, _news_table_payload
    from src.features.provenance.sources import exact_evidence_text_hash

    row = _row("2023-11-12")
    section = ComposedSection(section_id="identity", sentences=(), news_rows=(row,))
    binding = _FragmentBinding(
        fragment_id="40",
        document_identity="document:media.example:news-40",
        exact_evidence_hash=exact_evidence_text_hash(_SENTENCE),
        text=_SENTENCE,
        published_on="2023-11-12",
        publisher=_PUBLISHER,
        title="가나다전자 물류 자동화",
        is_news=True,
    )

    payload = _news_table_payload(section, {"40": binding}, as_of_date="2026-09-11")

    assert payload is not None
    assert payload["caption"] == "최근 보도 (보조, 1기사 · 2023년 보도 포함)"

    from src.features.composer import render as render_module

    rendered = render_module._news_report_table(  # noqa: SLF001
        section, {"40": 1}, as_of_date="2026-09-11"
    )
    assert rendered is not None
    assert payload["caption"] == rendered.caption


# ══════════════════════════════════════════════════════════
# ⑥ 배선 시험 — 헬퍼를 직접 불러 기준일을 손수 넣는 위 시험들은 「운영
#   진입점이 실제로 그 기준일을 넘기는가」를 못 잡는다(독립 검토 재현:
#   render.py·public_manifest.py·quality_projection.py 세 곳 중 어느
#   ``as_of_date=...`` 전달을 지워도 composer+web 전체가 그대로 통과했다).
#   그래서 여기서는 운영 진입점(render_report·build_public_structure_seal·
#   build_generation_quality_candidate)을 직접 불러, 그 배선 한 줄이
#   빠지면 해당 시험만 빨간불이 되게 만든다.
# ══════════════════════════════════════════════════════════

_WIRING_STALE_PUBLISHED_ON = "2023-11-12"
_WIRING_AS_OF_DATE = "2026-09-11"


def _wiring_fixture() -> tuple[ComposedReport, tuple[CollectedFragment, ...]]:
    """세 배선 시험이 공유하는 «오래된 기사 1건이 실린 1장(identity)»짜리 입력."""

    from src.features.composer.tests.test_news_block_channels import _news_fragment

    fragment = _news_fragment("40", _WIRING_STALE_PUBLISHED_ON, _SENTENCE)
    row = NewsRow(
        cells=(
            _WIRING_STALE_PUBLISHED_ON,
            _PUBLISHER + " · 가나다전자 물류 자동화 확대",
            _SENTENCE,
        ),
        citations=("40",),
        evidence_texts=(_SENTENCE,),
    )
    composed = ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(),
                news_rows=(row,) if section_id == "identity" else (),
            )
            for section_id in SECTION_IDS
        )
    )
    return composed, (fragment,)


def _news_table_from(rendered) -> object:
    return next(
        table
        for section in rendered.sections
        if section.cell == "identity"
        for table in section.tables
    )


def test_배선1_render_report_진입점이_기준일을_실제로_전달한다() -> None:
    """render.py의 ``_news_report_table(section, numbers, as_of_date=as_of_date)``

    배선(``render.py``의 ``news_table = _news_report_table(...)`` 줄)에서
    ``as_of_date=as_of_date``를 지우면(기본값 ``""``로 빠지면) 이 시험만
    빨간불이어야 한다.
    """

    from src.core import news_intake_switch
    from src.features.composer.render import render_report

    composed, fragments = _wiring_fixture()

    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
        rendered = render_report(
            "가나다전자", composed, fragments, None, as_of_date=_WIRING_AS_OF_DATE
        )
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001

    table = _news_table_from(rendered)
    assert table.caption == "최근 보도 (보조, 1기사 · 2023년 보도 포함)"


def test_배선2_seal_진입점이_기준일을_실제로_전달해_렌더와_일치한다() -> None:
    """public_manifest.py의 ``_news_table_payload(section, fragment_bindings,

    as_of_date=as_of_date)`` 배선을 지우면 정본 표 캡션이 렌더 캡션과
    달라져 ``assert_report_matches_public_structure``가
    ``PublicManifestError``를 던져야 한다.
    """

    from src.core import news_intake_switch
    from src.features.composer.constants import DEFAULT_CITATION_STYLE, SECTION_IDS as _SIDS
    from src.features.composer.public_manifest import (
        assert_report_matches_public_structure,
        build_public_structure_seal,
    )
    from src.features.composer.render import render_report

    composed, fragments = _wiring_fixture()
    packet_hashes = tuple(
        (section_id, f"{index:02d}" * 32) for index, section_id in enumerate(_SIDS, start=1)
    )

    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
        seal = build_public_structure_seal(
            composed,
            fragments,
            None,
            filing_meta=None,
            composition_tables=(),
            table_presentation="table",
            company_id="00123456",
            evidence_generation_sha256="a" * 64,
            evidence_packet_sha256s=packet_hashes,
            company_name="가나다전자",
            corp_type="",
            generated_at="",
            as_of_date=_WIRING_AS_OF_DATE,
            analysis_period="",
            latest_performance_period="",
            citation_style=DEFAULT_CITATION_STYLE,
        )
        rendered = render_report(
            "가나다전자",
            composed,
            fragments,
            None,
            as_of_date=_WIRING_AS_OF_DATE,
            public_structure_seal=seal,
            company_id="00123456",
        )
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001

    assert_report_matches_public_structure(rendered, seal)  # 예외 없으면 통과
    assert (
        _news_table_from(rendered).caption
        == "최근 보도 (보조, 1기사 · 2023년 보도 포함)"
    )


def test_배선3_shadow_대조_진입점이_기준일을_실제로_전달해_결속으로_본다() -> None:
    """quality_projection.py의 ``_only_bound_news_tables(..., as_of_date=

    rendered.as_of_date)`` 배선을 지우면(기본값 ``""``로 빠지면) 캡션
    불일치로 ``has_unbound_public_content``가 ``True``로 바뀌어야 한다.
    """

    from src.core import news_intake_switch
    from src.features.composer.quality_projection import (
        build_generation_quality_candidate,
    )
    from src.features.composer.render import render_report

    composed, fragments = _wiring_fixture()

    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
        rendered = render_report(
            "가나다전자", composed, fragments, None, as_of_date=_WIRING_AS_OF_DATE
        )
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001

    candidate = build_generation_quality_candidate(rendered, composed)

    identity = next(
        section for section in candidate.sections if section.section_id == "identity"
    )
    assert identity.has_unbound_public_content is False


# ══════════════════════════════════════════════════════════
# ⑦ (P2) 로컬 복제 달력 계산이 news_intake 정본과 값이 같은지 대조
#   (composer는 news_intake를 직접 import할 수 없어 계산을 복제했다 —
#   news_block._month_boundary의 docstring 참조. 값이 갈리면 composer가
#   판단하는 「1차 기간창」과 news_intake가 실제로 수집한 기간창이 서로
#   다른 경계를 쓰게 된다.)
#
#   이 시험 파일(composer/tests/*.py)은 test_composer는_news_intake를_
#   직접_import하지_않는다가 스캔하는 범위 밖이다 — 그 시험은
#   ``Path(__file__).resolve().parents[1]``(= composer/, tests/ 밖)의
#   ``*.py``만 훑고 하위 tests/ 폴더는 보지 않는다(비재귀 glob). 이 시험
#   자체를 실행해 실제로 안 걸리는 것까지 확인했다(아래 보고 참조) —
#   production 코드는 여전히 news_intake를 import하지 않는다.
# ══════════════════════════════════════════════════════════


def test_로컬_달력_계산이_news_intake_정본과_같은_값을_낸다() -> None:
    import datetime as dt

    from src.features.composer.news_block import _month_boundary as _composer_boundary  # noqa: SLF001
    from src.features.news_intake.search_snapshot import (
        month_boundary as _news_intake_boundary,
    )

    cases = [
        # (기준일, 개월) — 윤년 2월 29일, 월말 축소(3/31→2월엔 28·29일),
        # 연도 경계(1월→작년), 기준값(12개월)까지 훑는다.
        (dt.date(2026, 9, 11), 12),
        (dt.date(2026, 9, 11), 24),
        (dt.date(2026, 9, 11), 36),
        (dt.date(2026, 1, 15), 1),
        (dt.date(2024, 3, 31), 1),  # 2024=윤년, 2월은 29일까지만 있다
        (dt.date(2025, 3, 31), 1),  # 2025=평년, 2월은 28일까지만 있다
        (dt.date(2024, 2, 29), 12),  # 윤년 2/29 기준 1년 전 = 2023/2/28
        (dt.date(2026, 12, 31), 12),
        (dt.date(2000, 1, 1), 12),  # 400으로 나뉘는 윤년 경계
    ]
    for as_of, months in cases:
        assert _composer_boundary(as_of, months) == _news_intake_boundary(
            as_of, months
        ), (as_of, months)
