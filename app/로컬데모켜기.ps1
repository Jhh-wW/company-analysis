[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 자식 Python이 Windows DLL·임시 폴더·사용자별 캐시 위치를 찾는 데 필요한 경로만
# 부모에서 받는다. Python 설정·cloud/CI/DB/SaaS 변수는 이름과 무관하게 상속하지 않는다.
$safeChildOsEnvironmentNames = @(
    "SystemRoot",
    "WINDIR",
    "SystemDrive",
    "ComSpec",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "ALLUSERSPROFILE",
    "ProgramData",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "CommonProgramFiles",
    "CommonProgramFiles(x86)",
    "CommonProgramW6432"
)

function Get-CompatibleChildEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.ProcessStartInfo]$StartInfo
    )

    # Windows PowerShell 5.1/CLR 4 조합 일부에서는 두 환경 getter 모두 첫 접근이
    # null이고 두 번째 접근에서 초기화된다. 어느 한 속성으로 단순 치환하지 말고,
    # 지원하는 두 API를 각각 다시 얻은 뒤 실제 쓰기까지 확인한다.
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

    $probeName = "LOCAL_DEMO_LAUNCHER_ENV_PROBE"
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
        [Parameter(Mandatory = $true)]
        $Environment,
        [Parameter(Mandatory = $true)]
        [string[]]$AllowedNames
    )

    # ProcessStartInfo가 만든 부모 snapshot에서 허용한 OS 값만 잠깐 보관한다.
    # 값은 화면이나 파일에 내보내지 않는다. Clear 뒤에는 임의 이름의 비밀도 남지 않는다.
    $allowedValues = @{}
    foreach ($name in $AllowedNames) {
        $value = $Environment[$name]
        if ($null -ne $value -and [string]$value -ne "") {
            $allowedValues[$name] = [string]$value
        }
    }

    $requiredNames = @(
        "SystemRoot",
        "WINDIR",
        "ComSpec",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA"
    )
    foreach ($name in $requiredNames) {
        if (-not $allowedValues.ContainsKey($name)) {
            throw "자식 프로세스에 필요한 Windows 환경 '$name'을 찾지 못해 안전하게 중단합니다."
        }
    }

    $Environment.Clear()
    foreach ($name in $AllowedNames) {
        if ($allowedValues.ContainsKey($name)) {
            $Environment[$name] = $allowedValues[$name]
        }
    }

    # CLR adapter 차이로 Clear가 조용히 실패하는 경우에도 비밀을 상속한 채 진행하지 않는다.
    foreach ($name in @($Environment.Keys)) {
        if ($AllowedNames -notcontains [string]$name) {
            throw "허용하지 않은 부모 환경이 남아 있어 자식 프로세스를 시작하지 않습니다."
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
        throw "포트 $RequestedPort 는 이미 사용 중입니다. 예: .\로컬데모켜기.ps1 -Port 8010"
    }
    finally {
        $listener.Stop()
    }
}

function Wait-ForLoopbackListener {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process,
        [int]$RequestedPort,
        [int]$TimeoutMilliseconds = 15000
    )

    $deadline = [System.DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    while ([System.DateTime]::UtcNow -lt $deadline) {
        if ($Process.HasExited) {
            return $false
        }
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $client.Connect("127.0.0.1", $RequestedPort)
            if ($client.Connected) {
                return $true
            }
        }
        catch [System.Net.Sockets.SocketException] {
            # 아직 import·시작 중일 수 있으므로 제한 시간 안에서 다시 확인한다.
        }
        finally {
            $client.Dispose()
        }
        Start-Sleep -Milliseconds 50
    }
    return $false
}

$appRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $appRoot ".."))
$python = $null
$pythonCandidates = @(
    (Join-Path $appRoot ".venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    # 현재 검수 작업환경이 쓰는 Python 3.13 공용 가상환경도 마지막 후보로 인정한다.
    (Join-Path $repoRoot ".venv313-backup-review\Scripts\python.exe")
)
foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $python = $candidate
        break
    }
}
if ($null -eq $python) {
    $pythonCommand = Get-Command "python" -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python을 찾지 못했습니다. app 폴더에서 Python 3.13 가상환경과 의존성을 먼저 준비해 주세요."
    }
    $python = $pythonCommand.Source
}

# capability를 만들거나 데모 폴더를 쓰기 전에 충돌을 알린다. 그러면 두 번째 실행의
# 오류 출력에 관리 주소가 나타나지 않고, 먼저 떠 있던 서버도 그대로 유지된다.
Assert-LoopbackPortAvailable -RequestedPort $Port

# 기존 서버 실행기의 실제 사용자 자료와 섞이지 않는 로컬 데모 전용 저장소다.
$demoRoot = Join-Path $appRoot ".local_demo"
$recordsDirectory = Join-Path $demoRoot "observability"
$tldextractCache = Join-Path $demoRoot "cache\tldextract"
$storageDatabase = Join-Path $demoRoot "storage.db"
$recordsPath = Join-Path $recordsDirectory "runs.jsonl"

New-Item -ItemType Directory -Force -Path $demoRoot | Out-Null
New-Item -ItemType Directory -Force -Path $recordsDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $tldextractCache | Out-Null

# 공개 프록시에서 Host를 localhost로 꾸며도 관리자 입구를 열 수 없도록, 이 실행에만
# 유효한 32바이트 capability를 만든다. 파일·현재 터미널 환경에는 저장하지 않는다.
$tokenBytes = New-Object byte[] 32
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $random.GetBytes($tokenBytes)
}
finally {
    $random.Dispose()
}
$localDemoAuthToken = [System.BitConverter]::ToString($tokenBytes).Replace("-", "").ToLowerInvariant()

# ProcessStartInfo의 환경 사본만 바꾼다. 현재 터미널의 환경변수나 비밀값은
# 조회·출력·변경하지 않으며, 아래 공급자 설정은 데모 서버 자식 프로세스에서만 뺀다.
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $python
$startInfo.WorkingDirectory = $appRoot
$startInfo.UseShellExecute = $false
$startInfo.Arguments = "-m uvicorn src.web.main:app --host 127.0.0.1 --port $Port --workers 1 --no-access-log"

$childEnvironment = Get-CompatibleChildEnvironment -StartInfo $startInfo
$childEnvironment = Reset-ChildEnvironmentToAllowlist `
    -Environment $childEnvironment `
    -AllowedNames $safeChildOsEnvironmentNames

# 부모 Python 관련 값(PYTHONPATH/PYTHONHOME 등)은 상속하지 않고, 한글 경로와 즉시
# 보이는 로그에 필요한 비민감 runtime 값만 실행기가 직접 정한다.
$childEnvironment["PYTHONUTF8"] = "1"
$childEnvironment["PYTHONIOENCODING"] = "utf-8"
$childEnvironment["PYTHONUNBUFFERED"] = "1"
$childEnvironment["PIPELINE"] = "demo"
$childEnvironment["BETA_ADMIN_ONLY"] = "0"
$childEnvironment["LOCAL_DEMO_AUTH"] = "1"
$childEnvironment["LOCAL_DEMO_AUTH_TOKEN"] = $localDemoAuthToken
$childEnvironment["AUTH_COOKIE_INSECURE"] = "1"
$childEnvironment["ADMIN_EMAILS"] = "local-demo-admin@example.invalid"
$childEnvironment["PORT"] = [string]$Port

$childEnvironment["APP_DATA_ROOT"] = $demoRoot
$childEnvironment["STORAGE_DB_PATH"] = $storageDatabase
$childEnvironment["OBSERVABILITY_RECORDS_PATH"] = $recordsPath
$childEnvironment["TLDEXTRACT_CACHE"] = $tldextractCache

# ★ 배포 신원(commit)이 없으면 출고 단계가 「캐시·출고에 쓸 수 없는 epoch」로 판정해
#   보고서를 끝내 만들지 못한다(실측: RuntimeError, 화면은 「오류가 났습니다」).
#   Render 는 RENDER_GIT_COMMIT 을 넣어 주지만 로컬에는 아무도 넣지 않는다.
#   그래서 현재 커밋을 여기서 직접 채운다. git 이 없거나 형식이 다르면 넣지 않는다
#   — 그때는 지금까지와 똑같이 동작한다(보고서 생성 불가).
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $appRoot ".."))
$repositoryCommit = ""
try {
    $repositoryCommit = (& git -C $repositoryRoot rev-parse HEAD 2>$null | Select-Object -First 1)
} catch {
    $repositoryCommit = ""
}
if (($repositoryCommit -is [string]) -and ($repositoryCommit -match '^[0-9a-f]{40}$')) {
    $childEnvironment["APP_GIT_COMMIT"] = $repositoryCommit
}

$childEnvironment.Remove("GOOGLE_CLIENT_ID")
$childEnvironment.Remove("GOOGLE_CLIENT_SECRET")
$childEnvironment.Remove("GOOGLE_REDIRECT_URI")
$childEnvironment.Remove("ANTHROPIC_API_KEY")
$childEnvironment.Remove("DART_API_KEY")
$childEnvironment.Remove("NAVER_CLIENT_ID")
$childEnvironment.Remove("NAVER_CLIENT_SECRET")
$childEnvironment.Remove("NOTION_TOKEN")
$childEnvironment.Remove("NOTION_PARENT_PAGE_ID")

$allowedChildEnvironmentNames = $safeChildOsEnvironmentNames + @(
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "PIPELINE",
    "BETA_ADMIN_ONLY",
    "LOCAL_DEMO_AUTH",
    "LOCAL_DEMO_AUTH_TOKEN",
    "AUTH_COOKIE_INSECURE",
    "ADMIN_EMAILS",
    "PORT",
    "APP_DATA_ROOT",
    "STORAGE_DB_PATH",
    "OBSERVABILITY_RECORDS_PATH",
    "TLDEXTRACT_CACHE",
    "APP_GIT_COMMIT"
)
foreach ($name in @($childEnvironment.Keys)) {
    if ($allowedChildEnvironmentNames -notcontains [string]$name) {
        throw "허용하지 않은 환경 '$name'이 감지되어 자식 프로세스를 시작하지 않습니다."
    }
}

$process = New-Object System.Diagnostics.Process
$process.StartInfo = $startInfo
$exitCode = 1
$started = $false
try {
    if (-not $process.Start()) {
        throw "로컬 데모 서버를 시작하지 못했습니다."
    }
    $started = $true
    if (-not (Wait-ForLoopbackListener -Process $process -RequestedPort $Port)) {
        if ($process.HasExited) {
            throw "서버가 시작 전에 종료되었습니다. Python 3.13 환경과 requirements 설치를 확인해 주세요."
        }
        throw "서버가 15초 안에 시작되지 않았습니다. 이 실행을 종료한 뒤 다시 시도해 주세요."
    }

    $url = "http://127.0.0.1:$Port"
    $loginUrl = "$url/auth/local-demo/start?token=$localDemoAuthToken"
    Write-Host ""
    Write-Host "로컬 무료 데모를 켰습니다: $url" -ForegroundColor Cyan
    Write-Host "관리자 로그인 주소(이 실행 중에만 유효):" -ForegroundColor Yellow
    Write-Host $loginUrl
    Write-Host "일반 로그인 화면에는 이 로컬 전용 입구가 표시되지 않습니다."
    Write-Host "주소를 화면 공유·문서·메신저·로그에 남기지 마세요."
    Write-Host "데모 기록은 app\.local_demo 안에만 저장됩니다."
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
}

if ($exitCode -ne 0) {
    throw "로컬 데모 서버가 오류로 종료되었습니다 (종료 코드: $exitCode)."
}
