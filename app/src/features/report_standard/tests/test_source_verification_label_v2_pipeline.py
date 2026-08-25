"""부록 「사실 검증」 칸을 «실제 render 파이프라인»으로 지킨다.

★ 왜 이 파일이 따로 생겼나 (2026-08-25, 적대 검수가 재현한 결함)
  ─────────────────────────────────────────────────────────
  옆 파일(`test_source_verification_label_v2.py`)의 시험들은 전부 «손으로 지은»
  `prose_lines` 문자열을 쓴다 — 인용 번호 `[1]` 을 사람이 직접 박아 넣는다.
  그래서 render가 «번호를 언제 숨기는가»라는 진짜 규칙을 재현하지 못했고,
  아래 결함이 그 그물을 그대로 통과했다.

  재현된 결함:
    확인 문장: "…매출 100억원을 기록했다."   citations=("A",)  → 화면에 [1] 보임
    해석 문장: "…업계 평균을 웃도는 성과다."  citations=("A",)  → 화면에 [1] «안» 보임
  같은 자료 A를 «확인»과 «해석»이 함께 근거로 썼는데, render의 절충안 규칙이
  해석 쪽 번호를 숨긴다(같은 번호를 확인 문장이 이미 보여 주고 있으니
  `_ensure_no_orphan_markers` 가 되살릴 필요가 없다). 화면 글자만 되짚으면
  「해석도 이 자료를 썼다」를 못 보고 **「사실 검증 완료」**라고 적는다.
  사용자가 정한 규칙은 「해석이 섞이면 부분 검증」이므로 이건 오표시다.

  ★ 「사실 언급 → 그 사실의 해석」은 이 엔진이 **기본으로** 만드는 문장 배열이다
    (`render.py` 의 문단 묶기 주석: 「해석 문장은 앞 문장의 뜻풀이라 묶음을
    끊지 않는다」). 즉 드문 예외가 아니라 자주 나오는 모양이다.

  고친 방법: render가 「번호를 보였는지와 무관하게」 아는 등급을
  `Report.source_grades` 에 실어 보내고, 부록이 그것을 읽는다.

★ 이 파일의 모든 시험은 `render_report()` 를 «실제로» 통과시킨다.
  손으로 지은 문자열을 쓰지 않는다 — 그게 위 결함을 놓친 이유였다.
"""

from __future__ import annotations

from typing import Final

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    OPERATIONS_FLOW_SECTION_ID,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.render import render_report
from src.features.report_standard.section_content import (
    SOURCE_VERIFICATION_TABLE_ONLY_LABEL,
    _V2_INTERPRETED_GRADE,
    source_verification_label,
)

_FRAGMENT_ID: Final[str] = "1"


def _fragments() -> dict[int, dict[str, str]]:
    return {
        1: {
            "종류": "사업내용",
            "원문": (
                "회사는 2024년에 매출 100억원을 기록했으며, 이는 직전 회계연도보다 "
                "늘어난 수치다."
            ),
        }
    }


def _report(
    *sentences: ComposedSentence,
    summary: tuple[ComposedSentence, ...] | None = None,
) -> ComposedReport:
    """문장들을 «첫 장»에만 담은 v2 보고서. 나머지 장은 빈 장으로 둔다.

    ``summary``를 주면 핵심 요약을 갈아 끼운다 — 요약 쪽 등급을 다루는
    시험(아래 ④)에 필요하다. 안 주면 기본 요약을 쓴다.
    """
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=sentences if index == 0 else (),
            )
            for index, section_id in enumerate(SECTION_IDS)
        ),
        summary=(
            summary
            if summary is not None
            else (
                ComposedSentence(
                    text="매출이 늘었다.",
                    citations=(_FRAGMENT_ID,),
                    grade=GRADE_CONFIRMED,
                ),
                ComposedSentence(
                    text="증가 폭이 눈에 띈다.",
                    citations=(_FRAGMENT_ID,),
                    grade=GRADE_CONFIRMED,
                ),
                ComposedSentence(
                    text="성장 국면으로 읽힌다.", citations=(), grade=GRADE_INTERPRETED
                ),
            )
        ),
    )


def _label_of_first_source(report) -> str:
    assert report.citations, "부록이 비었습니다 — 재료가 잘못됐습니다"
    return source_verification_label(report, report.citations[0].source_id)


# ══════════════════════════════════════════════════════════
# ① 재현된 결함 — 확인과 해석이 «같은 자료»를 쓸 때
# ══════════════════════════════════════════════════════════


def test_확인과_해석이_같은_자료를_쓰면_부분_검증이다() -> None:
    """★ 이 시험이 없어서 놓쳤던 바로 그 경우.

    화면에는 해석 문장의 `[1]` 이 «안 보인다». 그래도 그 자료는 해석에도
    쓰였으므로 「부분 검증」이 맞다.
    """
    rendered = render_report(
        "가나다전자",
        _report(
            ComposedSentence(
                text="회사는 2024년에 매출 100억원을 기록했다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_CONFIRMED,
            ),
            ComposedSentence(
                text="이는 업계 평균을 웃도는 성과로 해석된다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_INTERPRETED,
            ),
        ),
        _fragments(),
        None,
    )

    # 전제 확인 — 해석 문장의 번호가 실제로 숨겨져 있어야 이 시험이 의미가 있다.
    본문 = [text for section in rendered.sections for text, _ in section.prose_lines]
    해석문장 = next(text for text in 본문 if "해석된다" in text)
    assert "[1]" not in 해석문장, (
        f"해석 문장에 번호가 보입니다 — render 규칙이 바뀌었다면 이 시험의 "
        f"전제를 다시 확인하세요: {해석문장!r}"
    )

    assert _label_of_first_source(rendered) == "부분 검증"


def test_확인_문장만_있으면_사실_검증_완료다() -> None:
    rendered = render_report(
        "가나다전자",
        _report(
            ComposedSentence(
                text="회사는 2024년에 매출 100억원을 기록했다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_CONFIRMED,
            ),
            ComposedSentence(
                text="직전 회계연도보다 늘어난 수치다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_CONFIRMED,
            ),
        ),
        _fragments(),
        None,
    )

    assert _label_of_first_source(rendered) == "사실 검증 완료"


# ══════════════════════════════════════════════════════════
# ② 등급을 나르는 통로 자체를 지킨다
# ══════════════════════════════════════════════════════════


def test_render가_등급을_번호별로_실어_보낸다() -> None:
    """`source_grades` 가 비면 부록은 옛 폴백(화면 글자 되짚기)으로 떨어진다 —
    그 폴백이 바로 위 결함을 놓치는 길이므로, 통로가 «채워지는지»를 못 박는다.
    """
    rendered = render_report(
        "가나다전자",
        _report(
            ComposedSentence(
                text="회사는 2024년에 매출 100억원을 기록했다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_CONFIRMED,
            ),
            ComposedSentence(
                text="이는 업계 평균을 웃도는 성과로 해석된다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_INTERPRETED,
            ),
        ),
        _fragments(),
        None,
    )

    assert rendered.source_grades, "render가 등급을 안 실어 보냈습니다"
    assert set(rendered.source_grades["1"]) == {GRADE_CONFIRMED, GRADE_INTERPRETED}


def test_해석_등급_상수가_composer_원본과_같다() -> None:
    """★ 이 값도 composer에서 «베껴 온» 것이라 어긋나도 아무도 모른다.

    어긋나면 `any(grade == _V2_INTERPRETED_GRADE ...)` 가 영원히 거짓이 되어
    **모든 자료가 「사실 검증 완료」** 로 나온다 — 방향만 반대인 거짓말이다.
    """
    assert _V2_INTERPRETED_GRADE == GRADE_INTERPRETED


# ══════════════════════════════════════════════════════════
# ③ 이 자료를 안 쓴 경우
# ══════════════════════════════════════════════════════════


def test_본문이_그_자료를_안_쓰면_본문_사실_없음이다() -> None:
    """부록에 실렸는데 본문 어디서도 안 쓴 자료 — 원래 결함이 «모든» 자료에
    붙이던 그 문구가, 이제는 진짜 그런 자료에만 붙는지 확인한다.
    """
    fragments = _fragments()
    fragments[2] = {"종류": "사업내용", "원문": "아무도 인용하지 않는 조각이다."}

    rendered = render_report(
        "가나다전자",
        _report(
            ComposedSentence(
                text="회사는 2024년에 매출 100억원을 기록했다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_CONFIRMED,
            ),
        ),
        fragments,
        None,
    )

    안_쓴_자료 = [
        source for source in rendered.citations if str(source.number) == "2"
    ]
    if not 안_쓴_자료:
        # 부록은 «인용된» 조각만 싣는다 — 그러면 이 경우 자체가 생기지 않는다.
        # 그것도 정상이므로 여기서 끝낸다(없는 상황을 지어내지 않는다).
        return
    assert (
        source_verification_label(rendered, 안_쓴_자료[0].source_id)
        == "본문 사실 없음"
    )


# ══════════════════════════════════════════════════════════
# ④ 핵심 요약의 «해석»도 등급이다 (2026-08-25 적대 검수가 재현)
# ══════════════════════════════════════════════════════════


def test_요약의_해석이_같은_자료를_쓰면_부분_검증이다() -> None:
    """★ 재현된 결함 — 요약 문장의 등급이 통째로 빠져 있었다.

    render는 요약 인용을 부록 번호에는 실으면서(`used_sections.setdefault`)
    «등급»은 안 담았다. 요약 문장도 본문과 같은 `verify_sentences`를 타므로
    「해석」으로 강등될 수 있고, 그때 인용은 그대로 남는다. 그러면 본문이
    전부 「확인」일 때 부록은 「사실 검증 완료」라고 적는다 — 그 자료를
    쓴 문장 중에 해석이 있으므로 「부분 검증」이 맞다.
    """
    rendered = render_report(
        "가나다전자",
        _report(
            ComposedSentence(
                text="회사는 2024년에 매출 100억원을 기록했다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_CONFIRMED,
            ),
            summary=(
                ComposedSentence(
                    text="매출이 늘었다.",
                    citations=(_FRAGMENT_ID,),
                    grade=GRADE_CONFIRMED,
                ),
                ComposedSentence(
                    text="성장 국면으로 읽힌다.",
                    citations=(_FRAGMENT_ID,),
                    grade=GRADE_INTERPRETED,
                ),
            ),
        ),
        _fragments(),
        None,
    )

    # 전제 확인 — 본문에는 해석이 하나도 없다. 그러니 아래 「부분 검증」은
    # 오직 «요약» 때문이어야 한다.
    본문 = [text for section in rendered.sections for text, _ in section.prose_lines]
    assert not any("해석" in text for text in 본문), f"본문에 해석이 있습니다: {본문}"

    assert _label_of_first_source(rendered) == "부분 검증"


def test_render가_요약_문장의_등급도_실어_보낸다() -> None:
    """통로 자체를 지킨다 — 라벨만 보면 «왜» 맞았는지 알 수 없다."""
    rendered = render_report(
        "가나다전자",
        _report(
            ComposedSentence(
                text="회사는 2024년에 매출 100억원을 기록했다.",
                citations=(_FRAGMENT_ID,),
                grade=GRADE_CONFIRMED,
            ),
            summary=(
                ComposedSentence(
                    text="성장 국면으로 읽힌다.",
                    citations=(_FRAGMENT_ID,),
                    grade=GRADE_INTERPRETED,
                ),
            ),
        ),
        _fragments(),
        None,
    )

    assert GRADE_INTERPRETED in rendered.source_grades[_FRAGMENT_ID], (
        f"요약 문장의 등급이 안 실렸습니다: {rendered.source_grades}"
    )


# ══════════════════════════════════════════════════════════
# ⑤ 표·도식만 인용한 자료 (2026-08-25 적대 검수가 재현)
# ══════════════════════════════════════════════════════════


def _report_with_flow_only_source() -> ComposedReport:
    """7장 «흐름표»만 조각 2를 인용하는 보고서.

    문장은 전부 조각 1만 쓴다 — 그래서 조각 2에는 «문장 등급»이 하나도
    없고, 부록에는 「본문 사용 장: 7장」만 남는다. 이 조합이 결함을 만들던
    바로 그 모양이다.
    """
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    ComposedSentence(
                        text="회사는 2024년에 매출 100억원을 기록했다.",
                        citations=(_FRAGMENT_ID,),
                        grade=GRADE_CONFIRMED,
                    ),
                ),
                flow_rows=(
                    (
                        FlowRow(
                            cells=("원자재 매입", "시트 가공", "가구 제조사"),
                            citations=("2",),
                        ),
                    )
                    if section_id == OPERATIONS_FLOW_SECTION_ID
                    else ()
                ),
            )
            for section_id in SECTION_IDS
        ),
        summary=(
            ComposedSentence(
                text="매출이 늘었다.", citations=(_FRAGMENT_ID,), grade=GRADE_CONFIRMED
            ),
        ),
    )


def _rendered_with_flow_only_source():
    fragments = _fragments()
    fragments[2] = {
        "종류": "사업내용",
        "원문": "회사는 원자재를 매입해 시트를 가공한 뒤 가구 제조사에 공급한다.",
    }
    return render_report(
        "가나다전자", _report_with_flow_only_source(), fragments, None
    )


def _source_number_2(rendered):
    사료 = [source for source in rendered.citations if source.number == 2]
    assert 사료, f"부록에 조각 2가 없습니다: {[s.number for s in rendered.citations]}"
    return 사료[0]


def test_흐름표만_인용한_자료는_본문_사실_없음이_아니다() -> None:
    """★ 재현된 결함 — 「본문 사실 없음」과 「본문 사용 장: 7장」이 한 줄 안에서
    모순됐다. 읽는 사람이 바로 알아채는 거짓말이라 반드시 갈라야 한다.
    """
    rendered = _rendered_with_flow_only_source()
    source = _source_number_2(rendered)

    # 전제 확인 — 이 자료는 «표»가 썼고(사용 장 있음) «문장»은 안 썼다(등급 없음).
    assert source.used_in, "전제가 깨졌습니다 — 흐름표가 사용 장을 안 남겼습니다"
    assert not rendered.source_grades.get("2"), (
        f"전제가 깨졌습니다 — 표 인용에 문장 등급이 생겼습니다: "
        f"{rendered.source_grades}"
    )

    assert (
        source_verification_label(rendered, source.source_id)
        == SOURCE_VERIFICATION_TABLE_ONLY_LABEL
    )


def test_표가_쓴_자료와_아무도_안_쓴_자료는_다르게_적는다() -> None:
    """「표·도식 근거」가 «모든» 자료에 번지지 않는지 함께 지킨다.

    사용 장이 비어 있으면(요약 전용·미사용) 예전 그대로 「본문 사실 없음」이다.
    """
    rendered = _rendered_with_flow_only_source()

    문장이_쓴_자료 = [s for s in rendered.citations if s.number == 1][0]
    assert source_verification_label(rendered, 문장이_쓴_자료.source_id) == (
        "사실 검증 완료"
    )
    assert (
        source_verification_label(rendered, "존재하지 않는-source-id")
        == "본문 사실 없음"
    )
