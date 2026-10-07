"""슬롯 허용쌍을 만들기 전에 단독 조사 원문을 운영 역할로 보존한다."""
from src.features.news_intake.claim_role import RolePart, sentence_ranges
from src.features.news_intake.identity_names import mentions_target
from src.features.news_intake.models import NewsCompanyContext
from src.shared.report_evidence.partnership_scope import research_partnership_problem
from src.shared.report_evidence import partnership_scope_constants as c


def partnership_role_parts(text: str, *, claim_slot: str,
                           company: NewsCompanyContext,
                           temporal_status: str = "") -> tuple[RolePart, ...] | None:
    """None은 변경 없음, 빈 tuple은 제휴 칸만 배제한다. 원문을 만들지 않는다."""
    if not research_partnership_problem(text, claim_slot):
        return None
    if temporal_status == "planned":
        return ()
    ranges = sentence_ranges(text)
    research = [text[start:end] for start, end in ranges
                if c.RESEARCH_ACTION_RE.search(text[start:end])]
    # 수행 예정·미수행을 운영 역할의 새 지원쌍으로 승격하지 않는다. 미래나 부정
    # 단어가 다른 활동에 있다는 이유로 완료된 조사까지 배제하지는 않는다.
    if any(c.UNPERFORMED_RESEARCH_TAIL_RE.search(unit[action.end():])
           for unit in research for action in c.RESEARCH_ACTION_RE.finditer(unit)):
        return ()
    # 조사 수행 주어가 대상 회사임을 같은 문장에서 독립 확인해야 옮길 수 있다.
    # 다른 문장의 회사 이름이나 발행처 이름을 주어로 빌리지 않는다.
    actors = [c.ACTOR_PREFIX_RE.match(unit) for unit in research]
    if not actors or not all(actor and mentions_target(actor[1], company) for actor in actors):
        return ()
    # 독립 협약 문장이 섞이면 어느 범위의 상대인지 추정해 통째로 옮기지 않는다.
    if any(c.RELATION_RE.search(text[start:end]) for start, end in ranges):
        return ()
    return (RolePart(0, len(text), "operations_partners", c.OPERATING_ROLE_SLOT),)
