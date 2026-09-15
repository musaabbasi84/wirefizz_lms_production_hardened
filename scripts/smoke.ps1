$ErrorActionPreference = "Stop"
Write-Host "Checking WireFizz LMS API..."
$r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/api/health
if ($r.StatusCode -ne 200) { throw "Health check failed: $($r.StatusCode)" }
$r.Content | Write-Host
Write-Host "API health OK"
