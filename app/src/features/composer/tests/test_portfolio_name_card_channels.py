"""결정적 이름 카드가 «세 채널 전부»에 실제로 그려지는지 본다.

★ 왜 필요한가 — 데이터 층에 카드 한 줄을 더해 놓고도 화면·PDF·노션 중 한
  곳이라도 그 줄을 안 그리면, 우리는 「붙였다」고 믿는데 독자는 못 본다.
  3장은 카드로 그려지는 장이라 렌더러가 «빈 칸을 빼는» 특별 경로를 타므로
  일반 표 시험이 이 경로를 대신해 주지 못한다.

★ AI·네트워크 0회. 가짜 작가가 이름을 하나도 안 쓴 부문 카드만 낸다 —
  2026-09-06 운영 실측에서 실제 작가가 낸 모양이다.
"""

from __future__ import annotations

import io
import json
import re
import uuid
from pathlib import Path

import pytest
from pypdf import PdfReader

from src.features.composer.constants import (
    PORTFOLIO_TABLE_CAPTION,
    PORTFOLIO_TABLE_HEADERS,
    PORTFOLIO_TABLE_SECTION_ID,
)
from src.features.composer.diagram_check import (
    FLOW_REVIEW_PROMPT_HEADER,
    FLOW_REVIEW_ROW_NUMBER_PATTERN,
)
from src.features.composer.pipeline import run_v2
from src.features.composer.port import CollectedFragment
from src.features.composer.portfolio_name_card import NAME_CARD_REASON
from src.shared.name_fragments.constants import (
    NAME_KIND_IP,
    compose_name_location,
)


#: 가공 이름 — 실존 회사·그룹·상품 이름을 시험에 박지 않는다.
_TITLE = "나. 주요 아티스트 전속계약"
_NAMES = ("하늘소년단", "바다소녀들", "별무리")
_ROW_TEXTS = (
    "㈜가나다뮤직 | 하늘소년단",
    "㈜가나다뮤직 | 바다소녀들",
    "㈜라마바뮤직 | 별무리",
)
_ARTIFACT_DIR = Path(__file__).resolve().parents[5] / ".local-artifacts" / "p38"


def _fragments() -> dict[int, dict[str, str]]:
    frags: dict[int, dict[str, str]] = {
        1: {"종류": "사업내용", "원문": "가나다회사는 사업부문 하나를 운영한다."},
        2: {
            "종류": "홈페이지",
            "원문": "고객 존중을 핵심 가치로 삼는다.",
            "출처": "https://www.ganada.example/about",
            "문서일": "2026-08-01",
        },
    }
    for index, name in enumerate(_NAMES):
        frags[index + 3] = {
            "종류": "사업내용",
            "원문": _ROW_TEXTS[index],
            "원문위치": compose_name_location(
                f"{_TITLE} · {index * 3 + 2}행", NAME_KIND_IP, name
            ),
        }
    return frags


def _writer(prompt: str) -> str:
    if "핵심 요약" in prompt:
        return json.dumps(
            {
                "문장들": [
                    {"글": f"요약 {mark} 문장이다.", "인용": ["1"], "등급": "확인"}
                    for mark in ("가", "나", "다")
                ]
            },
            ensure_ascii=False,
        )
    payload: dict[str, object] = {
        "문장들": [
            {
                "글": "가나다회사는 사업부문 하나를 운영한다.",
                "인용": ["1"],
                "등급": "확인",
            }
        ]
    }
    if PORTFOLIO_TABLE_HEADERS[0] in prompt:
        # ★ 이름을 한 글자도 안 쓴 «부문 카드»만 낸다.
        payload["경로표"] = [
            {"칸": ["사업부문 하나", "부문 설명", "부문을 운영한다", "주력"], "인용": ["1"]}
        ]
    return json.dumps(payload, ensure_ascii=False)


def _reviewer(prompt: str) -> str:
    if FLOW_REVIEW_PROMPT_HEADER in prompt:
        numbers = re.findall(
            FLOW_REVIEW_ROW_NUMBER_PATTERN, prompt, flags=re.MULTILINE
        )
    else:
        numbers = re.findall(r"\[(\d+)\] \(등급: [^,\n]+, 인용:", prompt)
    return json.dumps(
        {"판정": [{"번호": int(value), "결과": "참"} for value in numbers]},
        ensure_ascii=False,
    )


@pytest.fixture(scope="module")
def 보강된_보고서():
    output = run_v2(
        "가나다회사",
        _fragments(),
        None,
        writer_ask=_writer,
        reviewer_ask=_reviewer,
        corp_type="상장사",
        as_of_date="2026-09-06",
    )
    assert output.portfolio_name_card_count == len(_NAMES), (
        f"이름 카드가 안 붙었습니다 — 사유: "
        f"{output.portfolio_name_card_blocked_reason!r}"
    )
    return output.report


def _portfolio_table(report):
    section = next(
        section
        for section in report.sections
        if section.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    return next(
        table for table in section.tables if table.caption == PORTFOLIO_TABLE_CAPTION
    )


# ══════════════════════════════════════════════════════════
# ① 자료 층 — 표에 카드 두 줄이 있고 둘째 줄이 우리 것이다
# ══════════════════════════════════════════════════════════


def test_3장_표에_작가_카드와_이름_카드가_함께_있다(보강된_보고서) -> None:
    rows = _portfolio_table(보강된_보고서).rows

    assert len(rows) == 2, rows
    assert rows[0][0] == "사업부문 하나"
    assert rows[1][0] == _NAMES[0]
    assert rows[1][1] == "·".join(_NAMES)
    assert rows[1][2] == NAME_CARD_REASON
    assert rows[1][3] == ""


# ══════════════════════════════════════════════════════════
# ② 화면(result.html)
# ══════════════════════════════════════════════════════════


def test_화면이_이름_카드를_라벨값_카드로_그린다(보강된_보고서) -> None:
    from fastapi.testclient import TestClient

    from src.features.auth import constants as auth_constants
    from src.features.auth import logic as auth_logic
    from src.web import job_runtime
    from src.web.main import app
    from src.web.routers import reports as reports_router
    from src.web.tests.report_route_support import serve_legacy_report_snapshot

    job_id = f"p38-name-card-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)  # noqa: SLF001

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
        mp.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
        job_runtime._start_job_runtime()  # noqa: SLF001
        serve_legacy_report_snapshot(mp, 보강된_보고서, report_id=job_id)
        mp.setattr(job_runtime, "_link_expired", lambda _report: False)
        mp.setattr(
            reports_router, "_release_state", lambda **_kwargs: (object(), None)
        )
        mp.setattr(reports_router, "is_notion_configured", lambda: True)
        session = auth_logic.create_session("admin@example.com", True)
        with TestClient(app) as client:
            response = client.get(
                f"/result/{job_id}",
                cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
            )

    assert response.status_code == 200, response.text[:400]
    body = response.text
    # 카드 경로로 떨어졌는가(표·화살표가 아니라).
    assert 'class="section-content-card"' in body
    # 제목 = 첫 이름, 범위 칸 = 이름 전부.
    assert f"<h4>{_NAMES[0]}</h4>" in body or _NAMES[0] in body
    assert "·".join(_NAMES) in body
    assert f"<dd>{NAME_CARD_REASON}</dd>" in body
    # 「사업적 역할」은 빈 칸이라 우리 카드에서는 «빠져야» 한다.
    assert body.count("<dt>사업적 역할</dt>") == 1


# ══════════════════════════════════════════════════════════
# ③ PDF — 실제 렌더 바이트와 그림 한 장
# ══════════════════════════════════════════════════════════


def test_PDF가_이름_카드를_실제로_그린다(보강된_보고서) -> None:
    import pypdfium2 as pdfium

    from src.features.export_pdf import release as pdf_release

    candidate = pdf_release.prepare_pdf_release(보강된_보고서)
    pdf_bytes = candidate.pdf_bytes
    assert pdf_bytes.startswith(b"%PDF-")

    text = "".join(
        "".join((page.extract_text() or "").splitlines())
        for page in PdfReader(io.BytesIO(pdf_bytes)).pages
    )
    # CJK는 글자 사이에 공백이 낄 수 있어 느슨하게 찾는다.
    for name in _NAMES:
        assert re.search(r"\s*".join(map(re.escape, name)), text), name
    assert re.search(r"\s*".join(map(re.escape, NAME_CARD_REASON)), text)

    # 사람이 눈으로 볼 수 있게 그림 한 장을 남긴다(시험 판정에는 안 쓴다).
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page_index = next(
            index
            for index in range(len(document))
            if re.search(
                r"\s*".join(map(re.escape, _NAMES[0])),
                "".join(
                    (
                        PdfReader(io.BytesIO(pdf_bytes)).pages[index].extract_text()
                        or ""
                    ).splitlines()
                ),
            )
        )
        _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        document[page_index].render(scale=2.0).to_pil().save(
            _ARTIFACT_DIR / "name_card.png"
        )
    finally:
        document.close()


# ══════════════════════════════════════════════════════════
# ④ 노션 — 표 한 행으로 나간다
# ══════════════════════════════════════════════════════════


def _notion_table_rows(report) -> list[list[str]]:
    from src.features.export_notion.logic import build_blocks

    return [
        [
            "".join(part.get("text", {}).get("content", "") for part in cell)
            for cell in block["table_row"]["cells"]
        ]
        for table in build_blocks(report)
        if table.get("type") == "table"
        for block in table["table"]["children"]
        if block.get("type") == "table_row"
    ]


def test_노션_블록에_이름_카드_행이_있다(FULL_보강된_보고서) -> None:
    """노션은 «공개 봉인 projection»만 읽는다 — 그 투영에 카드가 남는지 본다."""

    rows = _notion_table_rows(FULL_보강된_보고서)

    assert [_FULL_NAMES[0], "·".join(_FULL_NAMES), NAME_CARD_REASON, ""] in rows, rows


# ══════════════════════════════════════════════════════════
# ⑤ FULL — 공개 봉인·manifest·evidence invariant를 실제로 지난다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 SHADOW로 못 끝내나 — 노션은 공개 봉인 projection만 읽고, 그 투영은
#   FULL이 만드는 표 manifest 참조를 요구한다. 그리고 우리 카드가 진짜로
#   위험한 자리가 바로 거기다: 인용이 장별 근거 소유권을 벗어나거나 봉인이
#   행을 못 읽으면 «보고서 전체»가 막힌다. 그 경로를 안 태우면 이 기능은
#   운영에서 처음으로 터진다.


_FULL_NAMES = ("하늘소년단", "바다소녀들", "별무리")


def _full_packets_with_names():
    from src.features.composer.constants import SECTION_IDS
    from src.features.composer.port import (
        SectionEvidencePacket,
        SectionEvidencePacketSet,
    )
    from src.features.composer.tests.test_section_public_manifest import (
        _MARKS,
        _document_content_sha256,
        _fragment_text,
    )
    from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
    from src.shared.report_quality.source_identity import (
        document_identity_from_parts,
    )

    generation = "a" * 64
    packets = []
    for index, section_id in enumerate(SECTION_IDS, start=1):
        url = f"https://manifest.example/document/{index}"
        document_text = _fragment_text(_MARKS[index - 1])
        fragments = [
            CollectedFragment(
                fragment_id=str(index),
                kind="회사 공식 자료",
                text=document_text,
                source_url=url,
                document_title=f"공식 자료 {index}",
                document_identity=document_identity_from_parts(url=url),
                document_content_sha256=_document_content_sha256(document_text),
                supported_claim_slots=CLAIM_SLOTS_BY_SECTION[section_id],
            )
        ]
        if section_id == PORTFOLIO_TABLE_SECTION_ID:
            for offset, name in enumerate(_FULL_NAMES):
                name_id = str(30 + offset)
                name_url = f"https://manifest.example/document/{name_id}"
                name_text = f"㈜가나다뮤직 | {name} | 전속계약"
                fragments.append(
                    CollectedFragment(
                        fragment_id=name_id,
                        kind="회사 공식 자료",
                        text=name_text,
                        source_url=name_url,
                        document_title=f"이름 표 {name_id}",
                        document_identity=document_identity_from_parts(
                            url=name_url
                        ),
                        document_content_sha256=_document_content_sha256(
                            name_text
                        ),
                        location=compose_name_location(
                            f"{_TITLE} · {offset + 2}행", NAME_KIND_IP, name
                        ),
                        supported_claim_slots=CLAIM_SLOTS_BY_SECTION[section_id],
                    )
                )
        packets.append(
            SectionEvidencePacket(
                company_id="00123456",
                evidence_generation_sha256=generation,
                section_id=section_id,
                fragments=tuple(fragments),
            )
        )
    return SectionEvidencePacketSet(
        company_id="00123456",
        evidence_generation_sha256=generation,
        packets=tuple(packets),
    )


@pytest.fixture(scope="module")
def FULL_실행결과():
    from src.features.composer.tests.test_section_public_manifest import _run_full

    output, _writer, _reviewer, _diagram = _run_full(
        packets=_full_packets_with_names()
    )
    return output


@pytest.fixture(scope="module")
def FULL_보강된_보고서(FULL_실행결과):
    return FULL_실행결과.report


def test_FULL도_이름_카드를_덧붙이고_봉인을_통과한다(FULL_실행결과) -> None:
    output = FULL_실행결과

    assert output.portfolio_name_card_count == len(_FULL_NAMES), (
        f"이름 카드가 안 붙었습니다 — 사유: "
        f"{output.portfolio_name_card_blocked_reason!r}"
    )
    assert output.unused_portfolio_name_count == 0
    # 봉인·투영이 실제로 만들어졌다는 뜻 — 이게 없으면 노션이 못 읽는다.
    assert output.report.public_projection is not None
    assert output.report.public_structure_manifest
    rows = _portfolio_table(output.report).rows
    assert rows == [
        [
            _FULL_NAMES[0],
            "·".join(_FULL_NAMES),
            NAME_CARD_REASON,
            "",
        ]
    ], rows


class _ThinThenFullWriter:
    """3장만 첫 회차에 얇게 쓰고, 승인받은 재호출에서 채우는 가짜 작가.

    ★ 왜 옆 파일의 `_RecoveringPacketWriter`를 못 쓰나 — 그 도구는
      ``assert len(fragment_ids) == 1``로 «packet에 조각이 딱 하나»를 요구한다.
      이름 조각을 넣은 3장 packet은 조각이 넷이라 그 단정에서 죽는다. 그래서
      조각이 여럿인 packet을 받는 같은 모양의 도구를 여기 둔다.
    """

    def __init__(self) -> None:
        from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
        from src.features.composer.tests.test_section_public_manifest import (
            _ENDINGS,
            _MARKS,
        )
        from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION

        self._grade = GRADE_CONFIRMED
        self._section_ids = SECTION_IDS
        self._endings = _ENDINGS
        self._marks = _MARKS
        self._slots_by_section = CLAIM_SLOTS_BY_SECTION
        self.section_calls: dict[str, int] = {}

    def __call__(self, prompt: str) -> str:
        fragment_ids = re.findall(r"\[조각 (\d+)\] \(", prompt)
        assert fragment_ids
        first = int(fragment_ids[0])
        section_id = self._section_ids[first - 1]
        mark = self._marks[first - 1]
        section_call = self.section_calls.get(section_id, 0) + 1
        self.section_calls[section_id] = section_call
        slots = self._slots_by_section[section_id]
        thin = section_id == PORTFOLIO_TABLE_SECTION_ID and section_call == 1
        endings = (self._endings[0],) if thin else self._endings
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": (
                            f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 과제 "
                            f"대응 협력 실적 {ending} 공식 자료에서 확인했다."
                        ),
                        "인용": [fragment_ids[0]],
                        "등급": self._grade,
                        "주장슬롯": slots[index % len(slots)],
                    }
                    for index, ending in enumerate(endings)
                ]
            },
            ensure_ascii=False,
        )


def _run_recovering_full_with_names():
    from src.features.composer.tests.test_section_public_manifest import (
        _BoundGroupedReviewer,
        _NoDiagram,
    )
    from src.shared.report_evidence.constants import ReleaseMode

    writer = _ThinThenFullWriter()
    output = run_v2(
        "가나다전자",
        (),
        None,
        writer_ask=writer,
        reviewer_ask=_BoundGroupedReviewer(),
        diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_full_packets_with_names(),
        company_id="00123456",
        build_identity_sha256="b" * 64,
    )
    return output, writer


def test_보충_회차가_3장을_다시_써도_이름_카드가_남는다() -> None:
    """3장이 보충 대상이면 그 장이 통째로 갈린다 — 카드도 함께 사라진다.

    ★ 이 시험이 없으면 보충이 도는 회사에서만 카드가 조용히 없어진다.
      첫 후보에서는 붙었으니 어떤 시험도 안 깨지고, 운영에서만 안 보인다.
    """

    output, writer = _run_recovering_full_with_names()

    # ★ 보충 회차가 «실제로 돌았는가»부터 확인한다 — 안 돌았으면 이 시험은
    #   기본 경로를 한 번 더 도는 것일 뿐 아무것도 안 지킨다.
    assert writer.section_calls[PORTFOLIO_TABLE_SECTION_ID] == 2, (
        writer.section_calls
    )
    assert output.portfolio_name_card_count == len(_FULL_NAMES), (
        f"보충 뒤 이름 카드가 사라졌습니다 — 사유: "
        f"{output.portfolio_name_card_blocked_reason!r}"
    )
    rows = _portfolio_table(output.report).rows
    assert rows[-1][0] == _FULL_NAMES[0], rows


def test_FULL_인용은_3장_소유_조각만_쓴다(FULL_보강된_보고서) -> None:
    """소유 밖 조각을 인용하면 evidence invariant가 보고서를 통째로 막는다."""

    table = _portfolio_table(FULL_보강된_보고서)
    cited = {
        int(value)
        for value in re.findall(r"\[(\d+)\]", " ".join(table.source_cites))
    }

    assert cited == {30, 31, 32}
    # 그 번호 전부가 «부록에도» 있어야 한다 — 인용-부록 1:1이 이 카드에서
    # 깨지면 validate_v2가 보고서를 통째로 막는다.
    부록 = {source.number for source in FULL_보강된_보고서.citations}
    assert cited <= 부록, sorted(부록)
