# -*- coding: utf-8 -*-
"""검증된 보도 조각 «하나»를 원문 그대로 옮긴 후보인지를 수집 객체로 증명한다.

★ 왜 따로 있나 — 역할·과금 결속 가드(`role_binding`)는 후보 문장과 «id→본문» 문자열만
  받는다. 그 안에서는 이 문장이 «검증된 보도를 그대로 옮긴 것인지», 날짜·발행처가
  확인됐는지, 이 장이 그 조각을 소유하는지를 알 수 없고 추정해서도 안 된다. 그래서
  자격은 실제 문장·`CollectedFragment`·장 소유권을 가진 검수 단계가 여기서 계산하고,
  그 결과를 «명시적 인자»로 안내 생성과 결속 검사에 함께 넘긴다.

★ AI 응답의 어떤 표식도 쓰지 않는다. 문장의 「원문대체후보」 표식도 쓰지 않는다 —
  수집 조각과 보고서 객체만 본다. 자격을 얻은 문맥은 후보지문·원문지문·인용·장을
  들고 다니며, 가드 쪽에서 실제 입력과 다시 대조해 맞지 않으면 쓰지 않는다.

⚠️ 닫힌 자격이다. 다음은 «자격 없음»이지 «탈락»이 아니다 — 기존 검사가 그대로 돈다:
   인용이 둘 이상, 인용이 보도가 아님, 수집 검증 표시·날짜·발행처가 빔, 장 소유권
   밖, 출처 접두사 뒤 본문이 조각 본문과 한 글자라도 다름(의역·부분 인용·요약).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from src.features.composer.grounding_constants import TABLE_SOURCE_ID
from src.features.composer.news_block import _is_news_fragment, news_ownership_from_claim_slots
from src.features.composer.news_usage import attribution_prefix
from src.features.composer.port import CollectedFragment
from src.shared.report_quality.supplementary_prose import NEWS_PROTECTED_SECTIONS


def _sha256(value: str) -> str:
    """진단의 후보지문과 같은 계산(UTF-8 SHA-256)."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerbatimNewsSource:
    """검증된 보도 조각 하나를 원문 그대로 옮긴 후보의 «결속 문맥».

    후보지문은 출처 접두사를 «포함한» 공개 문장의 지문(검수 진단과 같은 값)이고,
    원문지문은 조각 본문의 지문이다. 가드는 이 둘을 실제 입력에서 다시 계산해
    맞을 때만 문맥을 인정한다(`matches`).
    """

    source_id: str
    section_id: str
    attribution_prefix: str
    source_sha256: str
    candidate_sha256: str
    document_date: str
    publisher: str

    def matches(self, text: str, own_sources: Mapping[str, str]) -> bool:
        """가드가 받은 «접두사를 뗀 후보»와 «자기 인용»에 이 문맥이 실제로 결속되는가.

        ★ 문맥을 만든 뒤 후보나 인용이 바뀌었으면(재작성·다른 번호에 잘못 붙음)
          지문이 어긋나므로 쓰지 않는다. 보조 재무표(TABLE_SOURCE_ID)는 후보가 인용한
          조각이 아니라 표가 있으면 모든 후보에 함께 실리는 값이라 세지 않는다.
        """

        source = own_sources.get(self.source_id)
        if not isinstance(source, str) or not source.strip():
            return False
        if _sha256(source) != self.source_sha256:
            return False
        if text.strip() != source.strip():
            return False
        if _sha256(self.attribution_prefix + text) != self.candidate_sha256:
            return False
        others = [
            source_id for source_id in own_sources
            if source_id not in (self.source_id, TABLE_SOURCE_ID)
        ]
        return not others

    def as_diagnostic(self) -> dict[str, str]:
        """원문 없이 지문·인용·장·날짜·발행처만 남긴다."""

        return {
            "source_id": self.source_id,
            "section_id": self.section_id,
            "source_sha256": self.source_sha256,
            "candidate_sha256": self.candidate_sha256,
            "document_date": self.document_date,
            "publisher": self.publisher,
        }


def _owned_by_section(fragment: CollectedFragment, section_id: str) -> bool:
    """조각이 스스로 봉인해 온 의미 칸이 그 장의 것인가.

    ★ 보도표와 «같은» 정본 역매핑(`news_ownership_from_claim_slots`)을 쓴다. 칸 이름을
      「<장>:」 접두사로 잘라 장을 추정하면 「business_model:없는칸」 같은 값도 소유권이
      된다 — 정본 표에 없는 칸은 어느 장에도 속하지 않는다(fail-closed).
    """

    ownership = news_ownership_from_claim_slots((fragment,))
    return fragment.fragment_id in ownership.get(section_id, frozenset())


def verbatim_news_source(
    text: str,
    citations: Sequence[str],
    frag_by_id: Mapping[str, CollectedFragment],
    *,
    section_id: str,
    allowed_fragment_ids: Optional[frozenset[str]] = None,
) -> Optional[VerbatimNewsSource]:
    """후보 하나의 자격을 계산한다. 자격이 없으면 None(기존 검사 유지).

    Args:
        text: 공개 후보 문장 전체(출처 접두사 포함).
        citations: 그 문장의 «실제» 인용 id. 자동으로 붙는 보조 재무표는 여기 없다.
        frag_by_id: 수집 조각.
        section_id: 그 후보를 소유한 장.
        allowed_fragment_ids: packet 경로의 장별 허용 조각. 주어지면 그 안에 있어야 한다.
    """

    # ① 인용은 «정확히 하나»여야 한다. 잘못된 인용을 걸러낸 뒤 하나 남았다고 자격을
    #    주지 않는다 — 인용 개수 자체가 자격의 일부다.
    cited = tuple(str(citation) for citation in citations)
    if len(cited) != 1:
        return None
    fragment = frag_by_id.get(cited[0])
    if fragment is None or not _is_news_fragment(fragment):
        return None
    # ② 수집 단계가 법인·사업사실·원문·시점을 검증했다고 «봉인한» 조각만. packet 계약이
    #    bool 로 봉인하므로 여기서도 «정확히 True»만 받는다 — 문자열 "False"·숫자 1 같은
    #    참 같은 값은 검증 표시가 아니다.
    if getattr(fragment, "news_grounded", False) is not True:
        return None
    document_date = str(getattr(fragment, "document_date", "") or "").strip()
    publisher = str(getattr(fragment, "source_publisher", "") or "").strip()
    if not document_date or not publisher:
        return None
    # ③ 장 소유권 — 뉴스를 싣지 않는 장, 의미 칸이 없는 장, packet 허용 밖은 자격 없음.
    if not section_id or section_id in NEWS_PROTECTED_SECTIONS:
        return None
    if not _owned_by_section(fragment, section_id):
        return None
    if allowed_fragment_ids is not None and fragment.fragment_id not in allowed_fragment_ids:
        return None
    # ④ 출처 접두사를 뺀 후보가 조각 본문 «전체»와 같아야 한다. 느슨한 정규화는 없다.
    prefix = attribution_prefix(fragment)
    source_text = str(fragment.text or "").strip()
    if not source_text or not prefix or not text.startswith(prefix):
        return None
    if text[len(prefix):].strip() != source_text:
        return None
    return VerbatimNewsSource(
        source_id=fragment.fragment_id,
        section_id=section_id,
        attribution_prefix=prefix,
        source_sha256=_sha256(str(fragment.text or "")),
        candidate_sha256=_sha256(text),
        document_date=document_date,
        publisher=publisher,
    )
