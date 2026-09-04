"""typed DART 수집물 → v1 수집 조각 변환의 P1-A 계약.

★ 무엇을 막는가 (「하면 안 되는 설계 3」 실측)
  브랜치 판 병합은 중복키가 ``(원문, 문서ID)``였는데 legacy 공시 조각에는
  ``문서ID`` 키 자체가 없다(``make_fragments``가 ``{"종류","원문"}``만 만든다).
  그래서 중복키가 ``(원문, "")``으로 잡혀 **어떤 typed 문서ID와도 일치할 수
  없고, 같은 공시 문단이 두 번 실린다.** 이 파일은 그 자리를 「원문 정규화
  해시」로 바꿔 실제로 한 번만 남는지 확인한다.

★ 두 번째 계약 — typed 조각이 실은 문서 신원이 작가 프롬프트(장별 packet)와
  출처 신원까지 그대로 이어져야 한다. 지금 ``_full_section_evidence_packets``는
  ``출처``가 없는 조각에 **최신 공시 1건의 문서ID를 빌려 준다.** typed 조각은
  다른 공시에서 왔을 수 있으므로 그 조각 자신의 ``문서ID``를 써야 한다.

★ 실제 네트워크·DART·AI 호출 0건. typed 수집기는 «진짜 구현»을 돌리되 조회
  경계만 엔진이 이미 갖고 있는 가짜 fetcher로 바꾼다 — 산출 Mapping을 손으로
  지어내면 상위 자료형 계약이 실제와 어긋나도 시험이 초록불이 된다.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.logic import build_section_prompt
from src.features.composer.port import filing_meta_from_raw
from src.features.pipeline import real

#: 엔진 typed 수집기는 app 패키지가 아니라 ``analysis_engine/src`` 아래에 산다.
_ENGINE_SRC = pathlib.Path(__file__).resolve().parents[4].parent / "analysis_engine" / "src"
_ENGINE_FEATURE = _ENGINE_SRC / "features" / "evidence_collection"

pytestmark = pytest.mark.skipif(
    not _ENGINE_FEATURE.is_dir(),
    reason=f"엔진 typed 수집기가 이 트리에 없습니다: {_ENGINE_FEATURE}",
)

if _ENGINE_FEATURE.is_dir() and str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))

TARGET_COMPANY_ID = "00126380"
TYPED_RCEPT_NO = "20250315000001"
#: 최신 공시(=legacy ``filing``)는 typed 수집기가 고른 공시와 **다른** 접수번호다.
#: 두 값이 같으면 「문서ID를 빌려 쓰는」 결함이 시험에서 안 보인다.
LATEST_RCEPT_NO = "20260315000123"
GENERATION_SHA256 = "a" * 64


def _typed_dart_mapping() -> dict[str, object]:
    """엔진 typed 수집기를 가짜 조회기로 «실제로» 돌려 계약 Mapping을 만든다."""

    from features.evidence_collection import collect, serialize  # noqa: PLC0415
    from features.evidence_collection.filing_select import RawFilingRow  # noqa: PLC0415
    from features.evidence_collection.tests.fixtures import (  # noqa: PLC0415
        fake_fetcher,
        synthetic_documents,
    )

    fetcher = fake_fetcher.FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": fake_fetcher.FilingListResult(
                state="OK",
                rows=(
                    RawFilingRow(
                        rcept_no=TYPED_RCEPT_NO,
                        report_nm="사업보고서 (2024.12)",
                        rcept_dt="20250315",
                    ),
                ),
            ),
            "F": fake_fetcher.FilingListResult(state="OK", rows=()),
        },
        document_responses_by_rcept_no={
            TYPED_RCEPT_NO: fake_fetcher.DocumentFetchResult(
                state="OK", text=synthetic_documents.LISTED_BUSINESS_REPORT_TEXT
            )
        },
    )
    harvest = collect.collect_dart_evidence(
        fetcher,
        TARGET_COMPANY_ID,
        now="2026-09-02T00:00:00Z",
        deadline_seconds=30,
    )
    return serialize.harvest_to_mapping(harvest)


def _legacy_frags() -> dict[int, dict[str, str]]:
    """v1 수집이 실제로 만드는 모양의 조각 — 아홉 장 packet이 다 차는 최소 묶음."""

    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비를 만드는 법인이다."},
        2: {"종류": "MD&A", "원문": "가나다전자는 2025년 생산 설비를 도입했다."},
        3: {"종류": "신규사업전망", "원문": "가나다전자는 2027년 수출망을 넓힐 계획이다."},
        4: {
            "종류": "재무",
            "원문": "주요계정(DART API): 매출액·영업이익의 2025년, 2024년 연결 원값",
        },
        5: {
            "종류": "홈페이지",
            "원문": "가나다전자는 존중과 책임을 핵심 가치로 제시한다.",
            "출처": "https://www.ganada.example/culture",
            "문서명": "가나다전자 인재상",
            "원문위치": "/culture",
        },
    }


def _packets(frags: dict[int, dict[str, str]]):
    return real._full_section_evidence_packets(  # noqa: SLF001 - 생산 함수 계약
        corp_id=TARGET_COMPANY_ID,
        source_identity_digest=GENERATION_SHA256,
        frags=frags,
        filing_meta=filing_meta_from_raw(
            {
                "report_nm": "사업보고서 (2025.12)",
                "rcept_no": LATEST_RCEPT_NO,
                "rcept_dt": "20260315",
            }
        ),
    )


def _typed_texts(mapping: dict[str, object]) -> list[str]:
    return [str(item["text"]) for item in mapping["fragments"]]  # type: ignore[index]


def test_typed_DART_조각이_prompt와_citation까지_같은_문서ID로_이어진다() -> None:
    """typed 조각은 «자기 공시»의 접수번호로 문서 신원이 만들어져야 한다.

    수정 전에는 ``출처``가 없는 조각이 전부 최신 공시 1건(``filing_meta``)의
    문서ID를 빌려 써서, 다른 공시에서 온 typed 조각이 **엉뚱한 문서를 근거로
    가리켰다.**
    """

    mapping = _typed_dart_mapping()
    frags, added = real._merge_typed_dart_fragments(  # noqa: SLF001
        _legacy_frags(), mapping
    )

    assert added > 0, "typed 수집기가 조각을 하나도 내지 않았다(시험 전제 붕괴)"
    typed_numbers = [
        number
        for number, fragment in frags.items()
        if fragment.get("문서ID") == TYPED_RCEPT_NO
    ]
    assert len(typed_numbers) == added

    packet_set = _packets(frags)
    by_section = {packet.section_id: packet for packet in packet_set.packets}
    seen_typed = 0
    for packet in packet_set.packets:
        for fragment in packet.fragments:
            if fragment.fragment_id not in {str(n) for n in typed_numbers}:
                continue
            seen_typed += 1
            assert fragment.document_identity == (
                f"document:dart.fss.or.kr:{TYPED_RCEPT_NO}"
            )
            assert LATEST_RCEPT_NO not in fragment.document_identity
    assert seen_typed > 0, "typed 조각이 어느 장 packet에도 들어가지 못했다"

    # 작가 프롬프트에도 같은 조각이 문서명과 함께 실제로 보인다.
    sample_number = str(typed_numbers[0])
    sample_section = next(
        section_id
        for section_id in SECTION_IDS
        if any(
            fragment.fragment_id == sample_number
            for fragment in by_section[section_id].fragments
        )
    )
    prompt = build_section_prompt(
        "가나다전자",
        sample_section,
        by_section[sample_section].fragments,
        None,
    )
    assert f"[조각 {sample_number}]" in prompt
    assert frags[int(sample_number)]["원문"][:20] in prompt
    assert "사업보고서 (2024.12)" in prompt


def test_같은_원문의_typed와_legacy_조각은_한_번만_남는다() -> None:
    """legacy가 이미 실은 문단을 typed가 다시 실으면 작가가 같은 사실을 두 번 본다.

    legacy 조각에는 ``문서ID``가 없으므로 ``(원문, 문서ID)`` 중복키로는 절대
    안 잡힌다 — 원문 정규화 해시로 판정해야 한다. 공백·개행만 다른 판본도
    같은 원문으로 본다.
    """

    mapping = _typed_dart_mapping()
    typed_texts = _typed_texts(mapping)
    assert typed_texts, "typed 수집기가 조각을 하나도 내지 않았다(시험 전제 붕괴)"

    borrowed = typed_texts[0]
    # 공백·개행만 다른 판본 — 사람 눈에는 같은 문단이다.
    legacy = _legacy_frags()
    legacy[90] = {"종류": "사업내용", "원문": "  " + "\n ".join(borrowed.split()) + "  "}

    frags, added = real._merge_typed_dart_fragments(legacy, mapping)  # noqa: SLF001

    assert added == len(typed_texts) - 1
    same_text = [
        number
        for number, fragment in frags.items()
        if " ".join(str(fragment.get("원문") or "").split())
        == " ".join(borrowed.split())
    ]
    assert len(same_text) == 1, f"같은 원문이 {len(same_text)}번 실렸다"


def test_typed_조각끼리_같은_원문이_와도_한_번만_남는다() -> None:
    """같은 원문이 두 공시에 실려 오면(정정공시·반기 중복) 한 번만 남긴다."""

    mapping = _typed_dart_mapping()
    fragments = list(mapping["fragments"])  # type: ignore[arg-type]
    assert fragments
    twin = dict(fragments[0])
    twin["fragment_id"] = str(twin["fragment_id"]) + ":twin"
    mapping = dict(mapping)
    mapping["fragments"] = [*fragments, twin]

    frags, added = real._merge_typed_dart_fragments(  # noqa: SLF001
        _legacy_frags(), mapping
    )

    assert added == len(fragments)
    texts = [" ".join(str(f.get("원문") or "").split()) for f in frags.values()]
    assert len(texts) == len(set(texts))


def test_typed_병합은_기존_조각_번호를_덮어쓰지_않는다() -> None:
    """조각 번호는 작가가 인용하는 주소다 — 덮어쓰면 인용이 다른 사실을 가리킨다."""

    legacy = _legacy_frags()
    before = {number: dict(fragment) for number, fragment in legacy.items()}

    frags, _added = real._merge_typed_dart_fragments(  # noqa: SLF001
        legacy, _typed_dart_mapping()
    )

    for number, fragment in before.items():
        assert frags[number] == fragment


def test_typed_조각이_없으면_frags가_바이트_그대로다() -> None:
    """수집 결과가 비면 조각 묶음을 건드리지 않는다(빈 조각·빈 종류 금지)."""

    legacy = _legacy_frags()
    frags, added = real._merge_typed_dart_fragments(  # noqa: SLF001
        legacy, {"company_id": TARGET_COMPANY_ID, "documents": [], "fragments": []}
    )

    assert added == 0
    assert frags == _legacy_frags()


@pytest.mark.parametrize("published_on", ("2024-02-29", "20240229"))
def test_typed_DART_bridge는_신규_ISO와_옛_compact를_같은_날짜로_보존한다(
    published_on: str,
) -> None:
    mapping = _typed_dart_mapping()
    for document in mapping["documents"]:  # type: ignore[index]
        document["published_on"] = published_on

    made = real._typed_dart_legacy_fragments(mapping)  # noqa: SLF001

    assert made
    assert {item["문서일"] for item in made} == {"2024-02-29"}


@pytest.mark.parametrize(
    "published_on",
    ("2025-02-30", "20250230", "임의 문자열"),
)
def test_typed_DART_bridge는_없는날짜와_임의문자열을_사용하지_않는다(
    published_on: str,
) -> None:
    mapping = _typed_dart_mapping()
    for document in mapping["documents"]:  # type: ignore[index]
        document["published_on"] = published_on

    with pytest.raises(ValueError, match="공식 자료 날짜"):
        real._typed_dart_legacy_fragments(mapping)  # noqa: SLF001
