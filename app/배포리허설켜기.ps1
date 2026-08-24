[CmdletBinding()]
param(
    # 관리자 이메일은 «파일에 적지 않는다». 실행할 때 인자로만 받아 자식에게 넘긴다.
    # 콤마로 여러 명을 넣을 수 있다 (예: -AdminEmails "a@x.com,b@y.com").
    [Parameter(Mandatory = $true)]
    [string]$AdminEmails,

    [ValidateRange(1024, 65535)]
    [int]$Port = 8030,

    # 비워 두면 http://127.0.0.1:<포트>/auth/callback 을 쓴다. 이 실행기는 로컬 주소만
    # 받는다 — 배포용 https 주소를 실수로 넣어 운영 콜백으로 새는 것을 막는다.
    [string]$GoogleRedirectUri = "",

    # provider 키와 구글 OAuth 값을 담은 파일. 지정하지 않으면 부모 환경변수를 쓰고,
    # 그래도 없는 이름만 실행 중에 물어본다. 값은 화면·파일 어디에도 남기지 않는다.
    [string]$ProviderEnvFile = "",

    # ★ 실제 과금이 일어난다는 것을 사람이 명시적으로 확인해야만 시작한다.
    [switch]$ConfirmRealSpending,

    # 엔진 v2를 끄고 v1 경로로 리허설한다. 기본은 v2 켬.
    [switch]$DisableEngineV2,

    [switch]$DeleteDataOnExit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ══════════════════════════════════════════════════════════════════
# 이 실행기가 재현하는 것 — 「배포될 것과 똑같은 조건」
# ══════════════════════════════════════════════════════════════════
# render.yaml과 같게 두는 값: PIPELINE=real, BETA_ADMIN_ONLY=1,
#   ADMIN_EMAILS·GOOGLE_CLIENT_ID·GOOGLE_CLIENT_SECRET·GOOGLE_REDIRECT_URI(사람 입력),
#   ANTHROPIC/DART/NAVER 키(사람 입력), PROVENANCE_SEAL_SECRET(실행마다 생성),
#   uvicorn 실행 옵션(--workers 1 --no-proxy-headers --limit-concurrency 20 …).
#
# ★ 일부러 다르게 두는 값 두 가지 — 로컬에서는 이렇게 해야 «동작»한다.
#   1) 접속 주소: 배포는 공개 https, 여기는 http://127.0.0.1. 그래서
#      AUTH_COOKIE_INSECURE=1을 켠다. 앱은 이 값만으로 쿠키를 약하게 만들지 않고
#      요청·서버·클라이언트 소켓이 모두 loopback일 때만 예외를 준다
#      (src/web/request_helpers.py:574-605).
#   2) DEPLOYMENT_RUNTIME_CONTRACT: 배포의 render-admin-real-no-forwarded-v1을
#      로컬에 그대로 쓰면 «모든 POST가 거부»된다. 그 계약에서는 POST의 Origin이
#      고정 «https» PUBLIC_ORIGIN과 정확히 같아야 하는데
#      (src/web/request_helpers.py:727-741, src/web/deployment_mode.py:98-110),
#      로컬 브라우저는 http://127.0.0.1:<포트>를 보내므로 절대 같아질 수 없다.
#      그래서 Dockerfile:18-20이 쓰는 로컬 계약(local-web-v1 / local / local)을 쓴다.
#      로그인 게이트(BETA_ADMIN_ONLY)는 이 계약과 무관하게 그대로 켜져 있다.
# ══════════════════════════════════════════════════════════════════

# OWASP least privilege 원칙에 따라 자식은 Windows/Python 실행에 필요한 OS 값과
# 아래에서 명시한 이름만 받는다. 값은 출력하거나 저장하지 않는다.
$safeChildOsEnvironmentNames = @(
    "SystemRoot", "WINDIR", "SystemDrive", "ComSpec", "PATH", "PATHEXT",
    "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "ALLUSERSPROFILE", "ProgramData", "ProgramFiles",
    "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles",
    "CommonProgramFiles(x86)", "CommonProgramW6432"
)
# 배포에서 사람이 직접 넣는 비밀값. 부모 환경 · -ProviderEnvFile · 실행 중 질문
# 세 경로로만 들어오고, 이 실행기는 어느 경로에서도 값을 화면에 찍지 않는다.
$deploymentSecretEnvironmentNames = @(
    "ANTHROPIC_API_KEY",
    "DART_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET"
)
# 화면에는 «있다/없다»만 적는다.
$secretPromptLabels = @{
    "ANTHROPIC_API_KEY"     = "ANTHROPIC_API_KEY (화면에 안 보입니다)"
    "DART_API_KEY"          = "DART_API_KEY (화면에 안 보입니다)"
    "NAVER_CLIENT_ID"       = "NAVER_CLIENT_ID (화면에 안 보입니다)"
    "NAVER_CLIENT_SECRET"   = "NAVER_CLIENT_SECRET (화면에 안 보입니다)"
    "GOOGLE_CLIENT_ID"      = "GOOGLE_CLIENT_ID (…apps.googleusercontent.com 으로 끝납니다)"
    "GOOGLE_CLIENT_SECRET"  = "GOOGLE_CLIENT_SECRET (GOCSPX-… · 화면에 안 보입니다)"
}
# 구글 클라이언트 ID는 브라우저 주소에도 실려 나가는 «공개» 값이라 가리지 않는다.
$plainTextSecretEnvironmentNames = @("GOOGLE_CLIENT_ID")

# 배포와 같은 실행 옵션. 주소만 0.0.0.0 대신 loopback으로 고정한다.
$uvicornLimitConcurrency = 20
$uvicornBacklog = 32
$uvicornKeepAliveSeconds = 5
$uvicornGracefulShutdownSeconds = 300
$uvicornLogLevel = "info"

function Get-CompatibleChildEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.ProcessStartInfo]$StartInfo
    )

    $environment = $null
    try { $environment = $StartInfo.EnvironmentVariables } catch { $environment = $null }
    if ($null -eq $environment) {
        try { $environment = $StartInfo.Environment } catch { $environment = $null }
    }
    if ($null -eq $environment) {
        throw "자식 프로세스의 격리 환경을 만들 수 없어 안전하게 중단합니다."
    }

    $probeName = "DEPLOYMENT_REHEARSAL_LAUNCHER_ENV_PROBE"
    try {
        $environment[$probeName] = "ready"
        if ($environment[$probeName] -ne "ready") {
            throw "환경 사전 쓰기 검증에 실패했습니다."
        }
        $environment.Remove($probeName)
    }
    catch {
        throw "자식 프로세스의 격리 환경을 검증할 수 없어 안전하게 중단합니다."
    }
    return ,$environment
}

function Reset-ChildEnvironmentToAllowlist {
    param(
        [Parameter(Mandatory = $true)]$Environment,
        [Parameter(Mandatory = $true)][string[]]$AllowedNames
    )

    # ProcessStartInfo의 Windows 환경 사전은 런타임에 따라 실제 key casing을
    # 보존하기도 한다(`windir` vs `WINDIR`). PowerShell hashtable로 먼저 옮겨
    # Windows의 대소문자 비구분 계약대로 allowlist를 적용한다.
    $sourceValues = @{}
    foreach ($existingName in @($Environment.Keys)) {
        $sourceValues[[string]$existingName] = [string]$Environment[$existingName]
    }
    $allowedValues = @{}
    foreach ($name in $AllowedNames) {
        $value = $sourceValues[$name]
        if ($null -ne $value -and [string]$value -ne "") {
            $allowedValues[$name] = [string]$value
        }
    }
    # 일부 Windows 호스트는 같은 의미의 SystemRoot만 제공하고 WINDIR를 생략한다.
    # 부모에서 확인한 값만 서로 보완하며 새 경로나 비밀값을 추측하지 않는다.
    if (-not $allowedValues.ContainsKey("WINDIR") -and $allowedValues.ContainsKey("SystemRoot")) {
        $allowedValues["WINDIR"] = $allowedValues["SystemRoot"]
    }
    if (-not $allowedValues.ContainsKey("SystemRoot") -and $allowedValues.ContainsKey("WINDIR")) {
        $allowedValues["SystemRoot"] = $allowedValues["WINDIR"]
    }
    if (-not $allowedValues.ContainsKey("ComSpec") -and $allowedValues.ContainsKey("SystemRoot")) {
        $derivedComSpec = Join-Path $allowedValues["SystemRoot"] "System32\cmd.exe"
        if (Test-Path -LiteralPath $derivedComSpec -PathType Leaf) {
            $allowedValues["ComSpec"] = $derivedComSpec
        }
    }

    foreach ($name in @("SystemRoot", "WINDIR", "ComSpec", "PATH")) {
        if (-not $allowedValues.ContainsKey($name)) {
            throw "자식 프로세스에 필요한 Windows 환경 '$name'을 찾지 못했습니다."
        }
    }

    $Environment.Clear()
    foreach ($name in $AllowedNames) {
        if ($allowedValues.ContainsKey($name)) {
            $Environment[$name] = $allowedValues[$name]
        }
    }
    foreach ($name in @($Environment.Keys)) {
        if ($AllowedNames -notcontains [string]$name) {
            throw "허용하지 않은 부모 환경이 남아 있어 시작하지 않습니다."
        }
    }
    return ,$Environment
}

function Assert-LoopbackPortAvailable {
    param([int]$RequestedPort)

    $listener = New-Object System.Net.Sockets.TcpListener(
        [System.Net.IPAddress]::Loopback,
        $RequestedPort
    )
    try {
        $listener.Server.ExclusiveAddressUse = $true
        $listener.Start()
    }
    catch [System.Net.Sockets.SocketException] {
        throw "포트 $RequestedPort 는 이미 사용 중입니다. 다른 -Port 값을 사용해 주세요."
    }
    finally {
        $listener.Stop()
    }
}

function New-CryptographicRunSuffix {
    $bytes = New-Object byte[] 12
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([System.BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

function New-RandomHexSecret {
    param([int]$ByteCount = 32)

    $bytes = New-Object byte[] $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([System.BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

function Assert-SafeRehearsalDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$ApplicationRoot
    )
    $resolved = [System.IO.Path]::GetFullPath($Candidate)
    $root = [System.IO.Path]::GetFullPath($ApplicationRoot).TrimEnd('\')
    if (-not $resolved.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "리허설 데이터 폴더가 app 폴더 밖을 가리켜 중단합니다."
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "리허설 데이터 폴더가 링크(reparse point)라 중단합니다."
    }
    return $resolved
}

function Wait-ForLoopbackListener {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [int]$RequestedPort,
        [int]$TimeoutMilliseconds = 30000
    )

    $deadline = [System.DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    while ([System.DateTime]::UtcNow -lt $deadline) {
        if ($Process.HasExited) { return $false }
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $client.Connect("127.0.0.1", $RequestedPort)
            if ($client.Connected) { return $true }
        }
        catch [System.Net.Sockets.SocketException] {
            # import와 startup이 끝날 때까지만 짧게 다시 확인한다.
        }
        finally {
            $client.Dispose()
        }
        Start-Sleep -Milliseconds 50
    }
    return $false
}

function Read-MissingSecretValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][bool]$Masked
    )

    try {
        if ($Masked) {
            $secure = Read-Host $Prompt -AsSecureString
            $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try {
                $value = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($pointer)
            }
            finally {
                [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            }
        }
        else {
            $value = Read-Host $Prompt
        }
    }
    catch {
        throw "$Name 값을 입력받지 못했습니다. -ProviderEnvFile 이나 환경변수로 넣어 주세요."
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Name 값이 비어 있어 시작하지 않습니다."
    }
    return $value.Trim()
}

# ── 1. 사람이 확인해야 하는 것부터 fail-closed로 막는다 ─────────────
if (-not $ConfirmRealSpending) {
    Write-Host ""
    Write-Host "배포 리허설은 «진짜 조사»(PIPELINE=real)로 돌아갑니다." -ForegroundColor Red
    Write-Host "조사 1건마다 실제 요금이 발생합니다. 저장소에 적힌 단계별 예상비용 기준은"
    Write-Host "후보 50원 / 회사확정 100원 / OCR 100원 / 본조사 900원입니다"
    Write-Host "(app\src\features\budget\constants.py:104-109 — 청구 hard cap이 아니라"
    Write-Host " 호출 전 예상예약 차단 기준이며, 실제 단가·사용량에 따라 초과할 수 있습니다)."
    Write-Host ""
    throw "확인했으면 -ConfirmRealSpending 을 붙여 다시 실행해 주세요."
}

# 관리자 이메일 — 이 실행기의 «유일한» 관리자 등록 경로다. 부모 환경의 같은 이름은
# allowlist에서 빠져 있어 자식에게 전달되지 않는다.
$adminEmailPattern = '^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$'
$adminEmailList = @()
foreach ($part in $AdminEmails.Split(",")) {
    $trimmed = $part.Trim()
    if (-not $trimmed) { continue }
    if ($trimmed -notmatch $adminEmailPattern) {
        # 값 자체는 찍지 않는다 — 개인정보다.
        throw "-AdminEmails 안에 이메일 형식이 아닌 항목이 있습니다."
    }
    $adminEmailList += $trimmed.ToLowerInvariant()
}
if ($adminEmailList.Count -lt 1) {
    throw "-AdminEmails 에 관리자 이메일이 최소 1개 필요합니다."
}
$normalizedAdminEmails = ($adminEmailList -join ",")

# 구글 콜백 주소 — 로컬 loopback + 이 포트 + /auth/callback 만 받는다.
$allowedRedirectUris = @(
    "http://127.0.0.1:$Port/auth/callback",
    "http://localhost:$Port/auth/callback"
)
if ([string]::IsNullOrWhiteSpace($GoogleRedirectUri)) {
    $GoogleRedirectUri = $allowedRedirectUris[0]
}
else {
    $GoogleRedirectUri = $GoogleRedirectUri.Trim()
}
if ($allowedRedirectUris -notcontains $GoogleRedirectUri) {
    throw (
        "-GoogleRedirectUri 는 이 실행기의 로컬 주소만 받습니다: " +
        ($allowedRedirectUris -join " 또는 ")
    )
}

$appRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$python = Join-Path $appRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    # 이 저장소는 venv를 저장소 루트에 둔다 (app\.venv가 아님) — 부모 폴더도 확인한다.
    $repoRootPython = Join-Path (Split-Path -Parent $appRoot) ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $repoRootPython -PathType Leaf) {
        $python = $repoRootPython
    }
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $pythonCommand = @(
        Get-Command "python" -CommandType Application -ErrorAction SilentlyContinue
    ) | Select-Object -First 1
    if ($null -eq $pythonCommand) {
        throw "Python을 찾지 못했습니다. Python 3.13 환경과 의존성을 먼저 준비해 주세요."
    }
    $python = [string]$pythonCommand.Source
}

Assert-LoopbackPortAvailable -RequestedPort $Port

# ── 2. 자식 프로세스와 배포와 같은 실행 옵션 ────────────────────────
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $python
$startInfo.WorkingDirectory = $appRoot
$startInfo.UseShellExecute = $false
$startInfo.Arguments = (
    "-m uvicorn src.web.main:app --host 127.0.0.1 --port $Port --workers 1 " +
    "--no-proxy-headers --limit-concurrency $uvicornLimitConcurrency " +
    "--backlog $uvicornBacklog --timeout-keep-alive $uvicornKeepAliveSeconds " +
    "--timeout-graceful-shutdown $uvicornGracefulShutdownSeconds " +
    "--log-level $uvicornLogLevel"
)

$childEnvironment = Get-CompatibleChildEnvironment -StartInfo $startInfo

# ── 3. 비밀값 모으기: 파일 > 부모 환경 > 실행 중 질문 ───────────────
if ($ProviderEnvFile) {
    $resolvedProviderEnvFile = (Resolve-Path -LiteralPath $ProviderEnvFile -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $resolvedProviderEnvFile -PathType Leaf)) {
        throw "provider 환경 파일을 찾을 수 없습니다."
    }
    $providerFileValues = @{}
    foreach ($line in [System.IO.File]::ReadAllLines(
        $resolvedProviderEnvFile,
        [System.Text.Encoding]::UTF8
    )) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $separator = $trimmed.IndexOf("=")
        if ($separator -le 0) { continue }
        $name = $trimmed.Substring(0, $separator).Trim().TrimStart([char]0xFEFF)
        if ($deploymentSecretEnvironmentNames -notcontains $name) { continue }
        if ($providerFileValues.ContainsKey($name)) {
            throw "provider 환경 파일에 같은 키 이름이 중복되어 있습니다: $name"
        }
        $value = $trimmed.Substring($separator + 1).Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $providerFileValues[$name] = $value
        }
    }
    foreach ($name in $deploymentSecretEnvironmentNames) {
        if ($providerFileValues.ContainsKey($name)) {
            $childEnvironment[$name] = $providerFileValues[$name]
        }
    }
}

$mergedEnvironmentValues = @{}
foreach ($existingName in @($childEnvironment.Keys)) {
    $mergedEnvironmentValues[[string]$existingName] = [string]$childEnvironment[$existingName]
}
Write-Host ""
Write-Host "배포에서 사람이 넣는 값의 존재 여부(값은 표시하지 않음):" -ForegroundColor Cyan
$missingSecretNames = @()
foreach ($name in $deploymentSecretEnvironmentNames) {
    $value = $mergedEnvironmentValues[$name]
    $present = $null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)
    $answer = if ($present) { "yes" } else { "no" }
    Write-Host ("{0}: {1}" -f $name, $answer)
    if (-not $present) { $missingSecretNames += $name }
}
Write-Host ("ADMIN_EMAILS: yes (인자로 받은 {0}명)" -f $adminEmailList.Count)
Write-Host "GOOGLE_REDIRECT_URI: yes (인자·기본값 — 아래에 표시)"

$allowedParentNames = $safeChildOsEnvironmentNames + $deploymentSecretEnvironmentNames
$childEnvironment = Reset-ChildEnvironmentToAllowlist `
    -Environment $childEnvironment `
    -AllowedNames $allowedParentNames

if ($missingSecretNames.Count -gt 0) {
    Write-Host ""
    Write-Host "아래 값이 없어 지금 입력받습니다. 입력값은 저장하지 않습니다." -ForegroundColor Yellow
    Write-Host "취소하려면 Ctrl+C를 누르세요."
    foreach ($name in $missingSecretNames) {
        $masked = ($plainTextSecretEnvironmentNames -notcontains $name)
        $childEnvironment[$name] = Read-MissingSecretValue `
            -Name $name `
            -Prompt $secretPromptLabels[$name] `
            -Masked $masked
    }
}

# ── 4. 실행마다 격리된 데이터 폴더 ──────────────────────────────────
$rehearsalRunsRoot = Join-Path $appRoot ".local_deployment_rehearsal_runs"
New-Item -ItemType Directory -Force -Path $rehearsalRunsRoot | Out-Null
$rehearsalRunsRoot = Assert-SafeRehearsalDirectory `
    -Candidate $rehearsalRunsRoot `
    -ApplicationRoot $appRoot
$kst = [System.TimeZoneInfo]::FindSystemTimeZoneById("Korea Standard Time")
$kstNow = [System.TimeZoneInfo]::ConvertTimeFromUtc([System.DateTime]::UtcNow, $kst)
$runDirectoryName = "{0}_{1}" -f `
    $kstNow.ToString("yyyyMMdd_HHmmss", [System.Globalization.CultureInfo]::InvariantCulture), `
    (New-CryptographicRunSuffix)
$rehearsalRoot = Join-Path $rehearsalRunsRoot $runDirectoryName
New-Item -ItemType Directory -Path $rehearsalRoot | Out-Null
$rehearsalRoot = Assert-SafeRehearsalDirectory `
    -Candidate $rehearsalRoot `
    -ApplicationRoot $appRoot
$recordsDirectory = Join-Path $rehearsalRoot "observability"
$tldextractCache = Join-Path $rehearsalRoot "cache\tldextract"
$runtimeTemp = Join-Path $rehearsalRoot "tmp"
$storageDatabase = Join-Path $rehearsalRoot "storage.db"
$recordsPath = Join-Path $recordsDirectory "runs.jsonl"
New-Item -ItemType Directory -Force -Path $recordsDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $tldextractCache | Out-Null
New-Item -ItemType Directory -Force -Path $runtimeTemp | Out-Null

# ── 5. 배포 계약값 ──────────────────────────────────────────────────
$childEnvironment["PYTHONUTF8"] = "1"
$childEnvironment["PYTHONIOENCODING"] = "utf-8"
$childEnvironment["PYTHONUNBUFFERED"] = "1"
# render.yaml과 같은 두 값 — 진짜 조사 + 관리자 로그인 게이트 켬.
$childEnvironment["PIPELINE"] = "real"
$childEnvironment["BETA_ADMIN_ONLY"] = "1"
# 로컬 http에서만 쿠키 예외를 허용한다. 앱이 loopback을 다시 확인한다.
$childEnvironment["AUTH_COOKIE_INSECURE"] = "1"
$childEnvironment["ADMIN_EMAILS"] = $normalizedAdminEmails
$childEnvironment["GOOGLE_REDIRECT_URI"] = $GoogleRedirectUri
$childEnvironment["PORT"] = [string]$Port
$childEnvironment["APP_DATA_ROOT"] = $rehearsalRoot
$childEnvironment["STORAGE_DB_PATH"] = $storageDatabase
$childEnvironment["OBSERVABILITY_RECORDS_PATH"] = $recordsPath
$childEnvironment["TLDEXTRACT_CACHE"] = $tldextractCache
$childEnvironment["TEMP"] = $runtimeTemp
$childEnvironment["TMP"] = $runtimeTemp
# Dockerfile:18-20의 로컬 배포 계약. 배포의 render 계약을 로컬 http에 그대로 쓰면
# 모든 POST가 Origin 불일치로 거부된다(위 머리말 참고).
$childEnvironment["DEPLOYMENT_EXPOSURE"] = "local"
$childEnvironment["DEPLOYMENT_PLATFORM"] = "local"
$childEnvironment["DEPLOYMENT_RUNTIME_CONTRACT"] = "local-web-v1"
$childEnvironment["FORWARDED_ALLOW_IPS"] = "127.0.0.1"
# PIPELINE=real은 32바이트 이상의 출처 도장 비밀을 요구한다
# (src/web/runtime.py:110-114). 배포는 Render가 고정값을 만들고, 리허설은
# 실행마다 새로 만들어 부모 환경·파일 어디에도 남기지 않는다.
$childEnvironment["PROVENANCE_SEAL_SECRET"] = (New-RandomHexSecret -ByteCount 32)
# 배포 이미지에는 조사 엔진 폴더의 환경 파일이 아예 들어가지 않는다(Dockerfile:37 주석).
# 로컬에는 그 파일이 있으므로, 배포와 같은 조건을 만들려면 자동 읽기를 막아야 한다.
$childEnvironment["ANALYSIS_ENGINE_DISABLE_DOTENV"] = "1"
# 회사 후보 공급자는 배포에서도 설정하지 않아 기본값 disabled로 동작한다
# (src/features/business_candidate/providers.py:111). 여기서는 명시적으로 닫는다.
$childEnvironment["BUSINESS_CANDIDATE_PROVIDER"] = "disabled"
# 엔진 v2 스위치: 값이 정확히 "1"일 때만 real.py가 composer 경로로 분기한다
# (src/features/pipeline/real.py:197-209).
if ($DisableEngineV2) {
    $childEnvironment["ENGINE_V2"] = "0"
}
else {
    $childEnvironment["ENGINE_V2"] = "1"
}

$allowedChildEnvironmentNames = $allowedParentNames + @(
    "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PIPELINE",
    "BETA_ADMIN_ONLY", "AUTH_COOKIE_INSECURE", "ADMIN_EMAILS",
    "GOOGLE_REDIRECT_URI", "PORT", "APP_DATA_ROOT", "STORAGE_DB_PATH",
    "OBSERVABILITY_RECORDS_PATH", "TLDEXTRACT_CACHE",
    "DEPLOYMENT_EXPOSURE", "DEPLOYMENT_PLATFORM", "DEPLOYMENT_RUNTIME_CONTRACT",
    "FORWARDED_ALLOW_IPS", "PROVENANCE_SEAL_SECRET",
    "ANALYSIS_ENGINE_DISABLE_DOTENV", "BUSINESS_CANDIDATE_PROVIDER", "ENGINE_V2"
)
foreach ($name in @($childEnvironment.Keys)) {
    if ($allowedChildEnvironmentNames -notcontains [string]$name) {
        throw "허용하지 않은 환경 '$name'이 감지되어 시작하지 않습니다."
    }
}
foreach ($name in $deploymentSecretEnvironmentNames) {
    if ([string]::IsNullOrWhiteSpace([string]$childEnvironment[$name])) {
        throw "배포와 같은 조건을 만들 수 없습니다 — 값이 없습니다: $name"
    }
}

# ── 6. 실행 ─────────────────────────────────────────────────────────
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $startInfo
$exitCode = 1
$started = $false
try {
    if (-not $process.Start()) {
        throw "배포 리허설 서버를 시작하지 못했습니다."
    }
    $started = $true
    if (-not (Wait-ForLoopbackListener -Process $process -RequestedPort $Port)) {
        if ($process.HasExited) {
            throw "서버가 시작 전에 종료되었습니다. Python 환경과 설정을 확인해 주세요."
        }
        throw "서버가 30초 안에 시작되지 않았습니다."
    }

    $url = "http://127.0.0.1:$Port"
    Write-Host ""
    Write-Host "배포 리허설을 켰습니다: $url" -ForegroundColor Yellow
    Write-Host "PIPELINE=real · BETA_ADMIN_ONLY=1 — 배포와 같은 관리자 로그인 게이트입니다."
    if ($DisableEngineV2) {
        Write-Host "엔진: v1 (-DisableEngineV2)"
    }
    else {
        Write-Host "엔진: v2 (ENGINE_V2=1)"
    }
    Write-Host ""
    Write-Host "★ 들어가는 방법" -ForegroundColor Cyan
    Write-Host "  1) 브라우저에서 $url 을 엽니다."
    Write-Host "  2) 로그인 화면으로 넘어가면 구글 계정으로 로그인합니다."
    Write-Host "  3) -AdminEmails 로 넣은 계정으로 로그인해야 관리자 화면($url/admin)이 열립니다."
    Write-Host ""
    Write-Host "★ 구글 클라우드 콘솔에 «미리» 등록돼 있어야 하는 값" -ForegroundColor Yellow
    Write-Host "  승인된 리디렉션 URI: $GoogleRedirectUri"
    Write-Host "  이게 등록돼 있지 않으면 구글이 redirect_uri_mismatch로 거절합니다."
    Write-Host "  등록 순서는 docs\구글로그인_설정.md 3단계와 같습니다."
    Write-Host ""
    Write-Host "★ 배포와 일부러 다른 점 두 가지" -ForegroundColor DarkGray
    Write-Host "  - 주소가 https 공개주소가 아니라 http://127.0.0.1 입니다 (쿠키 Secure 예외)."
    Write-Host "  - DEPLOYMENT_RUNTIME_CONTRACT가 local-web-v1 입니다. 배포 계약을 그대로"
    Write-Host "    쓰면 로컬에서는 모든 POST가 Origin 불일치로 거부됩니다."
    Write-Host ""
    Write-Host "진짜 조사이므로 조사할 때마다 실제 요금이 발생합니다." -ForegroundColor Red
    if ($ProviderEnvFile) {
        Write-Host "키 값은 출력·파일 저장하지 않았습니다. 지정한 provider 환경 파일만 읽었으며 자식의 환경 파일 자동 읽기는 차단했습니다."
    }
    else {
        Write-Host "키 값은 출력·파일 저장하지 않았고 환경 파일도 읽지 않았습니다."
    }
    Write-Host "리허설 기록은 app\.local_deployment_rehearsal_runs의 이번 실행 폴더에만 저장됩니다."
    Write-Host "끄려면 이 창에서 Ctrl+C를 누르세요."
    Write-Host ""

    $process.WaitForExit()
    $exitCode = $process.ExitCode
}
finally {
    if ($started -and -not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
    }
    $process.Dispose()
    if ($DeleteDataOnExit -and (Test-Path -LiteralPath $rehearsalRoot -PathType Container)) {
        $safeDeleteTarget = Assert-SafeRehearsalDirectory `
            -Candidate $rehearsalRoot `
            -ApplicationRoot $appRoot
        $linkedChild = Get-ChildItem -LiteralPath $safeDeleteTarget -Force -Recurse |
            Where-Object {
                ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
            } |
            Select-Object -First 1
        if ($null -ne $linkedChild) {
            throw "격리 폴더 안에서 링크(reparse point)를 발견해 자동 삭제를 중단합니다."
        }
        Remove-Item -LiteralPath $safeDeleteTarget -Recurse -Force
        Write-Host "이번 실행의 격리 데이터를 삭제했습니다."
    }
    elseif (Test-Path -LiteralPath $rehearsalRoot -PathType Container) {
        Write-Host "이번 실행 데이터 보존 위치: $rehearsalRoot"
        Write-Host "필요 없어진 뒤 이 폴더만 직접 삭제하거나 다음 실행에 -DeleteDataOnExit를 사용하세요."
        Write-Host "개인정보가 포함될 수 있으므로 24시간 안에 검토·삭제하는 것을 권장합니다."
    }
}

if ($exitCode -ne 0) {
    throw "배포 리허설 서버가 오류로 종료되었습니다 (종료 코드: $exitCode)."
}
