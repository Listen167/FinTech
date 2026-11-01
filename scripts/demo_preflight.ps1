[CmdletBinding()]
param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$FrontendUrl = "http://127.0.0.1:5173",
    [switch]$Strict
)

$ErrorActionPreference = "Stop"
$checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
    param(
        [string]$Name,
        [ValidateSet("PASS", "WARN", "FAIL")]
        [string]$Status,
        [string]$Detail
    )
    $checks.Add([pscustomobject]@{
        Check = $Name
        Status = $Status
        Detail = $Detail
    })
}

try {
    $health = Invoke-RestMethod -Uri "$ApiBaseUrl/api/health" -TimeoutSec 5
    if ($health.status -eq "ok") {
        Add-Check "Backend API" "PASS" "health=ok"
    } else {
        Add-Check "Backend API" "FAIL" "health=$($health.status)"
    }

    if ($health.persistence.backend -eq "mysql" -and $health.persistence.connected) {
        Add-Check "MySQL" "PASS" "Persistent storage is connected"
    } else {
        Add-Check "MySQL" "WARN" "backend=$($health.persistence.backend); MySQL is recommended for the demo"
    }

    if ($health.integrations.feishu.configured) {
        Add-Check "Feishu config" "PASS" "Credentials are configured"
    } else {
        Add-Check "Feishu config" "WARN" "Credentials are not fully configured"
    }
} catch {
    Add-Check "Backend API" "FAIL" "Cannot reach $ApiBaseUrl/api/health"
}

try {
    $model = Invoke-RestMethod -Uri "$ApiBaseUrl/api/agent/model-status" -TimeoutSec 5
    if ($model.ready) {
        Add-Check "DeepSeek" "PASS" "$($model.model) is ready"
    } else {
        Add-Check "DeepSeek" "WARN" "Model is not ready; deterministic fallback will be used"
    }
} catch {
    Add-Check "DeepSeek" "WARN" "Cannot read model status"
}

try {
    $openapi = Invoke-RestMethod -Uri "$ApiBaseUrl/openapi.json" -TimeoutSec 5
    if ($openapi.openapi -and $openapi.info.version) {
        Add-Check "OpenAPI" "PASS" "OpenAPI $($openapi.openapi), API $($openapi.info.version)"
    } else {
        Add-Check "OpenAPI" "FAIL" "Contract version is missing"
    }
} catch {
    Add-Check "OpenAPI" "FAIL" "Cannot reach the runtime contract"
}

try {
    $frontend = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 5
    if ($frontend.StatusCode -ge 200 -and $frontend.StatusCode -lt 400) {
        Add-Check "Frontend" "PASS" "HTTP $($frontend.StatusCode)"
    } else {
        Add-Check "Frontend" "FAIL" "HTTP $($frontend.StatusCode)"
    }
} catch {
    Add-Check "Frontend" "FAIL" "Cannot reach $FrontendUrl"
}

try {
    $workers = @(
        Get-CimInstance Win32_Process | Where-Object {
            $_.CommandLine -match 'python(?:\.exe)?"?\s+-m\s+backend\.app\.integrations\.feishu_long_connection'
        }
    )
    if ($workers.Count -eq 1) {
        Add-Check "Feishu worker" "PASS" "1 process, PID=$($workers[0].ProcessId)"
    } elseif ($workers.Count -eq 0) {
        Add-Check "Feishu worker" "WARN" "No long-connection worker detected"
    } else {
        Add-Check "Feishu worker" "FAIL" "$($workers.Count) workers detected; duplicate consumption risk"
    }
} catch {
    Add-Check "Feishu worker" "WARN" "Cannot inspect processes; verify the worker log"
}

$checks | Format-Table -AutoSize
$failed = @($checks | Where-Object { $_.Status -eq "FAIL" }).Count
$warnings = @($checks | Where-Object { $_.Status -eq "WARN" }).Count

Write-Host ""
Write-Host "Summary: PASS=$(@($checks | Where-Object { $_.Status -eq 'PASS' }).Count) WARN=$warnings FAIL=$failed"

if ($failed -gt 0 -or ($Strict -and $warnings -gt 0)) {
    exit 1
}
exit 0
