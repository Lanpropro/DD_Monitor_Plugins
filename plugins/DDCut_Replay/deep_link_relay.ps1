param([string]$Endpoint, [string]$Launch, [string]$Uri)
$ErrorActionPreference = 'Stop'
if (-not $Uri.StartsWith('ddcut://', [StringComparison]::OrdinalIgnoreCase)) { exit 1 }
$started = $false
$clock = [Diagnostics.Stopwatch]::StartNew()
while ($clock.Elapsed.TotalSeconds -lt 45) {
    try {
        $location = Get-Content -LiteralPath $Endpoint -Raw -Encoding UTF8 | ConvertFrom-Json
        $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:' + [int]$location.port + '/link')
        $request.Proxy = $null
        $request.Method = 'POST'
        $request.ContentType = 'application/json'
        $request.Timeout = 2000
        $request.ReadWriteTimeout = 2000
        $request.Headers.Add('X-DDCut-Key', [string]$location.token)
        $body = [Text.Encoding]::UTF8.GetBytes((@{uri=$Uri} | ConvertTo-Json -Compress))
        $request.ContentLength = $body.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($body, 0, $body.Length) } finally { $stream.Dispose() }
        $response = $request.GetResponse()
        try { if ([int]$response.StatusCode -eq 202) { exit 0 } } finally { $response.Dispose() }
    } catch [Net.WebException] {
        if ($_.Exception.Response) { $_.Exception.Response.Dispose(); exit 1 }
    } catch [System.IO.FileNotFoundException] {
    } catch [System.Management.Automation.ItemNotFoundException] {
    }
    if (-not $started) {
        if (-not (Test-Path -LiteralPath $Launch -PathType Leaf)) { exit 1 }
        $process = New-Object Diagnostics.ProcessStartInfo
        $process.FileName = $Launch
        $process.WorkingDirectory = [IO.Path]::GetDirectoryName($Launch)
        $process.UseShellExecute = $true
        $process.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
        [Diagnostics.Process]::Start($process) | Out-Null
        $started = $true
    }
    Start-Sleep -Milliseconds 200
}
exit 1
