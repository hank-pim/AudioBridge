# Single-channel latency probe: how long after sender starts does the
# receiver emit its first level message? Same machine, localhost UDP.
# Compares family=0/RTP/Opus vs family=255/Ogg/Opus paths.

param(
    [string]$GstBin = 'C:\Program Files\gstreamer\1.0\msvc_x86_64\bin',
    [int]$Iterations = 3
)

$ErrorActionPreference = 'Stop'
$gst = Join-Path $GstBin 'gst-launch-1.0.exe'
$tmp = $env:TEMP

function Measure-First-Level {
    param([string]$Name, [string]$RxCmd, [string]$TxCmd)
    $log = Join-Path $tmp "lat_$Name.log"
    Remove-Item $log -ErrorAction SilentlyContinue
    $rxp = Start-Process -FilePath $gst -ArgumentList @('-m', $RxCmd) `
        -NoNewWindow -PassThru -RedirectStandardOutput $log `
        -RedirectStandardError ([System.IO.Path]::GetTempFileName())
    Start-Sleep -Milliseconds 1500   # let receiver get to PLAYING

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $txp = Start-Process -FilePath $gst -ArgumentList @($TxCmd) `
        -NoNewWindow -PassThru `
        -RedirectStandardOutput ([System.IO.Path]::GetTempFileName()) `
        -RedirectStandardError ([System.IO.Path]::GetTempFileName())

    $found = $null
    while ($sw.ElapsedMilliseconds -lt 8000) {
        $c = Get-Content $log -Raw -ErrorAction SilentlyContinue
        if ($c -and $c -match 'lvl0.*rms') { $found = [int]$sw.ElapsedMilliseconds; break }
        Start-Sleep -Milliseconds 5
    }
    Stop-Process -Id $txp.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $rxp.Id -Force -ErrorAction SilentlyContinue
    return $found
}

$portRtp = 5010
$portOgg = 5011

$rxRtp = "udpsrc port=$portRtp caps=`"application/x-rtp,media=audio,clock-rate=48000,encoding-name=OPUS,encoding-params=(string)1,payload=96`" ! rtpjitterbuffer latency=20 ! rtpopusdepay ! opusdec ! level name=lvl0 message=true interval=20000000 ! fakesink sync=false"
$txRtp = "audiotestsrc wave=sine freq=1000 ! audioconvert ! audio/x-raw,format=S16LE,rate=48000,channels=1 ! opusenc bitrate=64000 ! rtpopuspay pt=96 ! udpsink host=127.0.0.1 port=$portRtp"

$rxOgg = "udpsrc port=$portOgg ! oggdemux ! opusdec ! level name=lvl0 message=true interval=20000000 ! fakesink sync=false"
$txOgg = "audiotestsrc wave=sine freq=1000 ! audioconvert ! audio/x-raw,format=S16LE,rate=48000,channels=1 ! opusenc bitrate=64000 ! oggmux max-delay=20000000 max-page-delay=20000000 ! udpsink host=127.0.0.1 port=$portOgg"

$rtpResults = @()
$oggResults = @()
for ($i = 1; $i -le $Iterations; $i++) {
    $r = Measure-First-Level -Name "rtp_$i" -RxCmd $rxRtp -TxCmd $txRtp
    Start-Sleep -Milliseconds 300
    $o = Measure-First-Level -Name "ogg_$i" -RxCmd $rxOgg -TxCmd $txOgg
    Start-Sleep -Milliseconds 300
    Write-Host ("iter {0}: rtp = {1,4} ms   ogg = {2,4} ms" -f $i, $r, $o)
    if ($r) { $rtpResults += $r }
    if ($o) { $oggResults += $o }
}

Write-Host ""
if ($rtpResults.Count -gt 0) {
    $rtpAvg = ($rtpResults | Measure-Object -Average).Average
    Write-Host ("RTP avg: {0:F0} ms (n={1})" -f $rtpAvg, $rtpResults.Count)
}
if ($oggResults.Count -gt 0) {
    $oggAvg = ($oggResults | Measure-Object -Average).Average
    Write-Host ("Ogg avg: {0:F0} ms (n={1})" -f $oggAvg, $oggResults.Count)
}
