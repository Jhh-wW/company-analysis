"""제품명과 역할을 모두 자기 원문이 지지하는 표에서 칸 분할을 보존한다.

모의 검수 응답으로 실제 두 연결 경로를 확인한다. 실제 모델의 의미 판단 시험은 아니다.
"""

import json
import re

import pytest

from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


PRODUCT = "저비용 서비스"
CAUSE_TEXT = "저비용 서비스가 이익 증가의 주요 원인이다."
SOURCE_TEXT = "회사는 저비용 서비스를 제공한다. " + CAUSE_TEXT
RELATION = {
    "유형": "인과", "근거": "own", "원인": PRODUCT,
    "결과": "이익 증가", "원문": CAUSE_TEXT,
}


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("role_text", ("이익 증가의 주요 원인", CAUSE_TEXT))
def test_supported_product_and_role_keep_their_cell_boundaries(grouped, role_text):
    # 실제 네 칸은 제품명·범위·추진 근거·사업적 역할이다.
    row = FlowRow((PRODUCT, "", "", role_text), ("own",))
    report = ComposedReport((ComposedSection("portfolio", (), flow_rows=(row,)),))
    fragments = (CollectedFragment("own", "제품 설명", SOURCE_TEXT),)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        if grouped:
            items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
            assert len(items) == 1, "제품 표의 검수 후보가 하나여야 합니다."
            item = items[0]
            entry = {"번호": item.number, "장": item.section, "근거": list(item.citations)}
        else:
            entry = {"번호": 1}
        entry.update({"결과": "참", "검증근거": {"관계": [RELATION]}})
        return json.dumps({"판정": [entry]}, ensure_ascii=False)

    if grouped:
        checked = verify_report(
            report, fragments, None, ask,
            allowed_fragment_ids_by_section={"portfolio": frozenset(("own",))},
        )
    else:
        checked, problems = check_diagrams(report, fragments, ask)
        assert problems == ()
    assert checked.sections[0].flow_rows == (row,)
    assert len(prompts) == 1
