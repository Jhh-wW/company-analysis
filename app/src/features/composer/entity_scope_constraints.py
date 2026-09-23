"""같은 공시 문서의 «인용하지 않은» 관계법인 회계범위 각주를 제약으로만 운반한다.

★ 4차 실측 [27] — 「…Japan을 종속기업으로 두고 있으며 … 거래」는 특수관계자 조각만
  인용했다. 같은 공시의 «종속기업에서 제외» 각주가 검수 묶음에 와 있어도 후보의
  자기 인용 원문(sources)에 없어서 범위 방어를 우회했다.
★ 긍정 근거 sources를 넓혀 풀면 다른 근거를 빌려 승인하는 길이 열린다. 그래서
  sources·인용 번호·원문·해시·원장은 그대로 두고, 같은 문서의 제한 단서만 이
  context로 따로 넘긴다. 판정(법인·기간·표 행·술어 결속)은 scope 가드가 한다 —
  이 모듈은 scope 파서를 import하지 않는다(설계 `fourth-scope-constraint-sidecar`).

묶는 조건(모두):
  · 인용 조각과 제약 조각의 ``document_identity``가 비어 있지 않고 정확히 같으며,
    DART 접수번호 정규형(``document:dart.fss.or.kr:<14자리>``)이다. 제목·법인명·URL
    유사성으로 같은 문서를 추정하지 않는다.
  · 제약 조각은 등록된 종류(``LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE``)이고 후보가 인용하지
    않은 조각이다. 인용한 각주는 이미 자기 인용 원문으로 기존 가드가 본다.
  · 같은 신원이어도 문서 전체 원문 해시·보고기간이 «알려진 값끼리» 다르면 묶지 않는다.
    먼저 인용의 알려진 값과 어긋나는 각주를 빼고, 남은 context 전체(인용 + 각주)에서도
    알려진 값이 둘 이상이면 그 문서의 context를 만들지 않는다 — 인용 값을 모를 때
    서로 다른 각주 중 하나를 첫 값·다수결·최신 날짜로 고를 근거가 없기 때문이다.
빈 신원(legacy 직접 호출)은 대상 밖이다 — 해결한 것처럼 신원·해시·기간을 지어내지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from src.features.composer.constants import DART_DOCUMENT_HOST
from src.features.composer.entity_scope_constraint_constants import (
    DART_RECEIPT_NUMBER_RE,
    ENTITY_SCOPE_CONFLICT_FIELDS,
    ENTITY_SCOPE_CONSTRAINT_KINDS,
)
from src.features.composer.port import CollectedFragment
from src.shared.report_quality.source_identity import document_identity_components


@dataclass(frozen=True)
class EntityScopeContext:
    """한 DART 공시 문서에서 후보가 인용한 원문과, 인용하지 않은 제외 제약 원문.

    ``constraint_sources``는 제한 단서일 뿐 인용 근거가 아니다 — 긍정 근거 sources에
    합치지 않는다. 두 사전 모두 조각 id → 원문 그대로이며 읽기 전용이다.
    """

    document_identity: str
    cited_sources: Mapping[str, str]
    constraint_sources: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "cited_sources", MappingProxyType(dict(self.cited_sources)))
        object.__setattr__(self, "constraint_sources", MappingProxyType(dict(self.constraint_sources)))


def is_canonical_dart_identity(identity: str) -> bool:
    """정확한 ``document:dart.fss.or.kr:<14자리 접수번호>`` 신원인가 — 공백·변형은 거절한다."""

    if not identity or identity != identity.strip():
        return False
    host, document_id = document_identity_components(identity)
    return (
        host == DART_DOCUMENT_HOST
        and DART_RECEIPT_NUMBER_RE.fullmatch(document_id) is not None
        and identity == f"document:{host}:{document_id}"
    )


def _known_values(fragments: Sequence[CollectedFragment], field: str) -> frozenset[str]:
    return frozenset(value for fragment in fragments if (value := str(getattr(fragment, field, "") or "").strip()))


def _consistent(fragments: Sequence[CollectedFragment]) -> bool:
    """문서 전체 해시·보고기간이 칸마다 알려진 값 하나 이하일 때만 참 — 빈 값은 «모름»이다."""

    return all(len(_known_values(fragments, field)) <= 1 for field in ENTITY_SCOPE_CONFLICT_FIELDS)


def build_entity_scope_contexts(
    citations: Sequence[str], frag_by_id: Mapping[str, CollectedFragment],
) -> tuple[EntityScopeContext, ...]:
    """후보가 인용한 DART 문서마다, 같은 문서의 인용 밖 등록 각주가 있을 때만 context를 만든다.

    인용 순서대로 문서를 한 번씩 본다. 제약 각주가 없으면 그 문서의 context는 없다.
    인용·조각 객체는 읽기만 한다.
    """

    cited_ids = tuple(dict.fromkeys(fid for fid in citations if fid in frag_by_id))
    cited_set = frozenset(cited_ids)
    contexts: list[EntityScopeContext] = []
    seen: set[str] = set()
    for fid in cited_ids:
        identity = frag_by_id[fid].document_identity
        if identity in seen or not is_canonical_dart_identity(identity):
            continue
        seen.add(identity)
        cited = [frag_by_id[cid] for cid in cited_ids if frag_by_id[cid].document_identity == identity]
        constraints = {
            other_id: fragment
            for other_id, fragment in frag_by_id.items()
            if other_id not in cited_set
            and fragment.kind in ENTITY_SCOPE_CONSTRAINT_KINDS
            and fragment.document_identity == identity
            and _consistent((*cited, fragment))
        }
        # 인용 값과 맞는 각주만 남긴 뒤에도 context 전체에 알려진 값이 둘 이상이면
        # (인용 값을 몰라 각주끼리 어긋남) 이 문서의 제약은 만들지 않는다.
        if not constraints or not _consistent((*cited, *constraints.values())):
            continue
        contexts.append(EntityScopeContext(
            document_identity=identity,
            cited_sources={fragment.fragment_id: fragment.text for fragment in cited},
            constraint_sources={other_id: fragment.text for other_id, fragment in constraints.items()},
        ))
    return tuple(contexts)
