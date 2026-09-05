"""장별 표 계약을 못 박는다 — 목업 품질을 따라잡기 위해 늘린 부분.

★ 왜 늘렸나 (사용자가 완성 기준으로 정한 목업과의 대조)
  목업 9개 장에는 시각 요소가 11개 있었는데 우리 출력에는 4개뿐이었다.
  목업의 표들을 뜯어 보니 «숫자 표»가 아니라 AI가 쓰는 말을 칸에 나눠
  담은 것이었다:
      1장 「공식 자기정의 / 사업 범위 / 이 보고서의 해석」
      6장 「시점 / 계획 / 공시된 내용」
      8장 「내건 가치 / 일하는 원칙 / 확인된 사례」
  재료가 없어서 못 만든 게 아니라, 우리가 AI에게서 받는 그릇이 «문장 배열»
  하나뿐이라 무엇을 쓰든 줄글로 떨어진 것이었다.

★ 새 «종류»를 만들지 않았다. 5·7장이 쓰는 흐름표 계약에 장만 늘렸다.
  새 종류를 만들면 웹·PDF 렌더러를 각각 새로 짜야 하고, 그러다 한쪽만
  고쳐 화면과 인쇄물이 어긋난 사고가 이미 두 번 났다.

★ 9장은 «표를 만들지 않는다» (제품 결정).
  공시는 경쟁 관계를 말하면서도 상대 이름을 거의 밝히지 않는다
  (실측: 4개사 원문에서 법인 지목 0건). 이름 없는 비교표는 지어낸 비교다.
  대신 동종업계 «경쟁우위»를 산문으로 쓴다.

★ 3장도 나중에 같은 방식으로 늘렸다 — 하이브 실측에서 3장이
  문장 2개·표 0개로 9개 장 중 가장 빈약했다. 「목업에도 3장 시각 요소가
  없다」는 진행로그 기록은 목업을 잘못 읽은 것이었다(재확인: 목업 3장에
  제품 카드 2개 실존). 3장 헤더 「제품·서비스명 / 제품·서비스 범위 /
  중점 추진 근거 / 사업적 역할」은 목업 4행 중 「2장 수익 분류 참조」·
  「해석 한계」 두 줄을 뺀 것이다(이유는 constants.py 주석).
"""

from __future__ import annotations

import json

from src.features.composer.constants import (
    CULTURE_TABLE_HEADERS,
    CULTURE_TABLE_SECTION_ID,
    FLOW_CAPTION_BY_SECTION,
    FLOW_HEADERS_BY_SECTION,
    FLOW_PRESENTATION,
    FLOW_PROMPT_BY_SECTION,
    IDENTITY_TABLE_HEADERS,
    IDENTITY_TABLE_SECTION_ID,
    PORTFOLIO_TABLE_HEADERS,
    PORTFOLIO_TABLE_SECTION_ID,
    SECTION_IDS,
    SECTION_TITLES,
    STRATEGY_TABLE_GUIDE,
    STRATEGY_TABLE_HEADERS,
    STRATEGY_TABLE_SECTION_ID,
)
from src.features.composer.logic import build_section_prompt, parse_flow_rows
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.render import render_report

_원문 = (
    "회사는 스스로를 글로벌 콘텐츠 기업으로 규정하며 음악·영상 사업을 영위한다. "
    "2026년까지 해외 법인을 확대할 계획이다. "
    "공식 가치는 「함께 성장한다」이며 사내 육성 과정을 운영한다."
)


def _fragments() -> dict[int, dict[str, str]]:
    return {1: {"종류": "사업내용", "원문": _원문}}


def _fragment_objs() -> tuple[CollectedFragment, ...]:
    return (CollectedFragment(fragment_id="1", kind="사업내용", text=_원문),)


def _report(section_id: str, rows: tuple[FlowRow, ...]) -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=sid,
                sentences=(
                    ComposedSentence(
                        text="회사는 음악·영상 사업을 영위한다.",
                        citations=("1",),
                        grade="확인",
                    ),
                ),
                flow_rows=rows if sid == section_id else (),
            )
            for sid in SECTION_IDS
        ),
        summary=(
            ComposedSentence(text="콘텐츠 기업이다.", citations=("1",), grade="확인"),
            ComposedSentence(text="해외를 넓힌다.", citations=("1",), grade="확인"),
            ComposedSentence(text="함께 성장을 내건다.", citations=("1",), grade="확인"),
        ),
    )


def _section_of(report, cell: str):
    return next(s for s in report.sections if s.cell == cell)


# ══════════════════════════════════════════════════════════
# ① 목업이 요구한 여러 장이 표를 낸다
# ══════════════════════════════════════════════════════════
#
# ★ 이름을 「세 장」에서 「여러 장」으로 고쳤다(3장 추가) —
#   숫자를 이름에 박으면 장이 늘 때마다 이름도 거짓말이 된다.

_새_장 = (
    (IDENTITY_TABLE_SECTION_ID, IDENTITY_TABLE_HEADERS),
    (PORTFOLIO_TABLE_SECTION_ID, PORTFOLIO_TABLE_HEADERS),
    (STRATEGY_TABLE_SECTION_ID, STRATEGY_TABLE_HEADERS),
    (CULTURE_TABLE_SECTION_ID, CULTURE_TABLE_HEADERS),
)


def test_목업이_요구한_여러_장이_표_그릇을_갖는다():
    for section_id, headers in _새_장:
        assert FLOW_HEADERS_BY_SECTION.get(section_id) == headers, section_id


def test_새_장의_표가_화면까지_도달한다():
    for section_id, headers in _새_장:
        rows = (
            FlowRow(cells=tuple(f"칸{i}" for i in range(len(headers))), citations=("1",)),
        )
        report = render_report("가나다전자", _report(section_id, rows), _fragments(), None)

        장 = _section_of(report, section_id)
        assert 장.tables, f"{section_id}: 표가 없습니다"
        assert 장.tables[0].presentation == FLOW_PRESENTATION, section_id
        assert 장.tables[0].headers == list(headers), section_id
        assert 장.tables[0].caption == FLOW_CAPTION_BY_SECTION[section_id]


def test_근거가_없으면_표를_만들지_않는다():
    """★ 빈 그릇을 남기지 않는다 — 자료가 없으면 표 자체가 없다."""
    for section_id, headers in _새_장:
        가짜 = (
            FlowRow(
                cells=tuple(f"칸{i}" for i in range(len(headers))),
                citations=("99",),  # 실존하지 않는 조각
            ),
        )
        report = render_report("가나다전자", _report(section_id, 가짜), _fragments(), None)

        assert _section_of(report, section_id).tables == [], section_id


# ══════════════════════════════════════════════════════════
# ② 9장은 표를 «만들지 않는다» (제품 결정)
# ══════════════════════════════════════════════════════════


def test_9장은_표를_만들지_않는다():
    """★ 공시가 경쟁사 «이름»을 거의 안 밝힌다 — 이름 없는 비교는 지어낸 비교다.

    실측: JYP 23만자 0건 / 현대차 144만자 0건 / 진영 16만자 0건 /
    하이브 37만자 0건 (DART 법인을 지목한 문장 수).
    """
    assert "competitive_position" not in FLOW_HEADERS_BY_SECTION
    assert "competitive_position" not in FLOW_PROMPT_BY_SECTION


def test_9장_제목과_지침이_회사자기선언을_말한다():
    from src.features.composer.constants import SECTION_GUIDES

    assert SECTION_TITLES["competitive_position"] == "회사가 밝힌 차별점"
    지침 = SECTION_GUIDES["competitive_position"]
    assert "회사" in 지침 and "공식 출처" in 지침


def test_9장_지침이_회사선언과_작성자판단을_분리한다():
    from src.features.composer.constants import SECTION_GUIDES

    지침 = SECTION_GUIDES["competitive_position"]
    assert "사실 여부" in 지침
    assert "우열을 작성자가 판단하지 않는다" in 지침


def test_9장_지침이_닫힌_선언표지와_금지평가어를_말한다():
    from src.features.composer.constants import SECTION_GUIDES

    지침 = SECTION_GUIDES["competitive_position"]
    for marker in ("최초", "유일", "최다", "1위", "최대", "독자 개발", "특허"):
        assert marker in 지침
    assert "더 낫다" in 지침 and "쓰지 않는다" in 지침


def test_9장_근거부족_지침이_출고품질계약과_같은_경계를_말한다():
    """작가는 한두 문장 폴백을 내는데 FULL 검사가 항상 막는 모순을 되살리지 않는다."""
    from src.features.composer.constants import SECTION_GUIDES
    from src.shared.report_quality.evidence_support import (
        MIN_PROSE_EVIDENCE_SUPPORT_TERMS,
    )

    지침 = SECTION_GUIDES["competitive_position"]
    assert f"근거어를 최소 {MIN_PROSE_EVIDENCE_SUPPORT_TERMS}개" in 지침
    assert "추측으로 채우지 않는다" in 지침


# ══════════════════════════════════════════════════════════
# ③ 프롬프트 계약 — 장이 늘어도 깨지지 않는다
# ══════════════════════════════════════════════════════════


def test_모든_표_장이_출력형식_안내를_하나만_갖는다():
    """★ 둘이면 작가가 앞의 「이 JSON만 출력한다」를 따라 표를 빼먹는다."""
    for section_id in FLOW_HEADERS_BY_SECTION:
        prompt = build_section_prompt("가나다전자", section_id, _fragment_objs(), None)
        assert prompt.count("설명·머리말 없이") == 1, section_id


def test_각_장의_칸_이름이_그_장_프롬프트에만_나온다():
    """장을 늘릴 때 남의 칸 이름이 새면 작가가 엉뚱한 칸을 채운다."""
    for section_id, headers in _새_장:
        # ★ 조립된 프롬프트가 아니라 «그 장의 지침»만 본다. 조립본에는 수집
        #   조각 원문이 통째로 들어가서, 회사가 「…할 계획이다」라고 쓰기만
        #   해도 남의 칸 이름이 들어 있는 것처럼 보인다(실제로 오탐이 났다).
        지침 = FLOW_PROMPT_BY_SECTION[section_id]
        for name in headers:
            assert name in 지침, f"{section_id}: 「{name}」 없음"
        남의칸 = {
            name
            for other, other_headers in FLOW_HEADERS_BY_SECTION.items()
            if other != section_id
            for name in other_headers
        } - set(headers)
        # ★ 정정 — 3장을 추가하며 또 다른 오탐 유형이 나왔다.
        #   2장 칸 「제품·서비스」는 3장 칸 「제품·서비스명」·「제품·서비스
        #   범위」의 «부분 문자열»이다. 이건 남의 칸이 «새어 든» 게 아니라
        #   내 칸 이름 자체에 그 낱말이 들어 있는 것뿐이다(3장 표제가
        #   원래 «핵심 제품·서비스와 포트폴리오 역할»이니 당연하다). 남의
        #   칸이 «내 칸 이름의 부분»이면 leak이 아니라고 본다 — 남의 칸이
        #   «내 칸과 무관하게 통째로 튀어나왔을 때»만 진짜 leak이다.
        남의칸 = {
            name for name in 남의칸 if not any(name in own for own in headers)
        }
        샌_것 = [name for name in 남의칸 if name in 지침]
        assert not 샌_것, f"{section_id}에 남의 칸 이름이 샜습니다: {샌_것}"

        # 조립본에도 «자기 칸»은 반드시 들어 있어야 한다.
        prompt = build_section_prompt("가나다전자", section_id, _fragment_objs(), None)
        for name in headers:
            assert name in prompt, f"{section_id}: 조립본에 「{name}」 없음"


def test_장마다_칸_수가_다른_것을_파서가_지킨다():
    for section_id, headers in _새_장:
        raw = json.dumps(
            {
                "문장들": [{"글": "가나다.", "인용": ["1"], "등급": "확인"}],
                "경로표": [
                    {"칸": [f"칸{i}" for i in range(len(headers))], "인용": ["1"]},
                    {"칸": ["칸이", "모자람"], "인용": ["1"]},
                ],
            },
            ensure_ascii=False,
        )

        rows = parse_flow_rows(raw, section_id)

        # 칸 수가 맞는 줄만 남는다 (2칸 계약인 장이면 반대로 걸러진다)
        assert all(len(row.cells) == len(headers) for row in rows), section_id


# ══════════════════════════════════════════════════════════
# ④ 6장 «계획» 칸 순서 정정 (실측 결함)
# ══════════════════════════════════════════════════════════
#
# ★ 왜 이 시험이 있나 — 하이브 실측에서 6장 1행의 「시점」 칸에 시점이 아니라
#   주제(「글로벌 시장 진출」)가 들어갔다. 원인: 흐름표를 쓰는 6개 장 중
#   1·2·5·7·8장은 전부 «1번 칸 = 그 줄의 주제»인데 6장만 1번 칸이 시간
#   속성(시점)이라 주제를 담을 자리가 없었다 — AI 잘못이 아니라 구조 문제
#   였다(전수대조 조사 결론).
#   「계획」을 1번 칸으로 옮기고 지침에 정의를 추가하는 두 조치를 «함께»
#   해야 재발하지 않는다 — 이 시험은 그 두 조치가 실제로 남아 있는지 잠근다.


def test_6장_칸_순서가_계획_시점_공시된_내용이다():
    """★★ 순서 자체가 계약이다 — 다시 시점을 1번으로 되돌리면 재발한다."""
    assert STRATEGY_TABLE_HEADERS == ("계획", "시점", "공시된 내용")


def test_6장_지침에_계획_칸의_정의가_있다():
    """★ 정의 없이 칸만 옮기면 AI가 여전히 무엇을 써야 할지 모른다."""
    assert "「계획」은 이 줄의" in STRATEGY_TABLE_GUIDE
    # 「시점」·「공시된 내용」 정의도 이번에 안 잃었는지 함께 확인한다.
    assert "「시점」은 공식 자료에 적힌 대로" in STRATEGY_TABLE_GUIDE
    assert "「공시된 내용」은 회사가 실제로 밝힌" in STRATEGY_TABLE_GUIDE


def test_6장_프롬프트_스키마도_새_순서를_그대로_반영한다():
    """★ _flow_schema_guide가 STRATEGY_TABLE_HEADERS «순서 그대로» JSON 칸 목록을
    만든다 — 여기서 헤더 순서와 스키마 문구가 어긋나면 작가가 옛 순서로 쓴다.
    """
    지침 = FLOW_PROMPT_BY_SECTION[STRATEGY_TABLE_SECTION_ID]
    계획_위치 = 지침.find('"<계획>"')
    시점_위치 = 지침.find('"<시점>"')
    공시_위치 = 지침.find('"<공시된 내용>"')
    assert -1 not in (계획_위치, 시점_위치, 공시_위치), 지침
    assert 계획_위치 < 시점_위치 < 공시_위치, "스키마의 칸 순서가 헤더 순서와 다릅니다"
