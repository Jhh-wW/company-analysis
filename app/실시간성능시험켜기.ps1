[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8020,

    [ValidateRange(1, 100000)]
    [double]$PerRunExpectedCostCapKrw = 1200,

    [ValidateRange(1, 100000)]
    [double]$DailyExpectedCostCapKrw = 2200,

    [switch]$EnablePaidProviders,

    [string]$ProviderEnvFile = "",

    [switch]$DeleteDataOnExit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# OWASP least privilege 원칙에 따라 자식은 Windows/Python 실행에 필요한 OS 값과
# 아래에서 명시한 평가 설정·provider key 이름만 받는다. 값은 출력하거나 저장하지 않는다.
$safeChildOsEnvironmentNames = @(
    "SystemRoot", "WINDIR", "SystemDrive", "ComSpec", "PATH", "PATHEXT",
    "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "ALLUSERSPROFILE", "ProgramData", "ProgramFiles",
    "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles",
    "CommonProgramFiles(x86)", "CommonProgramW6432"
)
$paidProviderEnvironmentNames = @(
    "DART_API_KEY",
    "ANTHROPIC_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET"
)
$providerStatusEnvironmentNames = @(
    "DART_API_KEY",
    "ANTHROPIC_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "GOOGLE_PLACES_API_KEY",
    "GOOGLE_PLACES_TERMS_ACK"
)

function Get-CompatibleChildEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.ProcessStartInfo]$StartInfo
    )

    $environment = $null
    try { $environment = $StartInfo.EnvironmentVariables } catch { $environment = $null }
    if ($null -eq $environment) {
        try { $environment = $StartInfo.EnvironmentVariables } catch { $environment = $null }
    }
    if ($null -eq $environment) {
        try { $environment = $StartInfo.Environment } catch { $environment = $null }
    }
    if ($null -eq $environment) {
        try { $environment = $StartInfo.Environment } catch { $environment = $null }
    }
    if ($null -eq $environment) {
        throw "자식 프로세스의 격리 환경을 만들 수 없어 안전하게 중단합니다."
    }

    $probeName = "REALTIME_EVALUATION_LAUNCHER_ENV_PROBE"
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

    foreach ($name in @(
        "SystemRoot", "WINDIR", "ComSpec", "PATH"
    )) {
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

function Assert-SafeEvaluationDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$ApplicationRoot
    )
    $resolved = [System.IO.Path]::GetFullPath($Candidate)
    $root = [System.IO.Path]::GetFullPath($ApplicationRoot).TrimEnd('\')
    if (-not $resolved.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "성능시험 데이터 폴더가 app 폴더 밖을 가리켜 중단합니다."
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "성능시험 데이터 폴더가 링크(reparse point)라 중단합니다."
    }
    return $resolved
}

function Wait-ForLoopbackListener {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [int]$RequestedPort,
        [int]$TimeoutMilliseconds = 15000
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

if ($PerRunExpectedCostCapKrw -gt $DailyExpectedCostCapKrw) {
    throw "건당 예상비용 상한은 일일 예상비용 상한보다 클 수 없습니다."
}

$appRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$python = Join-Path $appRoot ".venv\Scripts\python.exe"
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

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $python
$startInfo.WorkingDirectory = $appRoot
$startInfo.UseShellExecute = $false
$startInfo.Arguments = "-m uvicorn src.web.main:app --host 127.0.0.1 --port $Port --workers 1 --no-access-log"

$childEnvironment = Get-CompatibleChildEnvironment -StartInfo $startInfo

if ($ProviderEnvFile) {
    if (-not $EnablePaidProviders) {
        throw "-ProviderEnvFile은 -EnablePaidProviders와 함께 사용해야 합니다."
    }
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
        if ($paidProviderEnvironmentNames -notcontains $name) { continue }
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
    foreach ($name in $paidProviderEnvironmentNames) {
        if ($providerFileValues.ContainsKey($name)) {
            $childEnvironment[$name] = $providerFileValues[$name]
        }
    }
}

$parentEnvironmentValues = @{}
foreach ($existingName in @($childEnvironment.Keys)) {
    $parentEnvironmentValues[[string]$existingName] = [string]$childEnvironment[$existingName]
}
$missingProviderNames = @()
Write-Host ""
Write-Host "필수 provider 환경변수 존재 여부(값은 표시하지 않음):" -ForegroundColor Cyan
foreach ($name in $providerStatusEnvironmentNames) {
    $value = $parentEnvironmentValues[$name]
    $present = $null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)
    $answer = if ($present) { "yes" } else { "no" }
    Write-Host ("{0}: {1}" -f $name, $answer)
    if (-not $present -and $paidProviderEnvironmentNames -contains $name) {
        $missingProviderNames += $name
    }
}

if ($EnablePaidProviders -and $missingProviderNames.Count -gt 0) {
    throw (
        "-EnablePaidProviders를 사용했지만 필요한 환경변수가 없습니다: " +
        ($missingProviderNames -join ", ")
    )
}

$allowedParentNames = @($safeChildOsEnvironmentNames)
if ($EnablePaidProviders) {
    $allowedParentNames += $paidProviderEnvironmentNames
}
$childEnvironment = Reset-ChildEnvironmentToAllowlist `
    -Environment $childEnvironment `
    -AllowedNames $allowedParentNames

$evaluationRunsRoot = Join-Path $appRoot ".local_evaluation_runs"
New-Item -ItemType Directory -Force -Path $evaluationRunsRoot | Out-Null
$evaluationRunsRoot = Assert-SafeEvaluationDirectory `
    -Candidate $evaluationRunsRoot `
    -ApplicationRoot $appRoot
$kst = [System.TimeZoneInfo]::FindSystemTimeZoneById("Korea Standard Time")
$kstNow = [System.TimeZoneInfo]::ConvertTimeFromUtc([System.DateTime]::UtcNow, $kst)
$runDirectoryName = "{0}_{1}" -f `
    $kstNow.ToString("yyyyMMdd_HHmmss", [System.Globalization.CultureInfo]::InvariantCulture), `
    (New-CryptographicRunSuffix)
$evaluationRoot = Join-Path $evaluationRunsRoot $runDirectoryName
New-Item -ItemType Directory -Path $evaluationRoot | Out-Null
$evaluationRoot = Assert-SafeEvaluationDirectory `
    -Candidate $evaluationRoot `
    -ApplicationRoot $appRoot
$recordsDirectory = Join-Path $evaluationRoot "observability"
$tldextractCache = Join-Path $evaluationRoot "cache\tldextract"
$runtimeTemp = Join-Path $evaluationRoot "tmp"
$storageDatabase = Join-Path $evaluationRoot "storage.db"
$recordsPath = Join-Path $recordsDirectory "runs.jsonl"
New-Item -ItemType Directory -Force -Path $recordsDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $tldextractCache | Out-Null
New-Item -ItemType Directory -Force -Path $runtimeTemp | Out-Null

$invariant = [System.Globalization.CultureInfo]::InvariantCulture
$childEnvironment["PYTHONUTF8"] = "1"
$childEnvironment["PYTHONIOENCODING"] = "utf-8"
$childEnvironment["PYTHONUNBUFFERED"] = "1"
$childEnvironment["PIPELINE"] = "real"
$childEnvironment["BETA_ADMIN_ONLY"] = "0"
$childEnvironment["AUTH_COOKIE_INSECURE"] = "1"
$childEnvironment["PORT"] = [string]$Port
$childEnvironment["APP_DATA_ROOT"] = $evaluationRoot
$childEnvironment["STORAGE_DB_PATH"] = $storageDatabase
$childEnvironment["OBSERVABILITY_RECORDS_PATH"] = $recordsPath
$childEnvironment["TLDEXTRACT_CACHE"] = $tldextractCache
$childEnvironment["TEMP"] = $runtimeTemp
$childEnvironment["TMP"] = $runtimeTemp
$childEnvironment["REALTIME_EVALUATION_MODE"] = "1"
$childEnvironment["REALTIME_EVALUATION_PAID_PROVIDERS"] = $(
    if ($EnablePaidProviders) { "1" } else { "0" }
)
$childEnvironment["REALTIME_EVALUATION_PER_RUN_CAP_KRW"] = `
    $PerRunExpectedCostCapKrw.ToString($invariant)
$childEnvironment["REALTIME_EVALUATION_DAILY_CAP_KRW"] = `
    $DailyExpectedCostCapKrw.ToString($invariant)
# 실시간 보고서의 출처 원문·해시를 잠그는 이 실행 전용 비밀이다. provider 키와
# 달리 사용자가 발급할 값이 아니므로 매 실행 새로 만들고 파일·부모 환경에는 남기지 않는다.
$sealBytes = New-Object byte[] 32
$sealRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $sealRng.GetBytes($sealBytes)
}
finally {
    $sealRng.Dispose()
}
$childEnvironment["PROVENANCE_SEAL_SECRET"] = `
    [System.BitConverter]::ToString($sealBytes).Replace("-", "").ToLowerInvariant()
$childEnvironment["ANALYSIS_ENGINE_DISABLE_DOTENV"] = "1"
# Google Places 결과는 별도 약관 검토와 명시 opt-in이 끝날 때까지 실제 실행기에서
# 항상 닫는다. 부모에 key/ACK가 있어도 자식으로 전달하지 않는다. 후보 흐름 자체는
# fake adapter E2E로만 검증한다.
$childEnvironment["GOOGLE_PLACES_BILLING_ACK"] = "0"
$childEnvironment["GOOGLE_PLACES_TERMS_ACK"] = "no"
$childEnvironment["BUSINESS_CANDIDATE_PROVIDER"] = "disabled"

$allowedChildEnvironmentNames = $allowedParentNames + @(
    "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PIPELINE",
    "BETA_ADMIN_ONLY", "AUTH_COOKIE_INSECURE", "PORT", "APP_DATA_ROOT",
    "STORAGE_DB_PATH", "OBSERVABILITY_RECORDS_PATH", "TLDEXTRACT_CACHE",
    "REALTIME_EVALUATION_MODE", "REALTIME_EVALUATION_PAID_PROVIDERS",
    "REALTIME_EVALUATION_PER_RUN_CAP_KRW", "REALTIME_EVALUATION_DAILY_CAP_KRW",
    "PROVENANCE_SEAL_SECRET",
    "ANALYSIS_ENGINE_DISABLE_DOTENV", "GOOGLE_PLACES_BILLING_ACK",
    "GOOGLE_PLACES_TERMS_ACK", "BUSINESS_CANDIDATE_PROVIDER"
)
foreach ($name in @($childEnvironment.Keys)) {
    if ($allowedChildEnvironmentNames -notcontains [string]$name) {
        throw "허용하지 않은 환경 '$name'이 감지되어 시작하지 않습니다."
    }
}

$process = New-Object System.Diagnostics.Process
$process.StartInfo = $startInfo
$exitCode = 1
$started = $false
try {
    if (-not $process.Start()) {
        throw "실시간 성능시험 서버를 시작하지 못했습니다."
    }
    $started = $true
    if (-not (Wait-ForLoopbackListener -Process $process -RequestedPort $Port)) {
        if ($process.HasExited) {
            throw "서버가 시작 전에 종료되었습니다. Python 환경과 설정을 확인해 주세요."
        }
        throw "서버가 15초 안에 시작되지 않았습니다."
    }

    $url = "http://127.0.0.1:$Port"
    Write-Host ""
    if ($EnablePaidProviders) {
        Write-Host "실시간 성능시험(유료 provider 허용)을 켰습니다: $url" -ForegroundColor Yellow
        Write-Host "브라우저에서 비용·외부호출 동의를 체크해야 첫 호출이 시작됩니다."
        Write-Host "Google Places 후보 검색은 약관 검토 전까지 이 실행기에서 잠겨 있습니다."
    }
    else {
        Write-Host "실시간 성능시험 미리보기를 켰습니다: $url" -ForegroundColor Cyan
        Write-Host "외부 호출은 0건입니다. 실제 시험은 명시적으로 -EnablePaidProviders를 사용해야 합니다."
    }
    Write-Host (
        "예상비용 운영 기준: 건당 {0}원 / 일일 {1}원 (한국시간)" -f
        $PerRunExpectedCostCapKrw, $DailyExpectedCostCapKrw
    )
    Write-Host "청구액 hard cap이 아니라 호출 전 예상예약 차단 기준이며, 실제 단가·사용량에 따라 초과할 수 있습니다."
    if ($ProviderEnvFile) {
        Write-Host "키 값은 출력·파일 저장하지 않았습니다. 지정한 provider 환경 파일만 읽었으며 자식의 환경 파일 자동 읽기는 차단했습니다."
    }
    else {
        Write-Host "키 값은 출력·파일 저장하지 않았고 환경 파일도 읽지 않았습니다."
    }
    Write-Host "시험 기록은 app\.local_evaluation_runs의 이번 실행 폴더에만 저장됩니다."
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
    if ($DeleteDataOnExit -and (Test-Path -LiteralPath $evaluationRoot -PathType Container)) {
        $safeDeleteTarget = Assert-SafeEvaluationDirectory `
            -Candidate $evaluationRoot `
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
    elseif (Test-Path -LiteralPath $evaluationRoot -PathType Container) {
        Write-Host "이번 실행 데이터 보존 위치: $evaluationRoot"
        Write-Host "필요 없어진 뒤 이 폴더만 직접 삭제하거나 다음 실행에 -DeleteDataOnExit를 사용하세요."
        Write-Host "개인정보가 포함될 수 있으므로 24시간 안에 검토·삭제하는 것을 권장합니다."
    }
}

if ($exitCode -ne 0) {
    throw "실시간 성능시험 서버가 오류로 종료되었습니다 (종료 코드: $exitCode)."
}
