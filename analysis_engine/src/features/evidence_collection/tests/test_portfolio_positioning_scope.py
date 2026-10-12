"""제품 역할 분류만 제한하며 원문과 구체 제품 사실을 보존한다."""
import hashlib

from features.evidence_collection.business_slot_scope import business_slot_scope_problem
from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.tests.test_streaming_collection import _collect

SLOT = "portfolio:product_role"
POSITIONING = "주력 제품 소개: 우리는 하이테크 기술력으로 미래 산업에 대한 다양한 솔루션을 제시합니다. 시장을 이끌어가고 있습니다."


def test_포지셔닝에_제품표제가_붙어도_제품역할로_채점하지_않는다():
    scores, observed = score_fragment_slots_with_signal(POSITIONING)
    assert observed
    assert SLOT not in {score.slot_id for score in scores}
    assert business_slot_scope_problem(POSITIONING, SLOT)


def test_혼합원문의_실제제품과_원문위치_해시를_보존한다():
    fact = "주력 제품인 설비진단솔루션은 진동을 측정하여 이상을 감지한다."
    text = POSITIONING + " " + fact
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert SLOT in {score.slot_id for score in scores}
    assert not business_slot_scope_problem(text, SLOT)
    harvest = _collect(text)
    for fragment in (*harvest.fragments, *harvest.unclassified_fragments):
        begin, end = map(int, fragment.location.split("-"))
        assert text[begin:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
    for document in (*harvest.documents, *harvest.unclassified_documents):
        assert document.content_sha256 == hashlib.sha256(text.encode()).hexdigest()
