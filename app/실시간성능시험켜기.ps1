[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8020,

    # 본조사 예약액(app/src/features/budget/constants.py PAID_PHASE_PROVIDER_BUDGET_KRW
    # = 1,800원)보다 작으면 유료 호출 전에 전부 거절된다. 기본값은 그 위로 둔다.
    [ValidateRange(1, 100000)]
    [double]$PerRunExpectedCostCapKrw = 2000,

    [ValidateRange(1, 100000)]
    [double]$DailyExpectedCostCapKrw = 5000,

    [switch]$EnablePaidProviders,

    [string]$ProviderEnvFile = "",

    # 저장소 Blueprint를 기본으로 읽되 실제 Render 설정을 확인했다는 뜻은 아니다.
    [ValidateSet("RepositoryContract", "Explicit", "ProductionObserved20260908")]
    [string]$ConfigurationProfile = "RepositoryContract",
    [string]$NewsIntake = "",
    [string]$RevenueTableV2 = "",
    [string]$TypedDartCollector = "",
    [string]$EvidenceReclassify = "",
    [string]$NewsroomDateAI = "",

    # 엔진을 명시적으로 덮어쓴다. 생략하면 선택한 profile의 값을 사용한다.
    [switch]$EngineV2,

    # -EngineV2를 켰을 때 보고서를 «어느 출시 모드로» 만들지. 이 값이 비면 v2
    # 경로는 AI를 부르기 전에 입력 계약으로 멈춰(src/features/pipeline/real.py:
    # 3510-3514) 성능을 잴 구간까지 가지 못한다. 허용 값은 ReleaseMode 계약
    # 그대로만 받는다 (src/shared/report_evidence/constants.py:81-87).
    # ★ 값 검사는 [ValidateSet]이 아니라 아래 본문에서 «대소문자까지» 한다.
    #   ValidateSet 위반은 «종료 오류»다. -File로 평범하게 켜면 실패(코드 1)로 끝나기는
    #   하지만 화면에는 PowerShell 바인더의 오류 덩어리만 남고, 이 실행기를 try/catch로
    #   감싸 부르는 쪽에서는 그 오류가 삼켜져 거부가 «성공»(코드 0)으로 보인다.
    #   본문 검사는 어느 쪽으로 부르든 읽을 수 있는 안내를 남기고 0이 아닌 코드로
    #   끝낸다. 대신 -ReleaseMode의 탭 자동완성을 잃는 것과 맞바꿨다.
    [string]$ReleaseMode = "FULL",

    [switch]$DeleteDataOnExit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ★ 출시 모드는 «대소문자까지» 계약과 같아야 한다 — 앱의 해석기는 소문자를 고쳐
#   읽지 않으므로, "full"을 받아 주면 사람은 켰다고 믿는데 자식이 입력 계약으로 멈춘다.
#   계약 밖 값이면 아무것도 시작하지 않고 0이 아닌 종료 코드로 끝낸다.
$allowedReleaseModes = @("SHADOW", "ENFORCE_NO_PARTIAL", "FULL")
if ($allowedReleaseModes -cnotcontains $ReleaseMode) {
    Write-Host ""
    Write-Host "-ReleaseMode 값을 쓸 수 없습니다: $ReleaseMode" -ForegroundColor Red
    Write-Host ("쓸 수 있는 값은 {0} 입니다. 대문자 그대로 적습니다." -f ($allowedReleaseModes -join " · "))
    Write-Host ""
    exit 2
}

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
    "NCP_APIGW_API_KEY_ID",
    "NCP_APIGW_API_KEY"
)
$providerStatusEnvironmentNames = @(
    "DART_API_KEY",
    "ANTHROPIC_API_KEY",
    "NCP_APIGW_API_KEY_ID",
    "NCP_APIGW_API_KEY",
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

function Get-Utf8Sha256 {
    param([Parameter(Mandatory = $true)][string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString(
            $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))
        )).Replace("-", "").ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Get-EvaluationFeatureSettings {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)
    $settings = [ordered]@{
        ENGINE_V2 = "0"
        REPORT_RELEASE_MODE = $ReleaseMode
        NEWS_INTAKE = "0"
        REVENUE_TABLE_V2 = "0"
        TYPED_DART_COLLECTOR = "0"
        EVIDENCE_RECLASSIFY = "0"
        NEWSROOM_DATE_AI = "0"
    }
    $contractSha256 = ""
    if ($ConfigurationProfile -eq "ProductionObserved20260908") {
        # 2026-09-08 코디네이터가 Render 환경 페이지에서 관측한 비밀 아닌 스위치.
        # 나머지 네 키는 페이지에 없었고 앱 기본값 0을 사용한다.
        $settings["ENGINE_V2"] = "1"
        $settings["NEWS_INTAKE"] = "1"
        $settings["REPORT_RELEASE_MODE"] = "FULL"
    }
    if ($ConfigurationProfile -eq "RepositoryContract") {
        $contractPath = Join-Path $RepositoryRoot "render.yaml"
        if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
            throw "저장소 profile에 필요한 render.yaml이 없습니다. 명시 설정은 -ConfigurationProfile Explicit를 사용하세요."
        }
        $contract = [System.IO.File]::ReadAllText($contractPath, [System.Text.Encoding]::UTF8)
        $contractSha256 = Get-Utf8Sha256 -Text $contract
        foreach ($name in @($settings.Keys)) {
            # 닫힌 설정 이름의 바로 다음 value만 읽는다. 환경 전체나 비밀을 읽지 않는다.
            $pattern = '(?m)^\s*- key:\s*' + [regex]::Escape($name) + '\s*\r?\n\s*value:\s*([^\r\n]+)'
            $matches = [regex]::Matches($contract, $pattern)
            if ($matches.Count -gt 1) { throw "저장소 설정이 중복되었습니다: $name" }
            if ($matches.Count -eq 1) {
                $settings[$name] = $matches[0].Groups[1].Value.Trim().Trim([char]34, [char]39)
            }
        }
    }
    $overrides = [ordered]@{
        NEWS_INTAKE = $NewsIntake
        REVENUE_TABLE_V2 = $RevenueTableV2
        TYPED_DART_COLLECTOR = $TypedDartCollector
        EVIDENCE_RECLASSIFY = $EvidenceReclassify
        NEWSROOM_DATE_AI = $NewsroomDateAI
    }
    foreach ($name in @($overrides.Keys)) {
        if ($overrides[$name] -ne "") { $settings[$name] = $overrides[$name] }
    }
    if ($script:PSBoundParameters.ContainsKey("EngineV2")) {
        $settings["ENGINE_V2"] = $(if ($EngineV2) { "1" } else { "0" })
    }
    if ($script:PSBoundParameters.ContainsKey("ReleaseMode")) {
        $settings["REPORT_RELEASE_MODE"] = $ReleaseMode
    }
    foreach ($name in @($settings.Keys)) {
        if ($name -eq "REPORT_RELEASE_MODE") {
            if ($allowedReleaseModes -cnotcontains $settings[$name]) {
                throw "설정 profile의 출시 모드가 올바르지 않습니다."
            }
        }
        elseif (@("0", "1") -cnotcontains $settings[$name]) {
            throw "기능 스위치는 정확히 0 또는 1이어야 합니다: $name"
        }
    }
    return @{ Settings = $settings; ContractSha256 = $contractSha256 }
}

function Invoke-EvaluationGitRead {
    param([string]$Executable, [string]$RepositoryRoot, [string]$Arguments)
    $gitProcess = New-Object System.Diagnostics.Process
    $gitProcess.StartInfo.FileName = $Executable
    $gitProcess.StartInfo.Arguments = '-C "' + $RepositoryRoot + '" ' + $Arguments
    $gitProcess.StartInfo.UseShellExecute = $false
    $gitProcess.StartInfo.CreateNoWindow = $true
    $gitProcess.StartInfo.RedirectStandardOutput = $true
    $gitProcess.StartInfo.RedirectStandardError = $true
    $gitProcess.StartInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $gitProcess.StartInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    try {
        if (-not $gitProcess.Start()) { throw "Git 읽기 검사를 시작하지 못했습니다." }
        $output = $gitProcess.StandardOutput.ReadToEnd()
        $null = $gitProcess.StandardError.ReadToEnd()
        $gitProcess.WaitForExit()
        if ($gitProcess.ExitCode -ne 0) { throw "Git 읽기 검사를 완료하지 못했습니다." }
        return $output.Trim()
    }
    finally { $gitProcess.Dispose() }
}

function Get-EvaluationCodeReceipt {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)
    $receipt = @{ Commit = ""; Clean = $false; Verified = $false }
    try {
        $gitCommand = @(Get-Command "git" -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
        if ($null -eq $gitCommand) { throw "Git을 찾지 못했습니다." }
        $root = Invoke-EvaluationGitRead -Executable $gitCommand.Source -RepositoryRoot $RepositoryRoot -Arguments "rev-parse --show-toplevel"
        if ([System.IO.Path]::GetFullPath($root).TrimEnd('\') -ine [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')) {
            throw "실행 폴더의 Git 저장소 경계가 일치하지 않습니다."
        }
        $commit = Invoke-EvaluationGitRead -Executable $gitCommand.Source -RepositoryRoot $RepositoryRoot -Arguments "rev-parse --verify HEAD"
        if ($commit -cnotmatch '^[0-9a-f]{40}$') { throw "실제 Git 커밋 40자리를 확인하지 못했습니다." }
        # 실행 소스·템플릿·런처·의존성 계약을 검사한다. 키·DB·결과 자료는 대상이 아니다.
        $statusArguments = 'status --porcelain=v1 --untracked-files=all -- app/src analysis_engine/src analysis_engine/tools/run_pilot.py analysis_engine/tools/survey_audit_reports.py analysis_engine/tools/build_goldenset_answer.py app/requirements.txt app/Dockerfile "app/실시간성능시험켜기.ps1" render.yaml'
        $changes = Invoke-EvaluationGitRead -Executable $gitCommand.Source -RepositoryRoot $RepositoryRoot -Arguments $statusArguments
        $receipt.Commit = $commit
        $receipt.Clean = [string]::IsNullOrWhiteSpace($changes)
        $receipt.Verified = $receipt.Clean
    }
    catch {
        # Git 환경이나 원격 설정 등 원본 오류는 출력하지 않는다.
        if ($EnablePaidProviders) {
            Write-Host "실행 소스의 실제 Git 신원을 검증하지 못했습니다. 저장소 커밋과 Git 설치를 확인하세요." -ForegroundColor Red
            exit 2
        }
    }
    if ($EnablePaidProviders -and -not $receipt.Clean) {
        Write-Host "유료 시험 실행 소스에 미커밋 변경 또는 미추적 파일이 있습니다. 검토 후 커밋하고 다시 시작하세요." -ForegroundColor Red
        exit 2
    }
    return $receipt
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
$featureProfile = Get-EvaluationFeatureSettings -RepositoryRoot (Split-Path -Parent $appRoot)
$codeReceipt = Get-EvaluationCodeReceipt -RepositoryRoot (Split-Path -Parent $appRoot)
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

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $python
$startInfo.WorkingDirectory = $appRoot
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
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
if ($codeReceipt.Verified) {
    $childEnvironment["APP_GIT_COMMIT"] = $codeReceipt.Commit
}

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
# 엔진 v2 스위치: 값이 정확히 "1"일 때만 real.py가 composer 경로로 분기한다.
if ($featureProfile.Settings["ENGINE_V2"] -eq "1") {
    $childEnvironment["ENGINE_V2"] = "1"
    # ★ 이 값이 없으면 조사가 AI 호출 전에 멈춰 성능시험이 아무것도 재지 못한다.
    $childEnvironment["REPORT_RELEASE_MODE"] = $featureProfile.Settings["REPORT_RELEASE_MODE"]
}
foreach ($name in @("NEWS_INTAKE", "REVENUE_TABLE_V2", "TYPED_DART_COLLECTOR", "EVIDENCE_RECLASSIFY", "NEWSROOM_DATE_AI")) {
    $childEnvironment[$name] = $featureProfile.Settings[$name]
}

# 비밀이나 부모 환경 전체를 직렬화하지 않는다. 관측값은 스위치 범위만 뜻한다.
$observedProductionSwitches = [ordered]@{
    ENGINE_V2 = "1"; REPORT_RELEASE_MODE = "FULL"; NEWS_INTAKE = "1"
    REVENUE_TABLE_V2 = "0"; TYPED_DART_COLLECTOR = "0"
    EVIDENCE_RECLASSIFY = "0"; NEWSROOM_DATE_AI = "0"
}
$matchesObservedSwitches = $true
foreach ($name in @($observedProductionSwitches.Keys)) {
    if ($featureProfile.Settings[$name] -cne $observedProductionSwitches[$name]) {
        $matchesObservedSwitches = $false
    }
}
$snapshot = [ordered]@{
    schema_version = "company-evaluation-settings-v1"
    configuration_profile = $ConfigurationProfile
    app_git_commit = $codeReceipt.Commit
    execution_source_clean = $codeReceipt.Clean
    code_identity_verified = $codeReceipt.Verified
    production_parity = "not_verified"
    observed_production_switches_date = "2026-09-08"
    matches_observed_production_switches = $matchesObservedSwitches
    repository_contract_sha256 = $featureProfile.ContractSha256
    origin = "http://127.0.0.1:$Port"
    settings = $featureProfile.Settings
    paid_providers_enabled = [bool]$EnablePaidProviders
    per_run_expected_cost_cap_krw = $PerRunExpectedCostCapKrw
    daily_expected_cost_cap_krw = $DailyExpectedCostCapKrw
}
$snapshotJson = $snapshot | ConvertTo-Json -Depth 5 -Compress
$snapshotDigest = Get-Utf8Sha256 -Text $snapshotJson
$snapshotPath = Join-Path $evaluationRoot "evaluation-settings.json"
[System.IO.File]::WriteAllText($snapshotPath, $snapshotJson, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText(
    (Join-Path $evaluationRoot "evaluation-settings.sha256"),
    $snapshotDigest, [System.Text.UTF8Encoding]::new($false)
)
Write-Host "비밀 제외 실행설정: $snapshotPath"
Write-Host "설정 SHA-256: $snapshotDigest / 운영 실측 일치 여부: 미확인"
Write-Host "2026-09-08 관측한 운영 스위치 일치: $matchesObservedSwitches (전체 배포·모델·데이터 일치 증명 아님)"

$allowedChildEnvironmentNames = $allowedParentNames + @(
    "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PIPELINE",
    "BETA_ADMIN_ONLY", "AUTH_COOKIE_INSECURE", "PORT", "APP_DATA_ROOT",
    "STORAGE_DB_PATH", "OBSERVABILITY_RECORDS_PATH", "TLDEXTRACT_CACHE",
    "REALTIME_EVALUATION_MODE", "REALTIME_EVALUATION_PAID_PROVIDERS",
    "REALTIME_EVALUATION_PER_RUN_CAP_KRW", "REALTIME_EVALUATION_DAILY_CAP_KRW",
    "PROVENANCE_SEAL_SECRET",
    "ANALYSIS_ENGINE_DISABLE_DOTENV", "GOOGLE_PLACES_BILLING_ACK",
    "GOOGLE_PLACES_TERMS_ACK", "BUSINESS_CANDIDATE_PROVIDER", "ENGINE_V2",
    "REPORT_RELEASE_MODE", "NEWS_INTAKE", "REVENUE_TABLE_V2", "TYPED_DART_COLLECTOR",
    "EVIDENCE_RECLASSIFY", "NEWSROOM_DATE_AI",
    "APP_GIT_COMMIT"
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
