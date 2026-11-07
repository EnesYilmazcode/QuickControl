$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$uv = (Get-Command uv -ErrorAction Stop).Source
New-Item -ItemType Directory -Path (Join-Path $root 'artifacts') -Force | Out-Null
$times = @()
for ($round=0; $round -lt 1; $round++) {
    for ($i=0; $i -lt 3; $i++) {
        $watch = [Diagnostics.Stopwatch]::StartNew()
        & $uv run --with mcp python "$root\quickcontrol.py" Screenshot "@$root\screenshot.json" --output "$root\artifacts" 1> "$root\artifacts\single-$round-$i.log" 2> "$root\artifacts\single-$round-$i.err"
        $code = $LASTEXITCODE
        $watch.Stop()
        $times += @{mode='single'; round=$round; ms=$watch.ElapsedMilliseconds; exit_code=$code}
    }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    & $uv run --with mcp python "$root\quickcontrol.py" --batch "@$root\benchmark.json" --output "$root\artifacts" 1> "$root\artifacts\batch-$round.log" 2> "$root\artifacts\batch-$round.err"
    $code = $LASTEXITCODE
    $watch.Stop()
    $times += @{mode='batch-three-screenshots-plus-status'; round=$round; ms=$watch.ElapsedMilliseconds; exit_code=$code}
    $times | ConvertTo-Json | Set-Content -LiteralPath "$root\fallback-comparison.json" -Encoding UTF8
}
$times | ConvertTo-Json
if (@($times | Where-Object exit_code -ne 0).Count) { exit 1 }
