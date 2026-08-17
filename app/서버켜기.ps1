# 기업분석 도구 — 서버 켜기
#
# 쓰는 법: 이 파일이 있는 폴더에서 PowerShell을 열고 아래를 입력합니다.
#     .\서버켜기.ps1
#
# ★ 열쇠(클라이언트 ID·보안 비밀)를 «파일에 적어두지 않습니다».
#   실행할 때마다 물어보고, 그 창이 살아 있는 동안만 기억합니다.
#   파일에 적어두면 실수로 남에게 보내거나 깃에 올릴 위험이 생깁니다.

$ErrorActionPreference = "Stop"

# 이 스크립트가 있는 폴더로 이동한다 (어디서 실행하든 똑같이 돌게)
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "=== 기업분석 도구 서버 켜기 ===" -ForegroundColor Cyan
Write-Host ""

# ── 파이썬 찾기 ─────────────────────────────────────────
# `python`이 PATH에 없을 수 있다. 그럴 때를 대비해 후보를 차례로 찾는다.
$python = $null
foreach ($candidate in @("python", "py")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source; break }
}
if (-not $python) {
    $guess = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"
    if (Test-Path $guess) { $python = $guess }
}
if (-not $python) {
    Write-Host "파이썬을 찾지 못했습니다." -ForegroundColor Red
    Write-Host "https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요."
    Write-Host "설치할 때 'Add python.exe to PATH' 를 «반드시» 체크하세요."
    exit 1
}
Write-Host "파이썬: $python" -ForegroundColor DarkGray

# ══════════════════════════════════════════════════════════
# 기억해 두기 — 매번 다시 치지 않게
# ══════════════════════════════════════════════════════════
# ★ 저장하는 곳은 «윈도우 사용자 설정»이다. 프로젝트 폴더가 «아니다».
#   그래서 실수로 깃에 올라가거나 파일째 남에게 보내질 위험이 없다.
#   (예전에는 매번 물어봤는데, 값이 4개라 서버를 껐다 켤 때마다 너무 번거로웠다.)
# ⚠️ 대신 이 컴퓨터를 쓰는 «다른 사람»은 읽을 수 있다. 공용 PC면 저장하지 말 것.
#   지우는 법은 이 파일 맨 아래 「저장한 값 지우기」 주석 참고.

function Get-Saved($name) {
    [Environment]::GetEnvironmentVariable($name, "User")
}

function Save-Value($name, $value) {
    [Environment]::SetEnvironmentVariable($name, $value, "User")
}

function Clear-Saved($names) {
    foreach ($n in $names) { [Environment]::SetEnvironmentVariable($n, $null, "User") }
}

# ★ 「이미 준비됐나」는 오직 이 깃발로만 판단한다. $env: 를 들여다보면 «안 된다» —
#   저장해 둔 값은 새 창이 열릴 때 자동으로 $env: 에 들어와 있어서,
#   「새로 입력」을 골라도 「이미 있네」로 오판하고 질문을 건너뛴다 (문제로그 P-91).
$googleReady = $false
$notionReady = $false

$googleKeys = @("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
$notionKeys = @("NOTION_TOKEN", "NOTION_PARENT_PAGE_ID")

# ── 저장된 게 있으면 먼저 알려준다 ──────────────────────
$savedGoogle = (Get-Saved "GOOGLE_CLIENT_ID") -and (Get-Saved "GOOGLE_CLIENT_SECRET")
$savedNotion = (Get-Saved "NOTION_TOKEN") -and (Get-Saved "NOTION_PARENT_PAGE_ID")

if ($savedGoogle -or $savedNotion) {
    Write-Host ""
    Write-Host "이 컴퓨터에 저장된 설정이 있습니다:" -ForegroundColor Green
    if ($savedGoogle) { Write-Host "  · 구글 로그인 (클라이언트 ID·보안 비밀)" -ForegroundColor Green }
    if ($savedNotion) { Write-Host "  · 노션 보내기 (토큰·페이지)" -ForegroundColor Green }
    Write-Host ""
    Write-Host "  y = 그대로 쓴다 (아무것도 안 물어봅니다)"
    Write-Host "  n = 고칠 것만 고른다"
    Write-Host "  d = 저장된 것을 «전부 지운다»"
    $useSaved = Read-Host "y / n / d"

    if ($useSaved -eq "d") {
        Clear-Saved ($googleKeys + $notionKeys)
        Write-Host "저장된 설정을 지웠습니다. 아래에서 새로 입력하세요." -ForegroundColor Yellow
        $savedGoogle = $false
        $savedNotion = $false
        $useSaved = "n"
    }
    elseif ($useSaved -eq "n") {
        # ★ 「n = 전부 다시」로 만들면 안 된다 — 구글 열쇠 하나 고치려는데
        #   노션 토큰까지 다시 찾아와야 한다. 실제로 그 상황이 생겼다.
        #   고칠 것만 고르게 하고, 나머지는 저장된 것을 그대로 쓴다.
        if ($savedGoogle) {
            Write-Host ""
            Write-Host "구글 로그인 — 저장된 값을 그대로 쓸까요? (y = 그대로 / n = 새로 입력)" -ForegroundColor Yellow
            if ((Read-Host "y 또는 n") -ne "n") {
                $env:GOOGLE_CLIENT_ID = Get-Saved "GOOGLE_CLIENT_ID"
                $env:GOOGLE_CLIENT_SECRET = Get-Saved "GOOGLE_CLIENT_SECRET"
                $env:GOOGLE_REDIRECT_URI = "http://localhost:8000/auth/callback"
                $env:AUTH_COOKIE_INSECURE = "1"
                $googleReady = $true
                Write-Host "구글 로그인 — 저장된 값 사용" -ForegroundColor Green
            } else {
                # ★ 물려받은 «옛» 값을 반드시 지운다 (문제로그 P-91).
                #   저장해 둔 값은 새 창이 열릴 때 자동으로 $env: 에 들어와 있다.
                #   안 지우면 「새로 입력」을 골라도 옛 열쇠가 그대로 서버로 넘어간다.
                Remove-Item Env:\GOOGLE_CLIENT_ID -ErrorAction SilentlyContinue
                Remove-Item Env:\GOOGLE_CLIENT_SECRET -ErrorAction SilentlyContinue
                $savedGoogle = $false      # 아래에서 새로 물어보게 한다
            }
        }
        if ($savedNotion) {
            Write-Host ""
            Write-Host "노션 보내기 — 저장된 값을 그대로 쓸까요? (y = 그대로 / n = 새로 입력)" -ForegroundColor Yellow
            if ((Read-Host "y 또는 n") -ne "n") {
                $env:NOTION_TOKEN = Get-Saved "NOTION_TOKEN"
                $env:NOTION_PARENT_PAGE_ID = Get-Saved "NOTION_PARENT_PAGE_ID"
                $notionReady = $true
                Write-Host "노션 보내기 — 저장된 값 사용" -ForegroundColor Green
            } else {
                Remove-Item Env:\NOTION_TOKEN -ErrorAction SilentlyContinue
                Remove-Item Env:\NOTION_PARENT_PAGE_ID -ErrorAction SilentlyContinue
                $savedNotion = $false
            }
        }
    }
} else {
    $useSaved = "n"
}

# ── 저장된 것을 그대로 쓰는 경우 ────────────────────────
# ★ googleReady = 「구글 열쇠가 이미 환경변수에 들어갔다」
#   이 깃발 하나로 «다시 묻지 않는다»를 판단한다. 위에서 저장된 값을 넣었든,
#   아래에서 새로 받았든 결과는 같아야 한다.
if ($useSaved -eq "y" -and $savedGoogle) {
    $env:GOOGLE_CLIENT_ID = Get-Saved "GOOGLE_CLIENT_ID"
    $env:GOOGLE_CLIENT_SECRET = Get-Saved "GOOGLE_CLIENT_SECRET"
    $env:GOOGLE_REDIRECT_URI = "http://localhost:8000/auth/callback"
    # ★ 로컬(http)에서만 켠다. 인터넷에 올릴 때는 «반드시» 빼야 한다.
    $env:AUTH_COOKIE_INSECURE = "1"
    $googleReady = $true
    Write-Host "구글 로그인 — 저장된 값 사용" -ForegroundColor Green
}
if ($useSaved -eq "y" -and $savedNotion) {
    $env:NOTION_TOKEN = Get-Saved "NOTION_TOKEN"
    $env:NOTION_PARENT_PAGE_ID = Get-Saved "NOTION_PARENT_PAGE_ID"
    # ★ 이 한 줄이 빠져 있었다 (문제로그 P-104). 값은 들어갔는데 «준비됐다»고
    #   표시를 안 해서, 「저장된 값 사용」이라 말해 놓고 바로 다음에 또 물어봤다.
    #   구글 쪽에는 넣고 노션 쪽에만 빠뜨렸다 — 같은 일을 두 곳에 적은 대가다.
    $notionReady = $true
    Write-Host "노션 보내기 — 저장된 값 사용" -ForegroundColor Green
}

# ── 로그인을 쓸지 물어본다 ──────────────────────────────
if (-not $googleReady) {
    Write-Host ""
    Write-Host "구글 로그인을 켤까요?" -ForegroundColor Yellow
    Write-Host "  y = 켠다 (클라이언트 ID·보안 비밀이 필요합니다)"
    Write-Host "  n = 안 켠다 (조사 기능만 씁니다. 관리 화면·대시보드는 못 봅니다)"

    if ((Read-Host "y 또는 n") -eq "y") {
        Write-Host ""
        Write-Host "구글 클라우드 콘솔에서 받은 값을 붙여넣으세요." -ForegroundColor Yellow
        Write-Host "  ★ 클라이언트 ID와 보안 비밀은 «같은 클라이언트» 것이어야 합니다." -ForegroundColor Yellow
        Write-Host "    섞이면 로그인이 조용히 실패합니다 (invalid_client)." -ForegroundColor DarkGray
        Write-Host "  (붙여넣기: 마우스 오른쪽 클릭)"
        Write-Host ""
        $clientId = Read-Host "클라이언트 ID (…apps.googleusercontent.com 으로 끝남)"
        # 보안 비밀은 화면에 «안 보이게» 받는다 — 어깨너머로 보이거나 캡처에 찍히지 않게
        $secretSecure = Read-Host "클라이언트 보안 비밀 (GOCSPX-… · 화면에 안 보입니다)" -AsSecureString
        $clientSecret = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretSecure)
        )

        if ([string]::IsNullOrWhiteSpace($clientId) -or [string]::IsNullOrWhiteSpace($clientSecret)) {
            Write-Host "두 값이 모두 필요합니다. 다시 실행해 주세요." -ForegroundColor Red
            exit 1
        }

        $env:GOOGLE_CLIENT_ID = $clientId.Trim()
        $env:GOOGLE_CLIENT_SECRET = $clientSecret.Trim()
        $env:GOOGLE_REDIRECT_URI = "http://localhost:8000/auth/callback"
        # ★ 로컬(http)에서만 켠다. 인터넷에 올릴 때는 «반드시» 빼야 한다 —
        #   이걸 켠 채 배포하면 로그인 기록이 암호화되지 않은 길로 오간다.
        $env:AUTH_COOKIE_INSECURE = "1"
        $googleReady = $true

        Write-Host ""
        Write-Host "이 컴퓨터에 기억시킬까요? (다음부터 안 물어봅니다)" -ForegroundColor Yellow
        Write-Host "  y = 기억한다   n = 이번만 쓴다"
        Write-Host "  ⚠️ 이 컴퓨터를 쓰는 다른 사람도 읽을 수 있습니다. 공용 PC면 n." -ForegroundColor DarkGray
        if ((Read-Host "y 또는 n") -eq "y") {
            Save-Value "GOOGLE_CLIENT_ID" $env:GOOGLE_CLIENT_ID
            Save-Value "GOOGLE_CLIENT_SECRET" $env:GOOGLE_CLIENT_SECRET
            Write-Host "기억했습니다." -ForegroundColor Green
        }
        Write-Host ""
        Write-Host "로그인 설정 완료." -ForegroundColor Green
    }
}
# 아래에서 「노션은 관리자 전용」 경고를 띄울지 판단할 때 쓴다.
$useLogin = $(if ($googleReady) { "y" } else { "n" })


# ── 노션 보내기를 쓸지 물어본다 ─────────────────────────
# 안 쓰면 그냥 건너뛴다. 보고서 화면의 [노션으로 보내기]만 안 될 뿐 나머지는 다 된다.
#
# ★ 노션 보내기는 «관리자 전용»이다 (기획서 D10·P4 — `main.py`의 require_admin).
#   그래서 구글 로그인을 안 켜면 토큰이 멀쩡해도 **버튼 자체가 안 보인다.**
#   여기서 미리 말해주지 않으면, 토큰을 다 넣고 「완료」를 본 뒤에야
#   버튼이 없다는 걸 알게 된다 — 실제로 그렇게 헛걸음했다.
# ★ 구글과 «같은 방식» — 깃발 하나만 본다. $env: 는 «안» 본다 (P-91).
if (-not $notionReady) {
Write-Host ""
Write-Host "노션으로 보내기를 켤까요?" -ForegroundColor Yellow
Write-Host "  y = 켠다 (통합 토큰 + 부모 페이지 주소가 필요합니다)"
Write-Host "  n = 안 켠다 (워드로 내려받기는 그대로 됩니다)"
if ($useLogin -ne "y") {
    Write-Host ""
    Write-Host "  ⚠️ 잠깐 — 노션 보내기는 «관리자 전용»입니다." -ForegroundColor Red
    Write-Host "     방금 구글 로그인을 안 켜셨기 때문에, 토큰을 넣어도" -ForegroundColor Red
    Write-Host "     보고서 화면에 [노션으로 보내기] 버튼이 «안 보입니다»." -ForegroundColor Red
    Write-Host "     쓰시려면 Ctrl+C로 끄고 다시 켜서 구글 로그인을 y로 답하세요." -ForegroundColor Yellow
    Write-Host ""
}
$useNotion = Read-Host "y 또는 n"

if ($useNotion -eq "y") {
    Write-Host ""
    Write-Host "notion.so/my-integrations 에서 만든 값을 붙여넣으세요." -ForegroundColor Yellow
    Write-Host "  자세한 순서: docs\노션_설정.md" -ForegroundColor DarkGray
    Write-Host ""
    # 토큰은 화면에 «안 보이게» 받는다 — 어깨너머로 보이거나 캡처에 찍히지 않게
    $notionSecure = Read-Host "노션 토큰 (ntn_… 또는 secret_… · 화면에 안 보입니다)" -AsSecureString
    $notionToken = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($notionSecure)
    )
    Write-Host ""
    Write-Host "보고서를 넣어둘 노션 페이지 «주소»를 그대로 붙여넣으세요." -ForegroundColor Yellow
    Write-Host "  (노션에서 그 페이지 열고 → 오른쪽 위 ··· → 「링크 복사」)"
    $notionPageUrl = Read-Host "페이지 주소"

    # ★ 주소에서 페이지 ID(32자리)만 뽑아낸다.
    #   사용자에게 「32자리만 골라서 넣으세요」라고 시키면 거의 틀린다 —
    #   주소를 통째로 받고 프로그램이 골라내는 쪽이 실수가 없다.
    $pageId = ""
    if ($notionPageUrl -match "([0-9a-fA-F]{32})") {
        $pageId = $Matches[1]
    } elseif ($notionPageUrl -match "([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})") {
        $pageId = $Matches[1] -replace "-", ""
    }

    if ([string]::IsNullOrWhiteSpace($notionToken) -or [string]::IsNullOrWhiteSpace($pageId)) {
        Write-Host ""
        Write-Host "토큰이나 페이지 주소를 못 읽었습니다 — 노션 보내기는 끕니다." -ForegroundColor Red
        Write-Host "주소는 https://www.notion.so/... 형태여야 하고 32자리 글자가 들어 있어야 합니다."
        Write-Host "나머지 기능은 그대로 씁니다."
    } else {
        $env:NOTION_TOKEN = $notionToken.Trim()
        $env:NOTION_PARENT_PAGE_ID = $pageId
        $notionReady = $true
        Write-Host ""
        Write-Host "노션 설정 완료 (페이지 $pageId)." -ForegroundColor Green
        Write-Host "★ 그 페이지에서 ··· → 「연결」 → 만든 통합을 추가하셨는지 확인하세요." -ForegroundColor Yellow
        Write-Host "  이걸 빠뜨리면 토큰이 맞아도 404가 납니다 — 가장 흔한 실수입니다."

        Write-Host ""
        Write-Host "이 컴퓨터에 기억시킬까요? (다음부터 안 물어봅니다)" -ForegroundColor Yellow
        Write-Host "  y = 기억한다   n = 이번만 쓴다"
        if ((Read-Host "y 또는 n") -eq "y") {
            Save-Value "NOTION_TOKEN" $env:NOTION_TOKEN
            Save-Value "NOTION_PARENT_PAGE_ID" $env:NOTION_PARENT_PAGE_ID
            Write-Host "기억했습니다." -ForegroundColor Green
        }

        if ($useLogin -ne "y") {
            Write-Host ""
            Write-Host "⚠️ 다만 구글 로그인을 안 켜셔서 [노션으로 보내기] 버튼은 안 보입니다." -ForegroundColor Red
            Write-Host "   (노션 보내기는 관리자 전용입니다)" -ForegroundColor Red
        }
    }
}
}

# ── 진짜 조사를 쓸지 물어본다 ───────────────────────────
Write-Host ""
Write-Host "어떤 방식으로 돌릴까요?" -ForegroundColor Yellow
Write-Host "  1 = 데모 (저장된 결과 재생. 돈 안 듦)"
Write-Host "  2 = 진짜 조사 (실제 공시·뉴스 조사. ★ 1건당 대체로 60~250원)"
Write-Host "      실제로 재 본 3곳: 82원 · 88원 · 182원 (회사마다 자료 양이 달라 벌어집니다)" -ForegroundColor DarkGray
$mode = Read-Host "1 또는 2"
if ($mode -eq "2") {
    $env:PIPELINE = "real"
    Write-Host "★ 진짜 조사 모드 — 조사할 때마다 비용이 발생합니다." -ForegroundColor Red
} else {
    Remove-Item Env:\PIPELINE -ErrorAction SilentlyContinue
    Write-Host "데모 모드 — 비용이 들지 않습니다." -ForegroundColor Green
}

# ── 켠다 ────────────────────────────────────────────────
Write-Host ""
Write-Host "서버를 켭니다. 브라우저에서 http://localhost:8000 을 여세요." -ForegroundColor Cyan
Write-Host "끄려면 이 창에서 Ctrl+C 를 누르세요."
Write-Host ""
Write-Host "★ 코드가 바뀌면 서버가 «알아서» 다시 읽습니다 — 껐다 켤 필요가 없습니다." -ForegroundColor Green
Write-Host "  (화면·기능을 고친 뒤 브라우저 새로고침만 하시면 됩니다)" -ForegroundColor DarkGray
Write-Host ""

# ★ `--reload` = 파일이 바뀌면 서버가 스스로 다시 읽는다 (문제로그 P-97).
#   이게 없어서 코드를 고칠 때마다 사람이 Ctrl+C → 재실행을 반복했다.
#   ⚠️ 인터넷에 배포할 때는 «반드시 빼야 한다» — 파일을 감시하느라 느려지고,
#     운영 중에 파일이 바뀌면 서버가 멋대로 재시작한다.
#   ⚠️ 다시 읽을 때 «메모리에 있던 값»(진행 중인 조사·오늘 쓴 돈)은 초기화된다.
#     오늘 쓴 돈은 이력에서 다시 읽어오므로(_seed_ledger) 상한은 그대로 지켜진다.
& $python -m uvicorn src.web.main:app --port 8000 --reload --reload-dir src

# ══════════════════════════════════════════════════════════
# 저장한 값 지우기
# ══════════════════════════════════════════════════════════
# 이 스크립트를 다시 실행하고 첫 질문에 «d»를 누르면 한 번에 지워진다.
#
# 손으로 지우려면 PowerShell에 아래를 붙여넣는다:
#   [Environment]::SetEnvironmentVariable("GOOGLE_CLIENT_ID", $null, "User")
#   [Environment]::SetEnvironmentVariable("GOOGLE_CLIENT_SECRET", $null, "User")
#   [Environment]::SetEnvironmentVariable("NOTION_TOKEN", $null, "User")
#   [Environment]::SetEnvironmentVariable("NOTION_PARENT_PAGE_ID", $null, "User")
#
# 저장된 곳: 윈도우 「사용자 환경 변수」 (시작 → "환경 변수" 검색).
# ★ 프로젝트 폴더에는 아무것도 안 남는다 — 깃에 올라갈 위험이 없다.
