# 8-channel Opus discrete-channel loopback test (channel-mapping-family=255).
#
# Sends 8 mono 1 kHz tones at distinct levels through opusenc -> oggmux ->
# udp -> oggdemux -> opusdec -> deinterleave, with a `level` element on each
# output channel. Parses level bus messages from the receiver and compares the
# measured RMS per channel against the expected dBFS.
#
# Why family=255: discrete channels with no surround-layout interpretation,
# so no channel gets treated as LFE and lowpassed. Carried over Ogg/Opus since
# RTP (rtpopuspay/rtpopusdepay) rejects family=255. SRT (or UDP for this
# loopback) carries the Ogg bytestream as-is.
#
# Pass criteria: every channel's measured RMS is within +/- TolDb of expected,
# AND no two channels collapse to the same level (would indicate downmix).

param(
    [string]$GstBin = 'C:\Program Files\gstreamer\1.0\msvc_x86_64\bin',
    [int]$DurationSec = 3,
    [int]$Port = 5004,
    [double]$TolDb = 5.0,
    [int]$BitrateKbps = 256
)

$ErrorActionPreference = 'Stop'
$gst = Join-Path $GstBin 'gst-launch-1.0.exe'
if (-not (Test-Path $gst)) { throw "gst-launch-1.0.exe not found at $gst" }

# Channel plan: same freq, distinct levels. With family=255 there's no LFE
# treatment, so every channel can carry the full 1 kHz tone.
$levels = @(
    @{ ch = 0; freq = 1000; volume = 0.501;  expectedDbfs = -6  },
    @{ ch = 1; freq = 1000; volume = 0.316;  expectedDbfs = -10 },
    @{ ch = 2; freq = 1000; volume = 0.200;  expectedDbfs = -14 },
    @{ ch = 3; freq = 1000; volume = 0.126;  expectedDbfs = -18 },
    @{ ch = 4; freq = 1000; volume = 0.0794; expectedDbfs = -22 },
    @{ ch = 5; freq = 1000; volume = 0.0501; expectedDbfs = -26 },
    @{ ch = 6; freq = 1000; volume = 0.0316; expectedDbfs = -30 },
    @{ ch = 7; freq = 1000; volume = 0.0200; expectedDbfs = -34 }
)
$N = $levels.Count

# --- Build receiver pipeline ---
# Ogg/Opus carries family=255 natively. oggdemux extracts Opus packets;
# opusdec hands us N-channel PCM with no LFE treatment.
$rxParts = @(
    "srtsrc uri=`"srt://127.0.0.1:$($Port)?mode=listener`"",
    "! oggdemux ! opusdec",
    "! audio/x-raw,format=S16LE,rate=48000,channels=$N",
    "! deinterleave name=d"
)
foreach ($e in $levels) {
    $rxParts += "d.src_$($e.ch) ! queue ! level name=lvl$($e.ch) message=true interval=100000000 ! fakesink sync=false"
}
$rxCmd = $rxParts -join ' '

# --- Build sender pipeline ---
# num-buffers chosen so the sender runs ~DurationSec at 48000/1024 samples/buf.
$numBuffers = [int](($DurationSec * 48000) / 1024)

# Emulate the shared dante_in -> tee -> tx_ic topology
$txParts = @(
    # 1) Fake ASIO source (collapses 8 test tones into one interleaved stream)
    "interleave name=fake_asio",
    "! audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=$N,channel-mask=(bitmask)0x0",
    
    # 2) Shared deinterleave (emulates dante_in_shared)
    "! deinterleave name=dante_in",
    
    # 3) TX transport interleave (per-transport)
    "interleave name=tx_ic",
    "! audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=$N,channel-mask=(bitmask)0x0",
    "! audioconvert",
    "! opusenc bitrate=$($BitrateKbps * 1000)",
    "! oggmux max-delay=20000000 max-page-delay=20000000 ! srtsink uri=`"srt://127.0.0.1:$($Port)?mode=caller`""
)
foreach ($e in $levels) {
    # Generate the source and feed the fake asio
    $txParts += "audiotestsrc wave=sine freq=$($e.freq) volume=$($e.volume) num-buffers=$numBuffers ! audioconvert ! audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x0 ! fake_asio.sink_$($e.ch)"
    
    # Route from dante_in -> level -> tee -> tx_ic
    $txParts += "dante_in.src_$($e.ch) ! queue ! level name=txlvl$($e.ch) message=true interval=100000000 ! tee name=t$($e.ch)"
    $txParts += "t$($e.ch). ! queue ! tx_ic.sink_$($e.ch)"
    $txParts += "t$($e.ch). ! queue ! fakesink sync=false"
}
$txCmd = $txParts -join ' '

Write-Host "=== Receiver ===" -ForegroundColor Cyan
Write-Host $rxCmd
Write-Host ""
Write-Host "=== Sender ===" -ForegroundColor Cyan
Write-Host $txCmd
Write-Host ""

# --- Run receiver in background, capturing stdout ---
$rxOutFile = Join-Path $env:TEMP "opus8_rx_$PID.log"
$rxErrFile = Join-Path $env:TEMP "opus8_rxerr_$PID.log"
$rxProc = Start-Process -FilePath $gst `
    -ArgumentList @('-m', $rxCmd) `
    -NoNewWindow -PassThru `
    -RedirectStandardOutput $rxOutFile `
    -RedirectStandardError $rxErrFile

Start-Sleep -Milliseconds 500  # let receiver bind the port

# --- Run sender in foreground ---
Write-Host "Running sender for ~$DurationSec sec..."
$txProc = Start-Process -FilePath $gst `
    -ArgumentList @('-m', $txCmd) `
    -NoNewWindow -PassThru -Wait

# Give receiver a moment to drain.
Start-Sleep -Seconds 1
if (-not $rxProc.HasExited) { Stop-Process -Id $rxProc.Id -Force }

# --- Parse level messages ---
$rxOut = Get-Content $rxOutFile -Raw
if (-not $rxOut) { Write-Host "Receiver produced no output. Stderr:" -ForegroundColor Red; Get-Content $rxErrFile; exit 1 }

$pattern = 'element "lvl(\d+)".*?rms=\(GValueArray\)<\s*([-0-9eE.,\s]+?)\s*>'
$matches = [regex]::Matches($rxOut, $pattern)
if ($matches.Count -eq 0) {
    Write-Host "No level messages parsed from receiver output." -ForegroundColor Red
    Write-Host "First 2KB of receiver stdout:"
    Write-Host $rxOut.Substring(0, [Math]::Min(2048, $rxOut.Length))
    Write-Host "Stderr:"; Get-Content $rxErrFile
    exit 1
}

# Aggregate: skip the first 2 messages per channel (startup transient) and average.
$perCh = @{}
foreach ($m in $matches) {
    $ch = [int]$m.Groups[1].Value
    $vals = $m.Groups[2].Value -split ',' | ForEach-Object { [double]$_.Trim() }
    if (-not $perCh.ContainsKey($ch)) { $perCh[$ch] = New-Object System.Collections.Generic.List[double] }
    $perCh[$ch].Add($vals[0])
}

Write-Host ""
Write-Host "=== Results ===" -ForegroundColor Cyan
$pass = $true
$measured = @{}
foreach ($e in $levels) {
    $ch = $e.ch
    if (-not $perCh.ContainsKey($ch)) {
        Write-Host ("ch{0}: NO DATA  (expected {1} dBFS)" -f $ch, $e.expectedDbfs) -ForegroundColor Red
        $pass = $false
        continue
    }
    $samples = $perCh[$ch]
    if ($samples.Count -le 2) {
        Write-Host ("ch{0}: only {1} samples" -f $ch, $samples.Count) -ForegroundColor Yellow
        $avg = ($samples | Measure-Object -Average).Average
    } else {
        $trimmed = $samples | Select-Object -Skip 2
        $avg = ($trimmed | Measure-Object -Average).Average
    }
    $measured[$ch] = $avg
    $delta = $avg - $e.expectedDbfs
    $ok = [Math]::Abs($delta) -le $TolDb
    $color = if ($ok) { 'Green' } else { 'Red' }
    $deltaStr = if ($delta -ge 0) { '+{0:F2}' -f $delta } else { '{0:F2}' -f $delta }
    Write-Host ("ch{0}: measured {1,7:F2} dBFS  expected {2,4} dBFS  delta {3,7}  msgs={4}" `
        -f $ch, $avg, $e.expectedDbfs, $deltaStr, $samples.Count) -ForegroundColor $color
    if (-not $ok) { $pass = $false }
}

# Collapse check: any two channels within 1 dB of each other = bad.
$chList = $measured.Keys | Sort-Object
for ($i = 0; $i -lt $chList.Count; $i++) {
    for ($j = $i + 1; $j -lt $chList.Count; $j++) {
        $a = $chList[$i]; $b = $chList[$j]
        if ([Math]::Abs($measured[$a] - $measured[$b]) -lt 1.0) {
            Write-Host ("COLLAPSE: ch{0} and ch{1} both measure ~{2:F2} dBFS" -f $a, $b, $measured[$a]) -ForegroundColor Red
            $pass = $false
        }
    }
}

Write-Host ""
if ($pass) {
    Write-Host "PASS - 8-channel discrete Opus (family=255) decode verified." -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL - see deltas above." -ForegroundColor Red
    Write-Host "Receiver log: $rxOutFile"
    Write-Host "Receiver err: $rxErrFile"
    exit 1
}
