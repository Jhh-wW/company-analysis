import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import revenue_table_switch
from src.features.composer.constants import (
    PORTFOLIO_TABLE_GUIDE_V2,
    PROMPT_FRAGMENT_LOCATION_LABEL,
    SECTION_IDS,
)
from src.features.composer.logic import build_section_prompt, compose_sections
from src.features.composer.port import filing_meta_from_raw
from src.features.composer.portfolio_names import (
    MIN_REPRESENTATIVE_NAMES_FOR_CARD,
    UNUSED_REPRESENTATIVE_NAMES_STEP,
    representative_names,
)
from src.shared.name_fragments import constants as shared_names
from src.shared.name_fragments.constants import (
    NAME_KIND_LABELS,
    REPRESENTATIVE_NAME_LABELS,
    parse_name_location,
)
from src.features.pipeline import real
from src.features.product_names.constants import (
    SUBJECT_BRAND,
    SUBJECT_IP,
    SUBJECT_PRODUCT,
    SUBJECT_SEGMENT,
)
from src.features.product_names.fragments import name_candidate_fragments
from src.features.product_names.models import NameCandidate
from src.features.product_names.logic import (
    collect_name_candidates,
    collect_name_candidates_from_tables,
)
from src.features.product_names.tables import read_filing_tables
from src.shared.report_evidence.constants import SOURCE_KIND_DART_BUSINESS_REPORT
from src.shared.report_evidence.legacy_fragment_kinds import LEGACY_FRAGMENT_KINDS
from src.shared.report_generation.models import exact_text_sha256


FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "product_names"
    / "tests"
    / "fixtures"
)
CORP_ID = "00126380"
RCEPT_NO = "20260315000123"
GENERATION_SHA256 = "b" * 64


def _typed_anchor() -> dict[str, object]:
    source_url = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + RCEPT_NO
    )
    return {
        "종류": SOURCE_KIND_DART_BUSINESS_REPORT,
        "원문": "공시에서 이미 검증된 원문 조각이다.",
        "출처": source_url,
        "문서ID": f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{RCEPT_NO}",
        "문서명": "사업보고서 (2025.12)",
        "문서일": "2026-03-15",
        "원문위치": "사업의 내용",
        "company_id": CORP_ID,
        "_evidence_section_ids": ("identity",),
        "_evidence_slot_ids": ("identity:legal_entity",),
        "_evidence_origin_fragment_ids": ("dart:anchor",),
        "_evidence_document_identity": f"document:dart.fss.or.kr:{RCEPT_NO}",
        "_evidence_document_content_sha256": "a" * 64,
        "_evidence_identity_binding": "fixture_identity_binding",
        "_evidence_publisher": "금융감독원 전자공시시스템",
        "_evidence_collected_on": "2026-03-15",
        "_evidence_domain_attestation_source_id": "",
        "_evidence_domain_attestation_evidence": "",
        "_evidence_reporting_period": "",
        "_evidence_attachment_url": "",
        "_evidence_ir_metadata_verification": "",
        "_evidence_domain_redirect_verification": "",
        "_evidence_domain_redirect_from_host": "",
        "_evidence_domain_redirect_to_host": "",
    }


def _legacy_fragments() -> dict[int, dict[str, object]]:
    return {
        number: {
            "종류": kind,
            "원문": f"테스트 회사의 {kind} 공식 근거다.",
        }
        for number, kind in enumerate(sorted(LEGACY_FRAGMENT_KINDS), start=1)
    }


def _filing() -> dict[str, str]:
    return {
        "rcept_no": RCEPT_NO,
        "report_nm": "사업보고서 (2025.12)",
        "rcept_dt": "20260315",
    }


def _kakao_text() -> str:
    return (FIXTURES / "kakao_product_services.txt").read_text(encoding="utf-8")


def _hybe_artist_path() -> Path:
    return FIXTURES / "hybe_artist_contracts.xml"


def test_이름조각은_packet과_가짜작가의_3장카드를_통과한다() -> None:
    before = _legacy_fragments()
    steps: list[dict[str, object]] = []
    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        before,
        filing_text=_kakao_text(),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
    )

    assert added > 0
    assert set(before) < set(frags)
    assert min(set(frags) - set(before)) > max(before)
    name_number = next(
        number
        for number, raw in frags.items()
        if raw.get("_evidence_slot_ids") == ("portfolio:product_role",)
        and "카카오톡" in str(raw.get("원문") or "")
    )
    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=CORP_ID,
        source_identity_digest=GENERATION_SHA256,
        frags=frags,
        filing_meta=filing_meta_from_raw(_filing()),
    )
    portfolio_packet = next(
        packet for packet in packets.packets if packet.section_id == "portfolio"
    )
    name_fragment = next(
        fragment
        for fragment in portfolio_packet.fragments
        if fragment.fragment_id == str(name_number)
    )
    assert name_fragment.supported_claim_slots == ("portfolio:product_role",)
    candidate = next(
        item
        for item in collect_name_candidates(
            _kakao_text(), source_kind=SOURCE_KIND_DART_BUSINESS_REPORT
        )
        if item.name == "카카오톡"
    )
    assert name_fragment.text == candidate.excerpt
    assert exact_text_sha256(name_fragment.text) == candidate.excerpt_sha256

    calls = 0

    def fake_writer(_prompt: str) -> str:
        nonlocal calls
        section_id = SECTION_IDS[calls]
        calls += 1
        flow_rows = []
        if section_id == "portfolio":
            flow_rows = [
                {
                    "칸": ["카카오톡", "", "", ""],
                    "인용": [str(name_number)],
                }
            ]
        return json.dumps(
            {"문장들": [], "경로표": flow_rows}, ensure_ascii=False
        )

    composed = compose_sections(
        "테스트 회사",
        {},
        None,
        fake_writer,
        section_evidence_packets=packets,
    )
    portfolio = next(
        section for section in composed.sections if section.section_id == "portfolio"
    )

    assert calls == len(SECTION_IDS)
    assert len(portfolio.flow_rows) == 1
    assert portfolio.flow_rows[0].cells[0] == "카카오톡"
    assert portfolio.flow_rows[0].citations == (str(name_number),)


def test_typed신원이_없어도_후보수와_단계는_남고_조각은_없다() -> None:
    before = _legacy_fragments()
    steps: list[dict[str, object]] = []

    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        before,
        filing_text=_kakao_text(),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(),
        steps=steps,
    )

    assert added == 0
    assert frags == before
    assert steps == [
        {
            "step": "7_이름후보",
            "후보": 8,
            "조각": 0,
            "종류별": {"segment": 2, "product": 6},
            "상한적용": False,
            "입력": real.NAME_INPUT_TEXT,
            "표수": 0,
        }
    ]


# ─────────────────────────────────────────────────────────────────────
# 표 구조 입력 — filing_text는 공백이 접힌 평문이라 표를 읽을 수 없다.
# 원문 파일 경로를 넘겨야 이름이 나온다.
# ─────────────────────────────────────────────────────────────────────

# 로컬에만 두는 실측 원문(공개 DART 사업보고서 사본). 없으면 그 시험만 건너뛴다.
LOCAL_HYBE_FILINGS = sorted(
    Path(
        "C:/Users/jh-wo/.claude/workspace/기업분석2/app/.local_evaluation_runs"
    ).glob("*/analysis_engine/pilot/raw_filings/20260320000802.xml")
)


def test_공백접힌_평문으로는_아티스트_이름을_못_읽는다() -> None:
    """이 시험이 초록이면 표 파서가 필요 없다는 뜻이다 — 실제로는 0건이다."""

    collapsed = " ".join(_hybe_artist_path().read_text(encoding="utf-8").split())

    assert collect_name_candidates(collapsed, source_kind="사업보고서") == ()


def test_원문경로를_넘기면_표에서_대표IP_조각이_생긴다() -> None:
    before = _legacy_fragments()
    steps: list[dict[str, object]] = []

    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        before,
        filing_text="",
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
        raw_path=str(_hybe_artist_path()),
    )

    assert added > 0
    assert set(before) < set(frags)
    step = steps[-1]
    assert step["입력"] == real.NAME_INPUT_TABLE
    assert step["표수"] == 1
    assert step["종류별"] == {SUBJECT_IP: 18}
    ip_names = [
        name
        for raw in frags.values()
        if (pair := parse_name_location(str(raw.get("원문위치") or ""))) is not None
        and pair[0] == NAME_KIND_LABELS["ip"]
        for name in (pair[1],)
    ]
    # 이 픽스처에는 대표 IP 한 종류뿐이라 남는 자리까지 IP가 채운다.
    assert len(ip_names) == 16
    # ★ 이름은 원문위치에서 «되읽어» 확인한다 — 원문(표 한 행)에서 이름을
    #   되찾으려 하면 어느 칸이 이름인지 추측하게 된다.
    assert len(set(ip_names)) == len(ip_names)
    assert all(name for name in ip_names)


def test_표에서_만든_이름조각도_transport_사전검증을_통과한다() -> None:
    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        _legacy_fragments(),
        filing_text="",
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=[],
        raw_path=str(_hybe_artist_path()),
    )
    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=CORP_ID,
        source_identity_digest=GENERATION_SHA256,
        frags=frags,
        filing_meta=filing_meta_from_raw(_filing()),
    )
    portfolio_packet = next(
        packet for packet in packets.packets if packet.section_id == "portfolio"
    )
    name_fragments = [
        fragment
        for fragment in portfolio_packet.fragments
        if fragment.supported_claim_slots == ("portfolio:product_role",)
    ]

    assert len(name_fragments) == added
    assert any("뉴진스" in fragment.text for fragment in name_fragments)


def test_원문파일이_없으면_평문_파서로_되돌아간다(tmp_path: Path) -> None:
    steps: list[dict[str, object]] = []

    _, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        _legacy_fragments(),
        filing_text=_kakao_text(),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
        raw_path=str(tmp_path / "없는파일.xml"),
    )

    assert added > 0
    assert steps[-1]["입력"] == real.NAME_INPUT_TEXT
    assert steps[-1]["표수"] == 0


@pytest.mark.skipif(
    not LOCAL_HYBE_FILINGS, reason="로컬 공시 원문 사본이 없는 환경입니다"
)
def test_실측_하이브_사업보고서_전체에서_대표IP를_읽는다() -> None:
    tables = read_filing_tables(LOCAL_HYBE_FILINGS[0])
    candidates = collect_name_candidates_from_tables(
        tables, source_kind="사업보고서"
    )
    names = {candidate.name for candidate in candidates}
    ip_names = {
        candidate.name
        for candidate in candidates
        if candidate.subject_kind == SUBJECT_IP
    }

    assert len(tables) > 100
    assert len(candidates) >= 10
    assert len(ip_names) >= 8
    assert {"방탄소년단", "뉴진스", "세븐틴", "르세라핌"} <= ip_names
    # 멤버(사람) 이름이 후보로 새면 안 된다.
    assert not ({"김남준", "김석진", "황민현"} & names)


# ─────────────────────────────────────────────────────────────────────
# 실측 원문 → packet → 3장 작가 프롬프트까지의 무과금 재현
#
# ★ 왜 필요했나 (2026-09-06) — 운영 보고서의 3장 카드에 그룹 이름이 한 번도
#   안 올랐는데, 그 실행의 진단 기록이 우리 손에 없었다. 「조각이 안 갔다」와
#   「갔는데 작가가 안 썼다」를 가르려면 같은 원문으로 프롬프트까지 다시
#   만들어 보는 수밖에 없다. 이 시험이 그 재현이다 (AI·네트워크 0회).
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def 스위치_켬():
    """운영과 같은 새 안내문 경로로 고정한다(render.yaml: REVENUE_TABLE_V2=1)."""

    revenue_table_switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    revenue_table_switch.freeze_process_revenue_table_switch(
        revenue_table_switch.RevenueTableSwitch.ON
    )
    try:
        yield
    finally:
        revenue_table_switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


@pytest.mark.skipif(
    not LOCAL_HYBE_FILINGS, reason="로컬 공시 원문 사본이 없는 환경입니다"
)
def test_실측_원문의_대표IP가_3장_packet과_작가_프롬프트까지_간다(
    스위치_켬,
) -> None:
    """대표 IP 조각이 3장 프롬프트에 «이름과 원문위치 표기까지» 실린다."""

    steps: list[dict[str, object]] = []
    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        _legacy_fragments(),
        filing_text="",
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
        raw_path=str(LOCAL_HYBE_FILINGS[0]),
    )

    # (a) 수집 단계 기록 — 어떤 입력으로 몇 개를 읽었는가.
    step = steps[-1]
    assert step["step"] == "7_이름후보"
    assert step["입력"] == real.NAME_INPUT_TABLE
    assert int(step["표수"]) > 100
    assert step["종류별"][SUBJECT_IP] >= 8
    assert "탈락" not in step

    # (b) 3장 packet 안에 대표 IP 조각이 몇 개 들어갔는가.
    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=CORP_ID,
        source_identity_digest=GENERATION_SHA256,
        frags=frags,
        filing_meta=filing_meta_from_raw(_filing()),
    )
    portfolio_packet = next(
        packet for packet in packets.packets if packet.section_id == "portfolio"
    )
    name_fragments = [
        fragment
        for fragment in portfolio_packet.fragments
        if (parsed := parse_name_location(fragment.location)) is not None
        and parsed[0] in REPRESENTATIVE_NAME_LABELS
    ]
    assert len(name_fragments) >= MIN_REPRESENTATIVE_NAMES_FOR_CARD
    assert len(name_fragments) <= added
    labelled = representative_names(portfolio_packet.fragments)
    assert len(labelled) >= MIN_REPRESENTATIVE_NAMES_FOR_CARD
    assert {label for label, _name in labelled} <= set(REPRESENTATIVE_NAME_LABELS)

    # (c) 그 조각과 안내문 문장이 실제 작가 프롬프트에 실리는가.
    prompt = build_section_prompt(
        "가나다뮤직",
        "portfolio",
        portfolio_packet.fragments,
        None,
        show_supported_claim_slots=True,
    )
    assert PORTFOLIO_TABLE_GUIDE_V2 in prompt
    assert "카드 하나는 «반드시» 그 이름들을 담는다" in prompt
    for fragment in name_fragments[:MIN_REPRESENTATIVE_NAMES_FOR_CARD]:
        # ★ 이름만이 아니라 «원문위치 표기»까지 실려야 안내문을 지킬 수 있다.
        #   이 줄이 이번 수정 이전의 실제 결함이었다(표기가 한 번도 안 실렸다).
        assert fragment.text in prompt
        assert f"{PROMPT_FRAGMENT_LOCATION_LABEL}: {fragment.location}" in prompt
        assert (parsed := parse_name_location(fragment.location)) is not None
        assert parsed[1] in prompt


# ─────────────────────────────────────────────────────────────────────
# 실행 기록 — 카드가 이름을 안 썼다는 사실이 steps 에 남는가
# ─────────────────────────────────────────────────────────────────────


def test_이름을_안_쓴_실행은_단계로_남는다() -> None:
    output = SimpleNamespace(
        unused_portfolio_name_count=10,
        unused_portfolio_name_counts_by_label=(("대표 IP", 7), ("제품", 3)),
    )

    assert real._unused_name_steps(output) == [  # noqa: SLF001
        {
            "step": UNUSED_REPRESENTATIVE_NAMES_STEP,
            "이름수": 10,
            "종류별": {"대표 IP": 7, "제품": 3},
        }
    ]


def test_이름을_쓴_실행은_단계를_남기지_않는다() -> None:
    output = SimpleNamespace(
        unused_portfolio_name_count=0,
        unused_portfolio_name_counts_by_label=(),
    )

    assert real._unused_name_steps(output) == []  # noqa: SLF001


def test_이_필드를_모르는_옛_결과도_그대로_지나간다() -> None:
    """저장된 옛 실행 결과를 다시 읽어도 터지지 않아야 한다."""

    assert real._unused_name_steps(SimpleNamespace()) == []  # noqa: SLF001
    assert real._unused_name_steps(SimpleNamespace(  # noqa: SLF001
        unused_portfolio_name_count=None
    )) == []
    # 개수는 있는데 종류별이 깨져 있어도 단계는 남는다(진단이 사라지면 안 된다).
    assert real._unused_name_steps(SimpleNamespace(  # noqa: SLF001
        unused_portfolio_name_count=4,
        unused_portfolio_name_counts_by_label="깨진 값",
    )) == [
        {"step": UNUSED_REPRESENTATIVE_NAMES_STEP, "이름수": 4, "종류별": {}}
    ]


def test_v2_실행부가_그_단계를_실제로_부른다() -> None:
    """헬퍼만 있고 아무도 안 부르면 실행 기록에 영영 안 남는다.

    ★ 소스를 «다시 읽어» 확인한다 — import한 모듈 객체는 옛 바이트코드를
      쓸 수 있어 같은 길이의 수정을 못 본다.
    """

    tree = ast.parse(
        Path(real.__file__).read_text(encoding="utf-8"), filename=real.__file__
    )
    runner = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_v2_composer"
    )
    called = {
        node.func.id
        for node in ast.walk(runner)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "_unused_name_steps" in called


# ─────────────────────────────────────────────────────────────────────
# 종류 라벨은 «생산자»와 «소비자»가 같아야 한다
# ─────────────────────────────────────────────────────────────────────


def test_두_기능이_같은_라벨_객체를_읽는다() -> None:
    """값 비교가 아니라 «같은 객체인가»를 본다.

    글자만 맞춰 두면 한쪽이 바뀔 때 다른 쪽이 조용히 어긋나고, 3장 판정이
    아무것도 못 찾고도 초록불이 된다. 그래서 정본을 `shared`에 두고 두
    기능이 그 객체를 그대로 읽는지 잠근다.
    """

    from src.features.composer import portfolio_names as consumer
    from src.features.product_names import constants as producer_constants
    from src.features.product_names import fragments as producer

    # 생산자: 라벨표와 원문위치 조립기를 정본에서 가져다 쓴다.
    assert producer.NAME_KIND_LABELS is shared_names.NAME_KIND_LABELS
    assert producer.compose_name_location is shared_names.compose_name_location
    # 생산자: 종류 id도 정본의 같은 객체다.
    assert producer_constants.SUBJECT_KINDS is shared_names.NAME_KINDS
    for name, kind in (
        ("SUBJECT_IP", shared_names.NAME_KIND_IP),
        ("SUBJECT_PRODUCT", shared_names.NAME_KIND_PRODUCT),
        ("SUBJECT_BRAND", shared_names.NAME_KIND_BRAND),
        ("SUBJECT_SEGMENT", shared_names.NAME_KIND_SEGMENT),
        ("SUBJECT_SUBSIDIARY", shared_names.NAME_KIND_SUBSIDIARY),
        ("SUBJECT_CONTRACT", shared_names.NAME_KIND_CONTRACT),
    ):
        assert getattr(producer_constants, name) is kind, name
    # 소비자: 읽는 함수도 정본의 같은 객체다.
    assert consumer.parse_name_location is shared_names.parse_name_location
    assert (
        consumer.REPRESENTATIVE_NAME_LABELS
        is shared_names.REPRESENTATIVE_NAME_LABELS
    )
    # 어느 쪽도 라벨 글자를 자기 파일에 다시 적어 두지 않았다.
    for module in (producer, consumer):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for label in shared_names.NAME_KIND_LABELS.values():
            assert f'"{label}"' not in source, (module.__name__, label)


def test_대표이름_라벨이_실제_생산자와_같다() -> None:
    """정본에 적힌 라벨이 «진짜 생산 결과»에도 그대로 나오는지 본다.

    상수끼리 맞춰 보는 것으로는 부족하다 — 조립 과정에서 라벨이 다른 글자로
    바뀌어도 상수 대조는 초록이기 때문이다.
    """

    produced: set[str] = set()
    for kind in (SUBJECT_IP, SUBJECT_PRODUCT, SUBJECT_BRAND):
        made = name_candidate_fragments(
            (
                NameCandidate(
                    name="가나다이름",
                    subject_kind=kind,
                    description="",
                    source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                    location="가. 표 · 2행",
                    excerpt="가나다이름 | 설명",
                    excerpt_sha256=exact_text_sha256("가나다이름 | 설명"),
                ),
            ),
            filing_meta=_filing(),
            corp_id=CORP_ID,
            typed_fragments=(_typed_anchor(),),
        )
        assert len(made) == 1, kind
        location = str(made[0]["원문위치"])
        # 생산자와 소비자가 같은 값을 읽어야 한다.
        parsed = parse_name_location(location)
        assert parsed is not None
        label, name = parsed
        assert name == "가나다이름"
        produced.add(label)

    assert produced == set(REPRESENTATIVE_NAME_LABELS)


def test_사업부문_라벨은_대표이름으로_세지_않는다() -> None:
    """부문명만 있는 카드는 이 규칙을 충족하지 못한다 — 그 경계를 못 박는다."""

    made = name_candidate_fragments(
        (
            NameCandidate(
                name="가나다부문",
                subject_kind=SUBJECT_SEGMENT,
                description="",
                source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                location="가. 표 · 2행",
                excerpt="가나다부문 | 설명",
                excerpt_sha256=exact_text_sha256("가나다부문 | 설명"),
            ),
        ),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
    )
    location = str(made[0]["원문위치"])

    # 생산자는 라벨을 읽지만 소비자(3장 판정)는 대표 이름으로 세지 않는다.
    assert parse_name_location(location) == (NAME_KIND_LABELS["segment"], "가나다부문")
    assert NAME_KIND_LABELS["segment"] not in REPRESENTATIVE_NAME_LABELS


# ─────────────────────────────────────────────────────────────────────
# 두 번째 회사 — 업종이 달라도 같은 사슬을 지나는가
# ─────────────────────────────────────────────────────────────────────

LOCAL_MANUFACTURER_FILINGS = sorted(
    Path(
        "C:/Users/jh-wo/.claude/workspace/기업분석2/app/.local_evaluation_runs"
    ).glob("*/analysis_engine/pilot/raw_filings/20260310002820.xml")
)


@pytest.mark.skipif(
    not LOCAL_MANUFACTURER_FILINGS, reason="로컬 공시 원문 사본이 없는 환경입니다"
)
def test_다른_업종_원문도_3장_packet에_대표이름을_싣는다(스위치_켬) -> None:
    """앞 시험과 «같은 코드»가 다른 업종 원문에서도 이름을 싣는지 본다.

    앞 시험의 원문은 이름 표가 아티스트 계약 표였고, 이 원문은 제품 목록이다.
    업종으로 갈리는 분기가 없다면 두 원문 모두 대표 이름이 실려야 한다.
    """

    steps: list[dict[str, object]] = []
    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        _legacy_fragments(),
        filing_text="",
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
        raw_path=str(LOCAL_MANUFACTURER_FILINGS[0]),
    )

    step = steps[-1]
    assert step["입력"] == real.NAME_INPUT_TABLE
    assert added > 0
    assert "탈락" not in step

    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=CORP_ID,
        source_identity_digest=GENERATION_SHA256,
        frags=frags,
        filing_meta=filing_meta_from_raw(_filing()),
    )
    portfolio_packet = next(
        packet for packet in packets.packets if packet.section_id == "portfolio"
    )
    labelled = representative_names(portfolio_packet.fragments)

    assert len(labelled) >= MIN_REPRESENTATIVE_NAMES_FOR_CARD, step
    assert {label for label, _name in labelled} <= set(REPRESENTATIVE_NAME_LABELS)

    prompt = build_section_prompt(
        "가나다회사",
        "portfolio",
        portfolio_packet.fragments,
        None,
        show_supported_claim_slots=True,
    )
    for _label, name in labelled[:MIN_REPRESENTATIVE_NAMES_FOR_CARD]:
        assert name in prompt
