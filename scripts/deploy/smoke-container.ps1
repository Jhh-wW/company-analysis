[CmdletBinding()]
param(
    [string]$Image = "company-analysis:release-readiness",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot "build-image.ps1") -Image $Image
}

$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$containerName = "company-analysis-smoke-$suffix"
$volumeName = "company-analysis-smoke-data-$suffix"
$started = $false

function Remove-SmokeResources {
    if ($script:started) {
        & docker rm --force $script:containerName 2>$null | Out-Null
    }
    & docker volume rm --force $script:volumeName 2>$null | Out-Null
}

function Start-SmokeContainer {
    & docker run --detach `
        --name $script:containerName `
        --network none `
        --read-only `
        --tmpfs "/tmp:rw,noexec,nosuid,size=268435456,uid=1000,gid=1000,mode=1770" `
        --cap-drop ALL `
        --security-opt no-new-privileges `
        --env PIPELINE=demo `
        --env BETA_ADMIN_ONLY=0 `
        --env PORT=10000 `
        --env APP_DATA_ROOT=/var/data `
        --env STORAGE_DB_PATH=/var/data/storage.db `
        --env OBSERVABILITY_RECORDS_PATH=/var/data/observability/runs.jsonl `
        --env TLDEXTRACT_CACHE=/var/data/cache/tldextract `
        --env GRACEFUL_SHUTDOWN_SECONDS=300 `
        --mount "type=volume,src=$($script:volumeName),dst=/var/data" `
        $Image | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "smoke 컨테이너를 시작하지 못했습니다."
    }
    $script:started = $true
}

function Wait-Ready {
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    do {
        & docker exec $script:containerName python -c `
            "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:10000/readyz', timeout=2); assert r.status == 200; assert json.load(r)['status'] in {'ready','degraded'}" `
            2>$null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    & docker logs $script:containerName
    throw "120초 안에 readiness가 통과하지 못했습니다."
}

try {
    & docker volume create $volumeName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "smoke 영속 볼륨을 만들지 못했습니다."
    }

    Start-SmokeContainer
    Wait-Ready
    & docker exec $containerName python /srv/deploy/verify_image.py
    if ($LASTEXITCODE -ne 0) {
        throw "이미지 비-root·금지 파일 검증에 실패했습니다."
    }
    & docker exec $containerName python -c `
        "from pathlib import Path; assert Path('/var/data/storage.db').is_file()"
    if ($LASTEXITCODE -ne 0) {
        throw "SQLite 파일이 영속 경로에 생기지 않았습니다."
    }

    & docker stop --time 330 $containerName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "SIGTERM 정상 종료에 실패했습니다."
    }
    & docker rm $containerName | Out-Null
    $started = $false

    Start-SmokeContainer
    Wait-Ready
    & docker exec $containerName python -c `
        "from pathlib import Path; assert Path('/var/data/storage.db').is_file()"
    if ($LASTEXITCODE -ne 0) {
        throw "재시작 뒤 영속 SQLite를 찾지 못했습니다."
    }

    Write-Host "비-root·readiness·영속 볼륨·SIGTERM smoke 완료: $Image"
}
finally {
    Remove-SmokeResources
}
