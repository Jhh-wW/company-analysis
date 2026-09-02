"""출시 배포값을 «리터럴 문자열»로 못 박는 시험.

★ 왜 상수를 import 해서 비교하지 않는가 —
  다른 시험들은 ``validator.RUNTIME_CONTRACT_RENDER_PORTFOLIO_LINK`` 같은 생산
  상수를 기대값으로 쓴다. 그러면 상수 쪽 글자가 잘못 바뀌어도 render.yaml이
  «같이» 바뀌기만 하면 초록이 된다 — 서로를 보고 베끼는 순환 검증이다.
  이 파일만은 사람이 눈으로 읽은 글자를 직접 박아, 양쪽이 함께 틀어지는 경우를
  잡는다. 값을 바꾸려면 이 리터럴을 손으로 고쳐야 하고, 그때 결정 근거를 남기게
  된다.

근거 결정(2026-09-02):
  - D-A   : 메인 병합 릴리스에서 SHADOW 종료 → ``REPORT_RELEASE_MODE=FULL``
  - D-G2  : 링크·초대·QR 입구를 여는 배포 계약으로 전환
  - D-G7  : 그 계약의 «이름»은 새 이름 ``render-portfolio-link-v1``
  - F-B3c : typed DART 수집기는 실제 문서로 검증된 적이 없어 출시에서 끄고 간다
            (환경변수를 아예 «선언하지 않는» 것이 off다)
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RENDER_BLUEPRINT = REPOSITORY_ROOT / "render.yaml"

#: 사람이 ``app/src/web/deployment_mode.py``에서 눈으로 읽고 옮겨 적은 글자.
#: 09장 §4 — 생산 상수를 import 해서 채우지 않는다.
EXPECTED_RUNTIME_CONTRACT = "render-portfolio-link-v1"
#: ``app/src/shared/report_evidence/constants.py``의 세 모드 중 출시 값.
EXPECTED_RELEASE_MODE = "FULL"
#: 출시에서 선언하지 않는 kill switch 이름(F-B3c).
TYPED_COLLECTOR_ENV_NAME = "TYPED_DART_COLLECTOR"


def _render_web_service_env() -> dict[str, object]:
    """render.yaml의 web 서비스 환경변수를 {키: 값}으로 읽는다.

    ``fromService``·``sync: false``처럼 값이 없는 항목은 ``None``이 되므로,
    값 비교 전에 문자열인지도 함께 확인한다.
    """

    blueprint = yaml.safe_load(RENDER_BLUEPRINT.read_text(encoding="utf-8"))
    web_service = next(
        service for service in blueprint["services"] if service["type"] == "web"
    )
    return {item["key"]: item.get("value") for item in web_service["envVars"]}


def test_render_yaml의_계약_이름은_render_portfolio_link_v1_리터럴과_같다() -> None:
    """D-G7 — 배포 계약 이름이 한 글자라도 다르면 손님 입구가 안 열린다.

    이름이 어긋나면 컨테이너는 시작 검증에서 거부되거나(허용 목록 밖),
    옛 관리자 계약으로 읽혀 링크 발급 404·초대 409가 «조용히» 되살아난다.
    """

    render_values = _render_web_service_env()

    assert "DEPLOYMENT_RUNTIME_CONTRACT" in render_values, (
        "render.yaml에 DEPLOYMENT_RUNTIME_CONTRACT가 없으면 컨테이너가 뜨지 않습니다"
    )
    assert render_values["DEPLOYMENT_RUNTIME_CONTRACT"] == (
        EXPECTED_RUNTIME_CONTRACT
    ), (
        "배포 계약 이름이 출시 값과 다릅니다 — 링크·초대·QR 입구가 닫힌 채 배포됩니다"
    )


def test_render_yaml의_release_mode는_FULL_리터럴과_같다() -> None:
    """D-A — 연습 모드(SHADOW)로 남으면 품질 미달 보고서가 차감과 함께 나간다."""

    render_values = _render_web_service_env()

    assert "REPORT_RELEASE_MODE" in render_values, (
        "ENGINE_V2=1인데 이 값이 없으면 앱이 부팅을 거부합니다"
    )
    assert isinstance(render_values["REPORT_RELEASE_MODE"], str), (
        "출고 모드는 문자열이어야 합니다"
    )
    assert render_values["REPORT_RELEASE_MODE"] == EXPECTED_RELEASE_MODE, (
        "출고 모드가 FULL이 아니면 품질 하한을 관찰만 하고 그대로 내보냅니다"
    )


def test_TYPED_DART_COLLECTOR는_render_yaml에_없다() -> None:
    """F-B3c — 실제 DART 문서로 검증된 적 없는 수집기를 출시와 함께 켜지 않는다.

    이 스위치는 «선언하지 않는 것»이 off다. 값을 "0"으로 적어 두는 것도 금지가
    아니지만, 키가 있으면 나중에 누가 "1"로 바꾸기가 너무 쉬워진다.
    """

    render_values = _render_web_service_env()

    assert TYPED_COLLECTOR_ENV_NAME not in render_values, (
        "출시 릴리스는 typed 수집기를 끈 채로 나갑니다 — 출시 뒤 감독 실행 1건으로 결정합니다"
    )


def _module_constant(module_path: Path, name: str) -> str:
    """모듈을 «실행하지 않고» 최상위 문자열 상수 하나를 읽는다.

    ★ 왜 import 하지 않는가 — ``exec_module``은 ``__pycache__``의 옛
      바이트코드를 재사용한다. 바이트코드 무효화는 (수정시각, 크기)로
      판단하므로 «같은 길이»로 값을 고치면 옛 값이 그대로 읽힌다(실측).
      그러면 이 시험은 디스크의 진짜 글자가 아니라 지난번 글자를 단정한다.
      구문 분석은 소스만 보므로 그 구멍이 없다.
    """

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != name:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        raise AssertionError(f"{name}이 문자열 리터럴이 아닙니다")
    raise AssertionError(f"{module_path.name}에 {name} 상수가 없습니다")


def test_deployment_mode_상수도_같은_리터럴이다() -> None:
    """render.yaml과 코드 상수가 «서로 어긋나는» 경우를 잡는다.

    두 시험은 방향이 다르다. 위 시험은 yaml을 리터럴에 비교하고, 이 시험은
    상수를 같은 리터럴에 비교한다. 둘 다 리터럴을 기준으로 하므로 한쪽이
    바뀌면 반드시 빨간불이 난다(서로를 기준으로 삼지 않는다).
    """

    module_path = REPOSITORY_ROOT / "app" / "src" / "web" / "deployment_mode.py"

    assert _module_constant(
        module_path, "RENDER_PORTFOLIO_LINK_CONTRACT"
    ) == EXPECTED_RUNTIME_CONTRACT, (
        "코드 상수와 render.yaml이 다른 글자를 가리키면 배포가 조용히 옛 계약이 됩니다"
    )
