"""2층 코드 검사 테스트 — 알려진 한계(공고 인용 자소서)까지 그대로 검증한다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from features.posting_gate.logic import find_sections, has_personal_combo, layer2

공고_예시 = """[모집분야] 재무회계 신입
[자격요건] 4년제 졸업, 전산회계 우대
[전형절차] 서류전형 → 면접전형
[접수기간] 채용시 마감"""

이력서_예시 = """이력서
성명: 김철수  생년월일: 1996년 3월 15일
연락처: 010-0000-1234
학력사항: 한국대학교 졸업"""

공고인용_자소서 = """채용공고의 우대사항을 보고 지원했습니다.
자격요건인 경력 3년을 충족하며 전형절차를 성실히 준비하겠습니다."""


def test_정상_공고는_통과():
    r = layer2(공고_예시)
    assert r.passed and len(r.sections_found) >= 4


def test_이력서는_값_조합으로_차단():
    r = layer2(이력서_예시)
    assert not r.passed and r.has_personal_combo


def test_공고인용_자소서는_2층을_뚫는다():
    # 실측으로 확인된 한계 — 항목 3개·값 조합 없음 → 통과. 1층 AI가 유일한 방어선인 이유
    r = layer2(공고인용_자소서)
    assert r.passed


def test_담당자_전화만_있는_공고는_조합_아님():
    assert not has_personal_combo("문의: 인사팀 010-1234-5678")


def test_마감일_날짜와_담당자_전화는_조합_아님():
    # 독립 검증 회귀 — 접수 마감일(출생 불가능 연도)은 생년월일 값이 아니다.
    # 이게 값으로 잡히면 마감일+문의 전화가 있는 정상 공고가 통째로 오차단된다.
    r = layer2("""[모집분야] 재무회계 신입
[자격요건] 4년제 졸업
[전형절차] 서류전형 → 면접전형
[접수기간] 2026년 8월 31일까지
문의: 인사팀 010-1234-5678""")
    assert r.passed and not r.has_personal_combo


def test_유니코드_대시_표기도_값_조합으로_잡는다():
    # 독립 검증 회귀 — en-dash 표기 값이 2층을 뚫던 구멍
    assert has_personal_combo("생년월일: 1996–3–15  연락처: 010–0000–7742")


def test_한_묶음_여러_단어도_항목_1개():
    assert find_sections("정규직 계약직 인턴 전부 모집") == ("고용형태",)


def test_항목_1개면_차단():
    r = layer2("복리후생이 좋은 회사에 다니고 싶다는 생각을 했다")
    assert not r.passed and "1개" in r.reason


def test_공백_낀_항목명도_검출한다():
    # 회귀 (OCR 실측) — 「필수 사항」·「우대 사항」이 미검출되어
    # 진짜 공고(로보스타)가 항목 0개로 판정되던 구멍
    found = find_sections("필수 사항: 경력 3년\n우대 사항: 관련 자격증")
    assert "자격요건" in found and "우대사항" in found


def test_전각_표기_항목명도_검출한다():
    # 정규화본 대조로 전각 문자 표기도 같은 항목으로 센다
    assert "자격요건" in find_sections("［자격요건］ 4년제 졸업")


def test_줄바꿈으로_갈라진_글자는_항목이_아니다():
    # 공백 허용은 같은 줄 1칸까지 — 줄바꿈까지 허용하면 「마감\n일정」처럼
    # 서로 다른 단어가 붙어 오검출된다 (보강의 안전 경계)
    assert find_sections("어제 마감\n일정을 확인했다") == ()


def test_공백_2칸_이상은_항목이_아니다():
    # 표(테이블)에서 셀이 다른 단어끼리 붙는 오검출 방지 — 허용은 1칸까지
    assert find_sections("필수  사항") == ()


def test_짧은_키워드는_공백_허용이_없다():
    # 독립 검증 발견 회귀 — 「채용시」·「마감일」에까지 공백을 허용하면
    # 「채용 시장」·「마감 일정」 같은 평범한 비공고 문장이 항목으로 잡힌다
    assert find_sections("마감 일정을 확인했다") == ()
    assert find_sections("채용 시장이 얼어붙었다") == ()


def test_뉴스체_문장이_문턱을_넘지_않는다():
    # 독립 검증 재현 입력 그대로 — 보강 1차안에서는 (복리후생, 접수기간) 2개로 오통과했다
    r = layer2("채용 시장 침체로 기업들이 복지 혜택을 줄이고 있다는 조사 결과가 나왔다.")
    assert not r.passed and r.sections_found == ("복리후생",)
