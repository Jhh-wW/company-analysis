"""공개 숫자·한글 수량 표현을 식별하는 공통 문법. 사실 승인 자체는 하지 않는다."""

from __future__ import annotations

import re

_NUMBER_TOKEN = re.compile(r"\d")
# 숫자를 한글로 바꿔 쓰는 것만으로 NumericBinding 경계를 우회하지 못하게 한다.
# 이 문법은 값을 계산하거나 사실을 승인하는 검증기가 아니다. 수량 단위가 바로
# 붙은 표현만 찾아 «구조화 결속 필요»로 보내며, '이 회사'처럼 우연히 수사가
# 들어간 일반 문장은 잡지 않는다.
_KOREAN_NUMBER_END = (
    # 단위 뒤에 다른 낱말이 바로 이어지면 그 단위가 아니라 한 단어일 수 있다.
    # 조+건을 「조건」으로, 한+개를 「한 개념」의 앞부분으로 읽지 않는다.
    # 조사(은/는/으로 등)가 붙은 실제 수량 표현은 그대로 허용한다.
    r"(?=(?:$|[^가-힣]|"
    r"(?:입니다|이었다|이라는|이라고|이다|이며|이고|인|일|"
    r"은|는|이|가|을|를|에|에서|으로|로|과|와|의|도|만|씩|부터|까지|보다)"
    r"(?:$|[^가-힣])))"
)
_SINO_KOREAN_DIGITS = r"(?:영|공|일|이|삼|사|오|육|칠|팔|구)+"
_SINO_KOREAN_WITH_MAGNITUDE = (
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*"
    r"(?:십|백|천|만|억|조)"
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*"
)
# 붙여 쓴 단위 앞에서는 최소 한 자리값(십/백/천/만/억)이 있거나, 조 앞에
# 숫자말이 있어야 한다. 단독 ``조원``은 금액일 수도 있지만 '프로젝트 조원'과
# 구별할 수 없으므로 공백 없는 형태만으로는 수치라고 단정하지 않는다.
_SINO_KOREAN_SAFE_ATTACHED_MAGNITUDE = (
    r"(?:"
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*"
    r"(?:십|백|천|만|억)"
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*"
    r"|(?:영|공|일|이|삼|사|오|육|칠|팔|구)+조"
    r"(?:영|공|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|조)*)"
)
_SINO_KOREAN_NUMBER_WITH_UNIT = re.compile(
    # 붙여 쓴 짧은 단위는 일반 단어와 형태가 겹친다(조건=조+건, 사원=사+원).
    # 일반 숫자말은 단위와 실제 공백이 있을 때만 인정한다. 공백 없이 붙은
    # 표현은 자리값을 포함해 수량임이 분명할 때만 인정한다.
    rf"(?<![가-힣])(?:"
    rf"(?:{_SINO_KOREAN_DIGITS}|{_SINO_KOREAN_WITH_MAGNITUDE})\s+"
    rf"(?:퍼센트|프로|배|원|달러|년|개월|분기|자릿수|개|건|명|회|곳|번째)"
    rf"|{_SINO_KOREAN_SAFE_ATTACHED_MAGNITUDE}\s*"
    rf"(?:퍼센트|프로|배|원|달러|년|개월|분기|자릿수))"
    rf"{_KOREAN_NUMBER_END}"
)
_NATIVE_KOREAN_NUMBER = (
    r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|마흔|쉰|예순|일흔|여든|아흔)"
)
_NATIVE_KOREAN_NUMBER_WITH_UNIT = re.compile(
    # 「한 번으로 끝나지 않는다」·「두 번째 이익축」은 양을 주장하는 값이
    # 아니라 관용적 횟수·순서다. 번/번째를 NumericBinding 게이트에 넣으면
    # 정상 기업 설명을 수치 오류로 삭제하므로 제외한다.
    # '세배'·'한배를 탔다'도 일반 단어/관용구이므로 배·자릿수는 공백을
    # 요구한다. 한달·한해처럼 그 자체가 기간인 표현만 붙여 쓰기를 허용한다.
    rf"(?<![가-힣])(?:{_NATIVE_KOREAN_NUMBER}\s*(?:해|달)"
    rf"|{_NATIVE_KOREAN_NUMBER}\s+(?:배|자릿수|개|건|명|곳))"
    rf"{_KOREAN_NUMBER_END}"
)
_QUANTITATIVE_WORD = re.compile(
    r"(?<![가-힣])(?:절반|반토막|두\s*배|몇\s*배|수십\s*퍼센트|수백\s*억|한\s*자릿수|두\s*자릿수)"
)


def has_public_numeric_token(text: str) -> bool:
    """공개 산문에 숫자·날짜·백분율의 바탕인 숫자 토큰이 있는가.

    이것은 문장에서 값을 역추출해 사실 장부를 만드는 함수가 아니다. 오직
    구조화 결속이 필요한 문장인지 보수적으로 분류한다. 따라서 ``2025년``·
    ``24.28%``·``제2공장``·``2025Q1``·``H100``뿐 아니라 ``두 배``·
    ``이십오 퍼센트``처럼 수량 단위가 붙은 한글 수도 참이다. 그 뜻은
    계산 수치는 NumericBinding으로, 보도 수치는 정확 원문과 독립 검수로 대조한다.
    """

    value = str(text or "")
    return any(
        pattern.search(value) is not None
        for pattern in (
            _NUMBER_TOKEN,
            _SINO_KOREAN_NUMBER_WITH_UNIT,
            _NATIVE_KOREAN_NUMBER_WITH_UNIT,
            _QUANTITATIVE_WORD,
        )
    )


def korean_numeric_tokens(text: str) -> set[str]:
    """한글 수량 표현은 같은 정본 문법으로 추출해 원문과 대조한다."""
    return {
        re.sub(r"\s+", "", match.group())
        for pattern in (_SINO_KOREAN_NUMBER_WITH_UNIT, _NATIVE_KOREAN_NUMBER_WITH_UNIT, _QUANTITATIVE_WORD)
        for match in pattern.finditer(str(text or ""))
    }
