"""검증근거.수치 배열에서 같은 근거·같은 원문을 가리키는 원문참조를 되돌린다.

★ 왜 여기서만 하나 — 같은 판정 번호의 수치 배열이 같은 원문을 여러 행에서
  반복 인용하면(다년도 열거처럼) 검수 응답 JSON이 그만큼 커진다. 근거 조각
  ID가 이미 있는데도 원문 전체 문자열을 행마다 다시 베끼는 부분만 줄인다.

★ 이 모듈이 하지 않는 것 — 원문 문자열은 한 글자도 정규화·요약·변경하지
  않는다. 참조를 못 푼 행은 «틀렸다»고 판정하지 않고 원문 없는 행으로
  남겨서, 기존 ``_quote``가 그대로(원문 없음 → 결속 실패) 처리하게 한다.
  그래서 이 모듈에는 항목·값·부호·차원·기간 검증이 전혀 없다 — 그건 여전히
  ``grounding.py``의 기존 검증기 몫이다.
"""

from __future__ import annotations

from collections.abc import Mapping

#: 원문을 대신하는 참조 키 — 1부터 시작하는, 같은 수치 배열 안의 앞 행 번호.
NUMERIC_QUOTE_REF_KEY = "원문참조"
_QUOTE_KEY = "원문"
_EVIDENCE_ID_KEY = "근거"


def resolve_numeric_quote_refs(entries: object) -> object:
    """참조를 원문 문자열로 되돌린 새 목록을 돌려준다. 입력을 제자리에서 바꾸지 않는다.

    허용: 앞 행이 «직접» 원문 문자열을 갖고 있고(그 앞 행도 참조가 아니고),
    근거 ID가 이번 행과 정확히 같을 때만 그 원문을 그대로 복사해 채운다.

    거절(원문을 채우지 않고 원문참조 키만 지운다 — 그래서 이후 ``_quote``가
    «원문 없음»으로 그대로 실패시킨다. 즉 해당 후보 하나만 근거검증 실패로
    닫히고, 그 실패가 다른 후보나 다른 판정 번호로 번지지 않는다):
      · 참조연쇄(가리킨 행도 원문참조를 씀)
      · 자기참조·미래참조(자기 자신이거나 뒤에 오는 행)
      · 범위밖·음수·0
      · bool·문자열 등 정수가 아닌 인덱스
      · 가리킨 행과 근거 ID가 다름
      · 원문과 원문참조를 한 행에 함께 씀

    이 배열 밖(다른 판정 번호·추세·시점 배열)을 가리키는 문법 자체가 없다 —
    인덱스는 오직 «이 수치 배열 안의 앞 행 번호»로만 해석되므로 다른 배열이나
    다른 판정을 참조할 길이 없다.
    """

    if not isinstance(entries, list):
        return entries

    resolved: list = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            resolved.append(entry)
            continue
        if NUMERIC_QUOTE_REF_KEY not in entry:
            resolved.append(entry)
            continue

        new_entry = dict(entry)
        reference = new_entry.pop(NUMERIC_QUOTE_REF_KEY)

        if _QUOTE_KEY in entry:
            # 원문과 참조를 동시에 준 행 — 어느 쪽도 신뢰하지 않고 닫는다.
            # 직접 원문이 그 자체로 유효해도(우연히 진짜 원문이어도) 통과시키지
            # 않는다 — 그래서 원문 키까지 지워 "원문 없음"으로 확실히 닫는다.
            new_entry.pop(_QUOTE_KEY, None)
            resolved.append(new_entry)
            continue
        if isinstance(reference, bool) or not isinstance(reference, int):
            resolved.append(new_entry)
            continue

        target_index = reference - 1  # 원문참조는 1부터 시작한다.
        if target_index < 0 or target_index >= index:
            # target_index < 0: 범위밖·음수·0. target_index >= index: 자기 자신
            # 이거나 아직 나오지 않은(미래) 행을 가리킨다 — 둘 다 금지.
            resolved.append(new_entry)
            continue

        target = entries[target_index]
        if (
            not isinstance(target, Mapping)
            or NUMERIC_QUOTE_REF_KEY in target  # 참조연쇄 금지 — 대상은 직접 원문만.
            or not isinstance(target.get(_QUOTE_KEY), str)
            or not target[_QUOTE_KEY].strip()
        ):
            resolved.append(new_entry)
            continue

        current_id, target_id = entry.get(_EVIDENCE_ID_KEY), target.get(_EVIDENCE_ID_KEY)
        if (
            not isinstance(current_id, str)
            or not isinstance(target_id, str)
            # 문자열 자체는 그대로 두고(strip해서 같다고 하지 않는다) 정확히
            # 같은지만 비교한다 — 다만 그 정확한 문자열이 빈 값이거나 공백뿐이면
            # "같은 근거 ID"라는 사실 자체가 없는 것이므로 둘 다 거절한다.
            # 그러지 않으면 근거를 안 적은 행끼리 서로를 참조로 승인하고,
            # sources에 빈/공백 키가 있으면 이후 _quote까지 통과할 수 있었다.
            or current_id != target_id
            or not current_id.strip()
        ):
            resolved.append(new_entry)
            continue

        # 여기까지 왔으면 유효한 참조다 — 원문 문자열을 한 글자도 바꾸지 않고
        # 그대로 복사한다.
        new_entry[_QUOTE_KEY] = target[_QUOTE_KEY]
        resolved.append(new_entry)

    return resolved
