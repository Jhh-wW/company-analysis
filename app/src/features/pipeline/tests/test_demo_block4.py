"""데모 4번(회사 상황) 재도출 시험.

여기서 재는 것은 「저장된 뉴스 조각을 코드 규칙만으로 4번에 배치하는 부분」이다.
AI를 부르지 않으므로 돈이 들지 않는다.

★ 이 시험의 존재 이유는 «채우는 것»이 아니라 «잘못 채우지 않는 것»이다.
  칸을 채우기만 하고 못 쓸 문장이 들어가면 고친 게 아니라 망가뜨린 것이다.
"""

from __future__ import annotations

import pytest

from src.core.constants import CELL_LABELS, SITUATION_CELLS
from src.features.pipeline import demo
from src.features.pipeline.demo import (
    _PROBLEM_CELL,
    _REDRAWN_NOTICE,
    _SITUATION_MAX_LINES,
    _news_sentences,
    _pick_situation_lines,
    _redraw_situation,
)
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import ReportSection
from src.features.report_standard.constants import CANONICAL_SECTION_IDS
from src.features.spanselect.constants import NEWS_FRAGMENT_KIND

#: 저장된 보고서에 남아 있던 «거짓 사유». 기사를 6건이나 채택한 회사에도 붙어 있었다.
_STALE_NO_NEWS_REASON = "채택 조건(제목 회사명·3년·동명 단서)을 통과한 기사 없음"

#: 시험용 회사 이름. 실제 회사와 겹치지 않게 둔다.
_COMPANY = "시험회사"


# ══════════════════════════════════════════════════════════
# 시험용 재료 만들기
# ══════════════════════════════════════════════════════════


def _news(number: int, headline: str, body: str) -> tuple[int, str, str]:
    """1판 `collect_news`가 만드는 것과 같은 모양의 뉴스 조각 한 개.

    원문 꼴: `"(YYYY-MM-DD 보도 · 도메인) 제목. 본문"`
    """
    return (number, NEWS_FRAGMENT_KIND, f"(2026-01-02 보도 · news.example.com) {headline}. {body}")


def _empty_situation_sections() -> list[ReportSection]:
    """4번 세 칸이 전부 비어 있고 «옛 사유»가 붙은 보고서."""
    return [
        ReportSection(cell=cell, title=CELL_LABELS[cell], empty_reason=_STALE_NO_NEWS_REASON)
        for cell in SITUATION_CELLS
    ]


def _record() -> dict:
    return {"id": "시험-001", "input": {"company": _COMPANY, "job": "개발"}}


def _run_redraw(monkeypatch, fragments) -> tuple[list[ReportSection], frozenset[str]]:
    """조각 저장소를 시험용으로 바꿔 끼우고 재도출을 돌린다."""
    monkeypatch.setattr(demo, "_load_fragments", lambda _run_id: tuple(fragments))
    return _redraw_situation(_record(), _empty_situation_sections())


def _lines_of(sections: list[ReportSection]) -> list[str]:
    return [text for section in sections for text, _cite in section.lines]


# ══════════════════════════════════════════════════════════
# 배제 — 들어가면 안 되는 것 (이게 이 시험의 핵심이다)
# ══════════════════════════════════════════════════════════


def test_감사의견_문구는_4번에_안_들어간다(monkeypatch):
    """감사인 위험 신호는 4-1과 섞지 않는다.

    실측(재수집-p014)에서 「계속기업 존속능력에 유의적 의문」이 4-3에 그대로 실렸다.
    """
    감사문장 = "시험회사가 계속기업으로서의 존속능력에 유의적 의문을 받으며 설비 투자를 확대했다."
    정상문장 = "시험회사가 국내 첫 자동화 설비를 출시하고 해외 시장에 진출했다."
    sections, redrawn = _run_redraw(monkeypatch, [_news(1, "시험회사 소식", f"{감사문장} {정상문장}")])

    담긴_문장 = _lines_of(sections)
    assert 감사문장 not in 담긴_문장
    assert 정상문장 in 담긴_문장, "감사 문구를 뺀 자리에 멀쩡한 문장이 들어가야 한다"
    assert redrawn


def test_시세_기사는_4번에_안_들어간다(monkeypatch):
    """「주가가 빠졌습니다밖에 못 쓴다」."""
    시세문장 = "시험회사가 장중 급등하며 시가총액 1조원을 확보했다."
    sections, redrawn = _run_redraw(
        monkeypatch, [_news(1, "시험회사 주가, 1월 2일 1,000원 하락 마감", 시세문장)]
    )

    assert _lines_of(sections) == []
    assert redrawn == frozenset(), "시세 기사뿐이면 아무 칸도 재도출되지 않는다"


def test_회사가_주어가_아닌_문장은_안_들어간다(monkeypatch):
    """실측 — 「티웨이항공에 따르면 카카오페이로 …」는 티웨이항공의 행사다."""
    남의문장 = "가나항공에 따르면 시험회사로 15만원 이상 결제 시 3만원 할인이 적용된다."
    sections, _redrawn = _run_redraw(monkeypatch, [_news(1, "가나항공 제휴 할인", 남의문장)])

    assert _lines_of(sections) == []


def test_잘린_문장은_안_들어간다(monkeypatch):
    """뉴스 API가 본문을 「…」로 자른다. 반쪽 문장은 자소서에 한 글자도 못 쓴다."""
    잘린문장 = "시험회사가 지난 23일 한국에너지공과대학교와 발전기금 기부 약정을 체결하고..."
    sections, _redrawn = _run_redraw(monkeypatch, [_news(1, "시험회사 약정", 잘린문장)])

    assert _lines_of(sections) == []


def test_회사_소개_문장은_4번이_아니다(monkeypatch):
    """4-2는 «하는 일»이지 «무엇인가»가 아니다."""
    소개문장 = "시험회사는 1999년 분사해 2011년 코스닥에 상장한 1세대 산업용 로봇 제조 전문기업이다."
    sections, _redrawn = _run_redraw(monkeypatch, [_news(1, "시험회사 분석", 소개문장)])

    assert _lines_of(sections) == []


# ══════════════════════════════════════════════════════════
# 채우기 — 빈 칸에만, 상한을 지켜서
# ══════════════════════════════════════════════════════════


def test_4번에_이미_문장이_있으면_문장을_건드리지_않는다(monkeypatch):
    """재도출은 «빈 칸»에만 한다. AI가 고른 문장과 코드가 고른 문장을 섞지 않는다."""
    이미있던문장 = "당사는 지난 3년간 사업 구조를 바꿔 왔습니다."
    sections = [
        ReportSection(cell="4-1", title=CELL_LABELS["4-1"], empty_reason=_STALE_NO_NEWS_REASON),
        ReportSection(cell="4-2", title=CELL_LABELS["4-2"], lines=[(이미있던문장, "조각 4·MD&A")]),
        ReportSection(cell="4-3", title=CELL_LABELS["4-3"], empty_reason=_STALE_NO_NEWS_REASON),
    ]
    뽑힐만한문장 = "시험회사가 신규 공장을 설립하고 해외 시장에 진출했다."
    monkeypatch.setattr(
        demo, "_load_fragments", lambda _run_id: (_news(1, "시험회사 소식", 뽑힐만한문장),)
    )

    got, redrawn = _redraw_situation(_record(), sections)

    assert [s.lines for s in got] == [s.lines for s in sections], "문장이 새로 들어가면 안 된다"
    assert redrawn == frozenset()


def test_이미_채워진_보고서도_거짓_사유는_바로잡는다(monkeypatch):
    """★ 기사를 모아 놓고 「기사 없음」이라고 말하면 화면이 스스로 모순된다."""
    sections = [
        ReportSection(cell="4-1", title=CELL_LABELS["4-1"], empty_reason=_STALE_NO_NEWS_REASON),
        ReportSection(cell="4-2", title=CELL_LABELS["4-2"], lines=[("있던 문장입니다.", "조각 4·MD&A")]),
        ReportSection(cell="4-3", title=CELL_LABELS["4-3"], empty_reason=_STALE_NO_NEWS_REASON),
    ]
    monkeypatch.setattr(
        demo,
        "_load_fragments",
        lambda _run_id: (_news(1, "시험회사 소식", "시험회사가 신규 공장을 설립했다."),),
    )

    got, _redrawn = _redraw_situation(_record(), sections)

    비어있는칸 = [s for s in got if not s.is_filled]
    assert 비어있는칸, "시험 전제 — 빈 칸이 있어야 한다"
    assert all(_STALE_NO_NEWS_REASON not in s.empty_reason for s in 비어있는칸)
    assert all("1건" in s.empty_reason for s in 비어있는칸), "몇 건을 모았는지 밝혀야 한다"


def test_칸당_상한을_넘지_않는다():
    """4-1·4-2·4-3 각 3개."""
    news = tuple(
        (
            n,
            f"(2026-01-0{n} 보도 · news.example.com) 시험회사 {n}차 계약. "
            f"시험회사가 {n}번째 해외 고객사와 공급 계약을 체결했다.",
        )
        for n in range(1, 6)
    )
    picked = _pick_situation_lines(_COMPANY, news)

    assert len(picked["4-2"]) == _SITUATION_MAX_LINES


def test_한_기사에서_한_문장만_뽑는다():
    """같은 기사에서 여러 문장을 뽑으면 8번 교차표까지 같은 행으로 부푼다."""
    본문 = (
        "시험회사가 첫 번째 해외 고객사와 공급 계약을 체결했다. "
        "시험회사가 두 번째 해외 고객사와 공급 계약을 체결했다."
    )
    picked = _pick_situation_lines(_COMPANY, ((1, f"(2026-01-02 보도 · news.example.com) 시험회사 계약. {본문}"),))

    assert sum(len(v) for v in picked.values()) == 1


def test_방향_표지가_있으면_4_3으로_간다():
    """4-3은 「몇 년 뒤 방향」."""
    picked = _pick_situation_lines(
        _COMPANY,
        (
            (
                1,
                "(2026-01-02 보도 · news.example.com) 시험회사 확장. "
                "시험회사가 내년까지 해외 판매망을 확대할 계획이다.",
            ),
        ),
    )

    assert len(picked["4-3"]) == 1
    assert picked["4-2"] == []


def test_4_1은_코드로_채우지_않는다(monkeypatch):
    """「무엇이 문제인가」는 낱말로 못 가른다 — 근거 없는 배치를 하느니 비워 둔다."""
    sections, redrawn = _run_redraw(
        monkeypatch,
        [_news(1, "시험회사 계약", "시험회사가 해외 고객사와 공급 계약을 체결했다.")],
    )

    assert _PROBLEM_CELL not in redrawn
    problem = next(s for s in sections if s.cell == _PROBLEM_CELL)
    assert problem.lines == []


def test_재도출_문장은_뉴스에서_왔음을_표기한다(monkeypatch):
    """출처 표기를 보고 「이게 뉴스구나」를 알 수 있어야 한다 (기존 「조각 N·종류」 꼴)."""
    sections, _redrawn = _run_redraw(
        monkeypatch,
        [_news(7, "시험회사 계약", "시험회사가 해외 고객사와 공급 계약을 체결했다.")],
    )

    cites = [cite for section in sections for _text, cite in section.lines]
    assert cites == [f"조각 7·{NEWS_FRAGMENT_KIND}"]


# ══════════════════════════════════════════════════════════
# 빈칸 사유가 거짓이 되지 않는다
# ══════════════════════════════════════════════════════════


def test_기사가_있는데_기사_없음이라고_말하지_않는다(monkeypatch):
    """★ 데모가 거짓 사유를 말해 사고가 났다."""
    sections, _redrawn = _run_redraw(
        monkeypatch, [_news(1, "시험회사 주가, 1월 2일 하락 마감", "시험회사가 장중 급락했다.")]
    )

    reasons = [s.empty_reason for s in sections]
    assert all(_STALE_NO_NEWS_REASON not in r for r in reasons)
    assert all(r for r in reasons), "비운 칸에는 반드시 사유가 있어야 한다"


def test_뉴스가_아예_없으면_저장된_사유를_그대로_둔다(monkeypatch):
    """뉴스를 한 건도 못 모았으면 「기사 없음」이 사실이다. 바꾸면 오히려 거짓이 된다."""
    공시조각 = (1, "MD&A", "당사는 사업을 영위하고 있습니다.")
    sections, redrawn = _run_redraw(monkeypatch, [공시조각])

    assert redrawn == frozenset()
    assert all(s.empty_reason == _STALE_NO_NEWS_REASON for s in sections)


def test_재도출한_칸은_재도출했다고_밝힌다(monkeypatch):
    """숨기면 사용자가 「AI가 고른 것」으로 오해한다. 진짜 조사는 다른 문장을 낸다."""
    sections, _redrawn = _run_redraw(
        monkeypatch,
        [_news(1, "시험회사 계약", "시험회사가 해외 고객사와 공급 계약을 체결했다.")],
    )
    filled = next(s for s in sections if s.lines)

    noted = demo._note_redrawn(filled)
    assert noted.lines[0] == (_REDRAWN_NOTICE, "")
    assert demo._note_redrawn(noted) == noted, "여러 번 불러도 안내가 겹치면 안 된다"


# ══════════════════════════════════════════════════════════
# 문장 가르기
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "원문, 남는수",
    [
        # 끝이 잘린 토막은 버린다 — 실측(루트로닉 조각 10)의 모양이다
        (
            "첫 문장은 스물다섯 글자를 넘기도록 충분히 길게 적어 둔 온전한 문장입니다. "
            "두 번째 문장은 여기서 잘렸습니다만 계속 이어지다 전...",
            1,
        ),
        # 너무 짧은 토막은 표 찌꺼기다
        ("짧다. 이 문장은 스물다섯 글자를 넘기도록 충분히 길게 적은 문장입니다.", 1),
        # ★ 「…」로 끝나면 **서술 종결이어도 버린다** — 아래 주석 참고
        ("시험회사가 유럽 인증을 받으며 수출 노선을 확보했다....", 0),
    ],
)
def test_잘리거나_짧은_토막은_문장으로_안_센다(원문, 남는수):
    """★ 마지막 칸의 기대값이 1 → 0으로 «바뀌었다». 그 이유를 남긴다.

    예전에는 「…로 끝나도 서술 종결이면 남긴다 — 뉴스 API가 뒤에 붙이는 자리표시자다」
    였다. 그런데 진짜 조사 쪽(`spanselect`)은 **꼬리에 「…」가 있으면 버린다.**
    두 규칙이 서로 다른 폴더에 있어서 **아무도 충돌을 못 봤고**, 결과적으로
    같은 문장이 유료 조사에서는 빠지고 무료 데모에는 실렸다.

    ★ 어느 쪽을 남길지 **실측으로 정했다** — 저장된 뉴스 문장 **149개** 기준:
      · 「…」 + 문장으로 안 끝남 → **55개** (두 규칙 모두 버림, 이견 없음)
      · 「…」 + 문장으로 끝남   → **2개만** 갈린다 (1.3%)

      그 2개를 직접 봤더니 **1대 1 무승부**였다:
        ⓐ 「…수출 노선을 확보했다....」 — 문장이 온전하다. **버리면 쓸 재료를 잃는다**
        ⓑ 「…LG그룹 품었지만 실적은 뒷걸음...」 — 잘린 제목인데, 「뒷걸음」의 **「음」**
           때문에 종결형 검사를 **우연히 통과**한다. **남기면 쓰레기가 실린다**

    ★ 그래서 **엄격한 쪽(버림)을 골랐다.** 근거 셋:
      ① 유료 조사 2회로 **이미 검증됐다** (루트로닉·파마리서치 출력이 깨끗해짐)
      ② 느슨한 쪽은 ⓑ 같은 쓰레기를 **확실히 통과시킨다**
      ③ 149개 중 1개를 잃는 값보다, 사용자 화면에 「확보했다....」가 보이는 값이 크다

    ⚠️ **알면서 치르는 비용이다** — 149문장에 약 1개꼴로 **쓸 만한 문장을 잃는다.**
      나중에 「점 앞이 «동사 어미»인지」까지 보면 둘 다 살릴 수 있을지 모르나,
      **표본이 2개뿐이라 지금 만들면 그 2개에 맞춘 규칙**이 된다. 재료가 쌓이면 다시 본다.
    """
    assert len(_news_sentences(원문)) == 남는수


# ══════════════════════════════════════════════════════════
# ★ 저장소 안의 개인정보 없는 작은 fixture 회귀
# ══════════════════════════════════════════════════════════


def test_작은fixture의_쓸수있는_기사는_4번에_채워진다(monkeypatch):
    sentence = "시험회사가 자동화 설비를 출시하고 해외 고객사 공급을 확대했다."
    sections, _redrawn = _run_redraw(
        monkeypatch,
        [_news(1, "시험회사 공급 확대", sentence)],
    )

    assert sentence in _lines_of(sections)
    assert any(section.lines for section in sections)


def test_작은fixture의_canonical_데모는_현재_9개_정본장만_가진다():
    report = build_demo_report()

    assert tuple(section.cell for section in report.sections) == CANONICAL_SECTION_IDS
    assert all(section.lines for section in report.sections)


def test_작은fixture의_재도출_안내는_회사사실로_재사용되지_않는다(monkeypatch):
    sections, _redrawn = _run_redraw(
        monkeypatch,
        [_news(1, "시험회사 신제품", "시험회사가 산업용 센서를 출시했다.")],
    )

    assert _REDRAWN_NOTICE not in _lines_of(sections)


def test_작은fixture의_감사의견과_시세는_4번에서_제외된다(monkeypatch):
    sections, _redrawn = _run_redraw(
        monkeypatch,
        [
            _news(1, "시험회사 감사", "시험회사는 계속기업 존속능력에 유의적 의문이 있다."),
            _news(2, "시험회사 주가", "시험회사의 주가는 장중 10퍼센트 급등했다."),
        ],
    )

    assert _lines_of(sections) == []


def test_작은fixture에_기사가_있으면_기사없음_사유를_그대로_두지않는다(monkeypatch):
    sections, _redrawn = _run_redraw(
        monkeypatch,
        [_news(1, "시험회사 공장", "시험회사가 국내 생산 설비를 증설했다.")],
    )

    for section in sections:
        if not section.lines:
            assert _STALE_NO_NEWS_REASON not in section.empty_reason
