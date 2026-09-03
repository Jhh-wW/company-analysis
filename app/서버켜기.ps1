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
# ★ 저장소의 가상환경을 «먼저» 본다. PATH의 `python`은 이 프로그램이 쓰는 라이브러리가
#   깔려 있지 않은 다른 파이썬일 수 있고, 그러면 서버가 켜지다가 import 오류로 죽는다.
$appRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $appRoot ".."))
$python = $null
foreach ($candidate in @(
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    (Join-Path $appRoot ".venv\Scripts\python.exe")
)) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $python = $candidate; break }
}
if (-not $python) {
    foreach ($candidate in @("python", "py")) {
        $found = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue
        if ($found) { $python = $found.Source; break }
    }
}
if (-not $python) {
    Write-Host "파이썬을 찾지 못했습니다." -ForegroundColor Red
    Write-Host "저장소 폴더에 가상환경(.venv)을 만들고 requirements.txt를 설치한 뒤 다시 실행하세요."
    Write-Host "파이썬이 아예 없다면 https://www.python.org/downloads/ 에서 먼저 설치합니다."
    Write-Host "설치할 때 'Add python.exe to PATH' 를 «반드시» 체크하세요."
    exit 1
}
Write-Host "파이썬: $python" -ForegroundColor DarkGray

# ══════════════════════════════════════════════════════════
# 기억해 두기 — 매번 다시 치지 않게
# ══════════════════════════════════════════════════════════
# ★ 저장하는 곳은 «윈도우 사용자 설정»이다. 프로젝트 폴더가 «아니다».
#   그래서 실수로 깃에 올라가거나 파일째 남에게 보내질 위험이 없다.
#   물어보는 값이 여러 개라, 저장해 두지 않으면 서버를 껐다 켤 때마다 전부 다시 쳐야 한다.
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
#   「새로 입력」을 골라도 「이미 있네」로 오판하고 질문을 건너뛴다.
$googleReady = $false
$notionReady = $false

# ★ ADMIN_EMAILS 가 비면 관리자는 «0명»이다 (src/features/auth/constants.py 의
#   기본 관리자 목록이 빈 값이다). 로그인에 성공해도 「관리자가 아닙니다」로 끝나
#   관리 화면·대시보드·노션 보내기에 영영 닿지 못한다. 그래서 구글 열쇠와 «한 묶음»으로 다룬다.
$googleKeys = @("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ADMIN_EMAILS")
$notionKeys = @("NOTION_TOKEN", "NOTION_PARENT_PAGE_ID")

# ── 저장된 게 있으면 먼저 알려준다 ──────────────────────
$savedGoogle = (Get-Saved "GOOGLE_CLIENT_ID") -and (Get-Saved "GOOGLE_CLIENT_SECRET") `
    -and (Get-Saved "ADMIN_EMAILS")
$savedNotion = (Get-Saved "NOTION_TOKEN") -and (Get-Saved "NOTION_PARENT_PAGE_ID")

if ($savedGoogle -or $savedNotion) {
    Write-Host ""
    Write-Host "이 컴퓨터에 저장된 설정이 있습니다:" -ForegroundColor Green
    if ($savedGoogle) { Write-Host "  · 구글 로그인 (클라이언트 ID·보안 비밀·관리자 이메일)" -ForegroundColor Green }
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
                $env:ADMIN_EMAILS = Get-Saved "ADMIN_EMAILS"
                $env:GOOGLE_REDIRECT_URI = "http://localhost:8000/auth/callback"
                $env:AUTH_COOKIE_INSECURE = "1"
                $googleReady = $true
                Write-Host "구글 로그인 — 저장된 값 사용" -ForegroundColor Green
            } else {
                # ★ 물려받은 «옛» 값을 반드시 지운다.
                #   저장해 둔 값은 새 창이 열릴 때 자동으로 $env: 에 들어와 있다.
                #   안 지우면 「새로 입력」을 골라도 옛 열쇠가 그대로 서버로 넘어간다.
                Remove-Item Env:\GOOGLE_CLIENT_ID -ErrorAction SilentlyContinue
                Remove-Item Env:\GOOGLE_CLIENT_SECRET -ErrorAction SilentlyContinue
                Remove-Item Env:\ADMIN_EMAILS -ErrorAction SilentlyContinue
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
    $env:ADMIN_EMAILS = Get-Saved "ADMIN_EMAILS"
    $env:GOOGLE_REDIRECT_URI = "http://localhost:8000/auth/callback"
    # ★ 로컬(http)에서만 켠다. 인터넷에 올릴 때는 «반드시» 빼야 한다.
    $env:AUTH_COOKIE_INSECURE = "1"
    $googleReady = $true
    Write-Host "구글 로그인 — 저장된 값 사용" -ForegroundColor Green
}
if ($useSaved -eq "y" -and $savedNotion) {
    $env:NOTION_TOKEN = Get-Saved "NOTION_TOKEN"
    $env:NOTION_PARENT_PAGE_ID = Get-Saved "NOTION_PARENT_PAGE_ID"
    # ★ 이 깃발을 빼면 값은 들어갔는데 «준비됐다»고 표시가 안 돼,
    #   「저장된 값 사용」이라 말해 놓고 바로 다음에 또 물어보게 된다.
    $notionReady = $true
    Write-Host "노션 보내기 — 저장된 값 사용" -ForegroundColor Green
}

# ── 로그인을 쓸지 물어본다 ──────────────────────────────
if (-not $googleReady) {
    Write-Host ""
    Write-Host "구글 로그인을 켤까요?" -ForegroundColor Yellow
    Write-Host "  y = 켠다 (클라이언트 ID·보안 비밀이 필요합니다)"
    Write-Host "  n = 안 켠다 (로그인 없이 조사 화면만 씁니다)"
    Write-Host "      관리 화면·대시보드·노션 보내기는 못 씁니다." -ForegroundColor DarkGray

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

        # ★ 관리자 이메일이 없으면 로그인에 성공해도 「관리자가 아닙니다」로 끝난다.
        #   구글 열쇠만 받고 이걸 안 받으면 관리 화면에 영영 못 들어간다.
        Write-Host ""
        Write-Host "관리자로 쓸 «본인 구글 계정»을 넣으세요. 여러 명이면 콤마로 구분합니다." -ForegroundColor Yellow
        $adminEmails = Read-Host "관리자 이메일 (예: 본인@gmail.com)"
        $adminEmails = $adminEmails.Trim()
        if ([string]::IsNullOrWhiteSpace($adminEmails) -or ($adminEmails -notmatch "@")) {
            Write-Host "이메일 주소가 필요합니다. 다시 실행해 주세요." -ForegroundColor Red
            exit 1
        }

        $env:GOOGLE_CLIENT_ID = $clientId.Trim()
        $env:GOOGLE_CLIENT_SECRET = $clientSecret.Trim()
        $env:ADMIN_EMAILS = $adminEmails
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
            Save-Value "ADMIN_EMAILS" $env:ADMIN_EMAILS
            Write-Host "기억했습니다." -ForegroundColor Green
        }
        Write-Host ""
        Write-Host "로그인 설정 완료." -ForegroundColor Green
    }
}
# 아래에서 「노션은 관리자 전용」 경고를 띄울지 판단할 때 쓴다.
$useLogin = $(if ($googleReady) { "y" } else { "n" })

# ── 로그인 벽을 «명시»한다 ──────────────────────────────
# 앱은 BETA_ADMIN_ONLY 가 정확히 "0"일 때만 로그인 벽을 끈다
# (src/features/auth/logic.py 의 beta_admin_only_from_env). 값을 안 정하면 벽이
# 켜진 채로 남아, 구글 로그인을 안 켠 사람은 첫 화면부터 아무것도 열지 못한다.
if ($googleReady) {
    $env:BETA_ADMIN_ONLY = "1"
    Write-Host "로그인 벽: 켬 — 관리자 이메일로 넣은 계정만 들어갑니다." -ForegroundColor DarkGray
} else {
    # 이 실행기는 내 컴퓨터(127.0.0.1)에서만 듣는 서버를 켠다. 로그인을 안 켠다면
    # 벽도 같이 꺼야 「조사 화면만 씁니다」가 사실이 된다.
    $env:BETA_ADMIN_ONLY = "0"
    Remove-Item Env:\ADMIN_EMAILS -ErrorAction SilentlyContinue
    Write-Host "로그인 벽: 끔 — 로그인 없이 조사 화면만 씁니다." -ForegroundColor DarkGray
}


# ── 노션 보내기를 쓸지 물어본다 ─────────────────────────
# 안 쓰면 그냥 건너뛴다. 보고서 화면의 [노션으로 보내기]만 안 될 뿐 나머지는 다 된다.
#
# ★ 노션 보내기는 «관리자 전용»이다 — POST /notion 이 관리자 확인을 먼저 한다
#   (src/web/routers/reports.py 의 send_to_notion).
#   그래서 구글 로그인을 안 켜면 토큰이 멀쩡해도 **버튼 자체가 안 보인다.**
#   여기서 미리 말해주지 않으면, 토큰을 다 넣고 「완료」를 본 뒤에야
#   버튼이 없다는 걸 알게 된다 — 실제로 그렇게 헛걸음했다.
# ★ 구글과 «같은 방식» — 깃발 하나만 본다. $env: 는 «안» 본다.
if (-not $notionReady) {
Write-Host ""
Write-Host "노션으로 보내기를 켤까요?" -ForegroundColor Yellow
Write-Host "  y = 켠다 (통합 토큰 + 부모 페이지 주소가 필요합니다)"
Write-Host "  n = 안 켠다 (보고서는 화면에서 보고 PDF로 내려받습니다)"
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
Write-Host "  2 = 진짜 조사 (전자공시·회사 공식 홈페이지 자료로 새로 만듭니다)"
Write-Host "      ★ AI 호출마다 요금이 나갑니다. 회사·자료 양에 따라 금액이 달라집니다." -ForegroundColor DarkGray
$mode = Read-Host "1 또는 2"
if ($mode -eq "2") {
    # ★ PIPELINE=real 은 32바이트 이상의 출처 도장 비밀이 없으면 서버가 «아예 뜨지 않는다»
    #   (src/web/runtime.py 의 시작 검사). 값 없이 그냥 켜면 원인을 알 수 없는 시작 실패로
    #   끝나므로, 여기서 먼저 확인하고 거절한다.
    $sealSecret = [string]$env:PROVENANCE_SEAL_SECRET
    $sealBytes = 0
    if (-not [string]::IsNullOrWhiteSpace($sealSecret)) {
        $sealBytes = [System.Text.Encoding]::UTF8.GetByteCount($sealSecret)
    }
    if ($sealBytes -lt 32) {
        Write-Host ""
        Write-Host "진짜 조사로는 켤 수 없습니다 — 아래 값이 먼저 있어야 합니다." -ForegroundColor Red
        Write-Host "  · PROVENANCE_SEAL_SECRET : 32바이트 이상의 아무 문자열. 없으면 서버가 시작되지 않습니다."
        Write-Host "  · DART_API_KEY · ANTHROPIC_API_KEY : 공시 조회와 AI 호출에 필요합니다."
        Write-Host ""
        Write-Host "이번 창에서만 쓰려면 아래를 붙여넣고 이 실행기를 다시 켜세요." -ForegroundColor Yellow
        Write-Host '  $env:PROVENANCE_SEAL_SECRET = "아무도 모르는 32자 이상의 문자열"'
        Write-Host ""
        Write-Host "데모로 보시려면 다시 실행해서 1을 고르세요."
        exit 1
    }
    $env:PIPELINE = "real"
    Write-Host "★ 진짜 조사 모드 — 조사할 때마다 요금이 발생합니다." -ForegroundColor Red
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

# ★ `--reload` = 파일이 바뀌면 서버가 스스로 다시 읽는다.
#   이게 없으면 코드를 고칠 때마다 사람이 Ctrl+C → 재실행을 반복해야 한다.
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
#   [Environment]::SetEnvironmentVariable("ADMIN_EMAILS", $null, "User")
#   [Environment]::SetEnvironmentVariable("NOTION_TOKEN", $null, "User")
#   [Environment]::SetEnvironmentVariable("NOTION_PARENT_PAGE_ID", $null, "User")
#
# 저장된 곳: 윈도우 「사용자 환경 변수」 (시작 → "환경 변수" 검색).
# ★ 프로젝트 폴더에는 아무것도 안 남는다 — 깃에 올라갈 위험이 없다.
