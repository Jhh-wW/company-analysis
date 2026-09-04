"""3층 지우개 검출 시험 — 카나리아 3종을 전부 검출해야 통과다.

카나리아 3종:
  이름 카나리아7742 · 전화 010-0000-7742 · 주민등록번호 999999-9997742
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.logging_util import LOG_DIR
from features.privacy_filter.logic import Detection, _replace_analyzer_spans, detect, erase

카나리아_이름 = "카나리아7742"
카나리아_전화 = "010-0000-7742"
카나리아_주민번호 = "999999-9997742"

가짜_이력서 = f"""이력서
이름: {카나리아_이름}
연락처: {카나리아_전화}
주민등록번호: {카나리아_주민번호}
이메일: canary@example.com
생년월일: 1996년 3월 15일
학력: 한국대학교 졸업"""

공고_예시 = """삼성전자 채용공고
[모집분야] 재무회계 신입  [근무지] 서울 강남구
[자격요건] 4년제 졸업, 지원자격은 전산회계 우대
[전형절차] 서류전형 → 면접전형  [접수기간] 채용시 마감"""


def _types(text: str) -> set[str]:
    return {d.entity_type for d in detect(text)}


# ── 로컬 치환기 회귀 시험 ──────────────────────────────────

def test_로컬_치환기_겹친_같은_유형은_한번만_지운다():
    spans = (
        Detection("KR_PHONE", 0, 4, 0.95),
        Detection("KR_PHONE", 2, 6, 0.70),
    )
    assert _replace_analyzer_spans("abcdefgh", spans) == "[삭제:전화번호]gh"


def test_로컬_치환기_맞닿은_범위는_각각_지운다():
    spans = (
        Detection("KR_PHONE", 0, 2, 0.95),
        Detection("EMAIL_ADDRESS", 2, 4, 0.95),
    )
    assert _replace_analyzer_spans("abcdef", spans) == (
        "[삭제:전화번호][삭제:이메일]ef")


def test_로컬_치환기_유형별_표식은_항상_같다():
    expected = {
        "KR_RRN": "[삭제:주민등록번호]",
        "KR_PHONE": "[삭제:전화번호]",
        "KR_BIRTHDATE": "[삭제:생년월일]",
        "KR_NAME": "[삭제:이름]",
        "PERSON": "[삭제:이름]",
        "EMAIL_ADDRESS": "[삭제:이메일]",
        "UNKNOWN": "[삭제:개인정보]",
    }
    for entity_type, marker in expected.items():
        span = Detection(entity_type, 0, 1, 0.95)
        assert _replace_analyzer_spans("x", (span,)) == marker


def test_로컬_치환기_빈값과_미검출은_그대로_돌려준다():
    assert _replace_analyzer_spans("", ()) == ""
    assert _replace_analyzer_spans("공개 정보", ()) == "공개 정보"


# ── 카나리아 3종 (통과 조건) ──────────────────────────────

def test_카나리아_이름_검출():
    assert "KR_NAME" in _types(f"이름: {카나리아_이름}")


def test_카나리아_전화_검출():
    assert "KR_PHONE" in _types(f"연락처: {카나리아_전화}")


def test_카나리아_주민번호_검출():
    assert "KR_RRN" in _types(카나리아_주민번호)


def test_카나리아_3종_전부_삭제():
    r = erase(가짜_이력서)
    for canary in (카나리아_이름, 카나리아_전화, 카나리아_주민번호):
        assert canary not in r.text


# ── 표기 변형 (우회 방지 — 독립 검증 발견분 회귀 포함) ──────────────

def test_무구분_전화도_검출():
    assert "KR_PHONE" in _types("연락처 01000007742")


def test_전화_구분자_변형_전부_검출():
    # 독립 검증에서 뚫린 표기들 — 구분자 {0,3}개·괄호 국번·끝자리 분리
    for 표기 in ("010 - 0000 - 7742", "010–0000–7742", "010~0000~7742",
                 "010/0000/7742", "(010) 0000-7742", "010-0000-77 42",
                 "010-00 00-7742", "010·0000·7742"):
        assert "KR_PHONE" in _types(f"연락처: {표기}"), 표기


def test_전화_전각숫자도_검출():
    r = erase("연락처: ０１０-００００-７７４２")
    assert "7742" not in r.text and r.counts.get("KR_PHONE", 0) >= 1


def test_공백_구분_주민번호도_검출():
    assert "KR_RRN" in _types("999999 9997742")


def test_주민번호_구분자_변형_전부_검출():
    # 독립 검증에서 뚫린 표기들 — 공백+하이픈 혼합·em-dash·점·제로폭 공백
    제로폭_표기 = "999999-\u200b9997742"
    for 표기 in ("999999 - 9997742", "999999—9997742",
                 "999999.9997742", 제로폭_표기):
        assert "KR_RRN" in _types(표기), repr(표기)


def test_생년월일은_일_글자까지_삭제():
    r = erase("생년월일: 1996년 3월 15일")
    assert "1996" not in r.text and r.text.endswith("[삭제:생년월일]")


def test_생년월일_2자리_연도도_삭제():
    r = erase("생년월일: 96년 3월 15일")
    assert "96년" not in r.text and "[삭제:생년월일]" in r.text


def test_4자리_연도는_통째로_판단_부분삭제_금지():
    # 2차 독립 검증 회귀 — 「1899년」의 꼬리 「99년」만 잡혀 부분 삭제되던 구조 결함
    for 원문 in ("생년월일: 1899년 3월 15일생", "설립일: 1850년 6월 6일",
                 "설립일: 2044년 3월 1일", "만료일: 2100년 1월 1일", "가상일자: 9999년 1월 1일"):
        assert erase(원문).text == 원문, 원문


def test_라벨_부가설명_괄호도_검출():
    # 2차 독립 검증 회귀 — 「이름(한글):」·「성명(국문):」 공식 서식 관용구 미검출
    for 문구 in (f"이름(한글) : {카나리아_이름}", f"성명(국문): {카나리아_이름}",
                 f"성명({카나리아_이름})", "이름(한글) : 홍길동"):
        r = erase(문구)
        assert 카나리아_이름 not in r.text and "홍길동" not in r.text, 문구


def test_이중_괄호_부가설명도_값까지_검출():
    # 3차 독립 검증 회귀 — 「이름(한글)(영문):」에서 값-괄호 분기가 부가설명을 값으로 오인,
    # 진짜 값이 통째로 새던 구멍. 긴 부가설명(20자 이내)도 함께 확인
    for 문구 in (f"이름(한글)(영문): {카나리아_이름}",
                 f"이름(한글 또는 영문으로 기재): {카나리아_이름}"):
        assert 카나리아_이름 not in erase(문구).text, 문구


def test_어휘_괄호는_콜론_없어도_값까지_검출():
    # 「이름(한글) 홍길동」 — 부가설명 「어휘」 괄호는 공백 구분자로도 값을 잡는다 (누출 방지 유지)
    for 문구 in ("이름(한글) 홍길동", f"성명(한글/영문) {카나리아_이름}"):
        r = erase(문구)
        assert "홍길동" not in r.text and 카나리아_이름 not in r.text, 문구


def test_값괄호_뒤_문장은_삼키지_않는다():
    # 4차 독립 검증 회귀 — 「성명(값) 다음단어」의 다음 단어(핵심 재료 포함)가 삭제되던 과삭제
    r = erase(f"성명({카나리아_이름}) 자격요건에 부합합니다")
    assert 카나리아_이름 not in r.text and "자격요건에 부합합니다" in r.text
    r2 = erase(f"이름({카나리아_이름}) 근무지는 서울입니다")
    assert 카나리아_이름 not in r2.text and "근무지는 서울입니다" in r2.text


def test_서식_안내문은_보존():
    # 4차 독립 검증 회귀 — 항목명 나열 안내문에서 「생년월일」·「확인이」가 이름으로 삼켜지던 과삭제
    for 원문 in ("이력서에는 성명(한글/영문) 생년월일 연락처를 정확히 기재해주시기 바랍니다",
                 "이름(한글 또는 영문으로 표기 가능) 확인이 필요합니다",
                 "이름 항목은 선택사항입니다",
                 "성명 필드는 비워두셔도 됩니다"):
        assert erase(원문).text == 원문, 원문


def test_그룹별_괄호_전화도_검출():
    # 2차 독립 검증 회귀 — 「[010]-[0000]-[7742]」 그룹별 감쌈
    assert "KR_PHONE" in _types("[010]-[0000]-[7742] 로 연락주세요")


# ── 한글 이름 — 라벨 패턴 + NER 2겹 ──────────────────────

def test_라벨_이름_검출():
    # NER 단독은 「성명 홍길동」의 홍길동을 놓친다(실측) — 라벨 패턴이 잡는다
    assert "KR_NAME" in _types("성명 홍길동")


def test_라벨_변형도_검출():
    # 독립 검증에서 뚫린 라벨 표기들 — 괄호 라벨·글자 사이 공백·하이픈 구분자
    for 문구 in (f"[이름] {카나리아_이름}", f"이 름 : {카나리아_이름}",
                 f"이름 - {카나리아_이름}", f"이름 {카나리아_이름}"):
        r = erase(문구)
        assert 카나리아_이름 not in r.text, 문구


def test_영문_이름과_영문_라벨도_검출():
    assert "Gildong" not in erase("이름: Hong Gildong").text
    assert "홍길동" not in erase("Name: 홍길동").text


def test_성_띄어쓴_이름도_라벨_뒤에선_전부_삭제():
    r = erase("이름: 김 철수")
    assert "철수" not in r.text


def test_라벨없는_단독_이름은_못잡는다_한계():
    # 알려진 한계(독립 검증 확인) — 이력서 머리글의 라벨 없는 한 줄 이름은 NER이 못 잡는다.
    # 방어선: 이런 문서는 1층 AI(공고 아님)·2층 값 조합이 문서째 폐기한다. 실캡처 시험 때 재실측.
    assert 카나리아_이름 in erase(카나리아_이름).text


def test_성_한글자와_어휘_조합은_못잡는다_한계():
    # 알려진 한계(5차 독립 검증) — 「한 글자 성 + 어휘」가 값 자리를 채우면 성까지 놓친다.
    # 자연 문장에서 우연히 나오기 어려운 조합 — 실캡처 실측 때 빈도 관찰. 정상 이름은 잡힌다(아래 대조).
    assert "이" in erase("성명(한글) 이 근무지").text
    assert "철수" not in erase("성명(한글) 김 철수").text


def test_본문속_이름은_NER이_검출():
    assert "PERSON" in _types("박영희 대리가 담당합니다")


def test_이메일_검출():
    assert "EMAIL_ADDRESS" in _types("문의: canary@example.com")


# ── 공고 보존 (과삭제 방지 — 독립 검증 발견분 회귀 포함) ──────────

def test_공고는_회사명_근무지_항목_보존():
    r = erase(공고_예시)
    for 보존어 in ("삼성전자", "서울 강남구", "자격요건", "전형절차"):
        assert 보존어 in r.text


def test_접수기간_마감일_날짜는_보존():
    # 출생 연도 범위(~2011) 밖의 날짜는 생년월일이 아니다 — 접수기간이 지워지던 회귀
    r = erase("[접수기간] 2026.09.01 ~ 2026.09.14 17:00")
    assert "2026.09.01" in r.text and "2026.09.14" in r.text


def test_전형일정_날짜와_줄바꿈_보존():
    원문 = "- 인적성검사: 2026.09.20\n- 1차 면접: 2026/10/05"
    assert erase(원문).text == 원문


def test_2자리_연도_마감일은_보존():
    # 「26년 8월 31일」(2026) — 2자리 연도는 출생 가능 범위(40~99·00~11)만 잡는다
    r = erase("[접수기간] 26년 8월 31일까지")
    assert "26년 8월 31일" in r.text


def test_영문혼용_공고_보존():
    # ko NER이 영어 토큰을 이름으로 오인하던 회귀 — 모양 필터(한글 2~4자)로 차단
    원문 = "자격요건: React 사용 경험 3년 이상\n- Kotlin, Spring Boot 기반 API 개발"
    assert erase(원문).text == 원문


def test_근무지_항목어_고용형태값_보존():
    for 원문, 보존어 in (("근무지: 대전광역시 유성구", "유성구"),
                         ("우대사항: 정보처리기사 자격증 보유자", "우대사항"),
                         ("■ 고용형태: 정규직 (수습 3개월)", "정규직"),
                         ("제출서류: 이력서, 자기소개서", "이력서")):
        assert 보존어 in erase(원문).text, 원문


def test_행사일_최종합격_등_보존():
    # 2차 독립 검증 회귀 — 목록 밖 채용 용어의 이름 오인 과삭제
    for 원문 in ("행사일: 2020년 5월 5일", "박람회: 2026년 8월 31일",
                 "유의사항: 2026년 8월 31일",
                 "채용과정: 서류전형 → 실무면접 → 최종합격"):
        assert erase(원문).text == 원문, 원문


def test_문서번호는_전화로_오인_안_함():
    # 숫자 경계 — 「제2019-0123456호」 내부가 전화로 부분 삭제되던 회귀
    assert "KR_PHONE" not in _types("공고번호 제2019-0123456호")


def test_지원자격은_이름으로_오검출_안_함():
    assert "KR_NAME" not in _types("[자격요건] 지원자격: 4년제 졸업")


def test_담당자_전화는_공고여도_지운다():
    # 3층은 「값」 자체를 지운다 — 공고 통과 여부(2층)와 별개다
    assert "KR_PHONE" in _types("문의: 인사팀 010-1234-5678")


# ── S1 규율 — 값이 밖으로 새지 않는다 ──────────────────────

def test_detect_반환에_원문값_없음():
    d = detect(가짜_이력서)[0]
    assert set(vars(d)) == {"entity_type", "start", "end", "score"}


def test_로그에는_건수만_값은_없다():
    run_id = "카나리아검출시험"
    erase(가짜_이력서, run_id=run_id)
    last = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    row = json.loads(last)
    assert row["out"]["검출건수"]["KR_RRN"] >= 1
    for canary in (카나리아_이름, 카나리아_전화, 카나리아_주민번호):
        assert canary not in last
