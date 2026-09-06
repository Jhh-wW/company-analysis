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
    NEWS_BLOCK_MAX_ROWS,
    NEWS_BLOCK_STEP,
    augment_news_blocks,
    news_block_caption,
    news_block_steps,
    news_ownership_from_claim_slots,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
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
        source_url="https://media.example/news/one",
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
        ("2026-09-01", _PUBLISHER, _SENTENCE)
    ]
    assert _table_rows(result, "portfolio") == [
        ("2026-09-01", _PUBLISHER, _SENTENCE)
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
    assert rows == [("2026-09-01", _PUBLISHER, _QUOTED)]
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
    assert _table_rows(result, "identity")[0][1] == _OTHER_PUBLISHER


def test_장당_행_수는_상한을_넘지_않는다() -> None:
    ids = tuple(str(70 + offset) for offset in range(NEWS_BLOCK_MAX_ROWS + 2))
    fragments = tuple(
        _news(fragment_id, published_on=f"2026-09-0{index + 1}")
        for index, fragment_id in enumerate(ids)
    )
    result = augment_news_blocks(
        _report(),
        fragments,
        allowed_fragment_ids_by_section=_owned(identity=ids),
    )

    assert len(_table_rows(result, "identity")) == NEWS_BLOCK_MAX_ROWS
    assert dict(result.blocked_counts_by_reason)[BLOCKED_ROW_LIMIT] == (
        len(ids) - NEWS_BLOCK_MAX_ROWS
    )


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
        ("2026-09-01", _PUBLISHER, _SENTENCE)
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
        ("2026-09-01", _PUBLISHER, _SENTENCE)
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
    assert NEWS_BLOCK_HEADERS == ("발행일", "매체", "보도 문장")
    assert news_block_caption(2) == "최근 보도 (보조, 2)"


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
