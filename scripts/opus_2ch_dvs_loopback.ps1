# 2-channel Opus loopback test using Dante Virtual Soundcard (DVS) ASIO.
#
# Reads channels 1 & 2 from DVS, encodes via Opus (family=255 over Ogg),
# sends over SRT to localhost, decodes, and outputs back to DVS channels 1 & 2.
# Because DVS ASIO is single-client, both TX and RX legs must run in a single
# GStreamer pipeline process.

param(
    [string]$GstBin = 'C:\Program Files\gstreamer\1.0\msvc_x86_64\bin',
    [int]$Port = 5005,
    [int]$BitrateKbps = 128,
    [int]$LatencyMs = 40,
    [switch]$DebugGst
)

$ErrorActionPreference = 'Stop'
$gst = Join-Path $GstBin 'gst-launch-1.0.exe'
if (-not (Test-Path $gst)) { throw "gst-launch-1.0.exe not found at $gst" }

if ($DebugGst) {
    $env:GST_DEBUG = "2,asiosrc:5,asiosink:5,opus*:4,ogg*:4,srt*:4,basesrc:3,basesink:3"
}

# DVS ASIO CLSID known to the project
$DvsClsid = "{B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8}"

# One combined pipeline:
# Leg 1 (TX): asiosrc -> audioconvert -> opusenc -> oggmux -> srtsink (caller)
# Leg 2 (RX): srtsrc (listener) -> oggdemux -> opusdec -> asiosink.
#
# This static hardware smoke test routes decoded RX audio directly into the
# output interleave. The app's dynamic spine uses per-channel mixers so RX legs
# can attach/detach at runtime, but that extra layer makes it harder to isolate
# whether DVS output is actually being driven.

$parts = @(
    # --- Shared DVS Capture Spine ---
    "asiosrc device-clsid=`"$DvsClsid`" input-channels=0,1 name=spine_asiosrc",
    "! audioconvert",
    "! audioresample",
    "! audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved,channel-mask=(bitmask)0x0",
    "! deinterleave name=spine_in",

    # Branch channel 0
    "spine_in.src_0 ! queue ! audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x0 ! level name=tx_lvl0 message=true interval=100000000 ! tee name=in0 allow-not-linked=true",
    "in0. ! queue ! tx_ic.sink_0",
    "in0. ! queue ! fakesink sync=false async=false",

    # Branch channel 1
    "spine_in.src_1 ! queue ! audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x0 ! level name=tx_lvl1 message=true interval=100000000 ! tee name=in1 allow-not-linked=true",
    "in1. ! queue ! tx_ic.sink_1",
    "in1. ! queue ! fakesink sync=false async=false",

    # --- TX Transport Leg ---
    "interleave name=tx_ic",
    "! audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved,channel-mask=(bitmask)0x0",
    "! audioconvert",
    "! opusenc bitrate=$($BitrateKbps * 1000)",
    "! oggmux max-delay=20000000 max-page-delay=20000000",
    "! srtsink name=srt_tx uri=`"srt://127.0.0.1:$($Port)?mode=caller&latency=$LatencyMs`" async=false wait-for-connection=false",

    # --- RX Transport Leg ---
    "srtsrc name=srt_rx uri=`"srt://127.0.0.1:$($Port)?mode=listener&latency=$LatencyMs`"",
    "! oggdemux ! opusdec",
    "! audioconvert",
    "! audioresample",
    "! audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved",
    "! deinterleave name=rx_d",

    # RX channel routing
    "rx_d.src_0 ! queue ! level name=rx_lvl0 message=true interval=100000000 ! audioconvert ! audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved,channel-mask=(bitmask)0x0 ! spine_out.sink_0",
    "rx_d.src_1 ! queue ! level name=rx_lvl1 message=true interval=100000000 ! audioconvert ! audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved,channel-mask=(bitmask)0x0 ! spine_out.sink_1",

    # --- Shared DVS Output Spine ---
    "interleave name=spine_out",
    "! level name=out_lvl message=true interval=100000000",
    "! audioconvert",
    "! audioresample",
    "! audio/x-raw,rate=48000,channels=2,layout=interleaved,channel-mask=(bitmask)0x0",
    "! asiosink device-clsid=`"$DvsClsid`" output-channels=0,1 name=spine_asiosink provide-clock=false sync=false async=false"
)

$cmd = $parts -join ' '

Write-Host "=== DVS Full-Duplex Loopback (2ch) ===" -ForegroundColor Cyan
Write-Host "Routing: DVS In 1/2 -> Opus/SRT -> DVS Out 1/2"
Write-Host $cmd
Write-Host "Press Ctrl+C to stop."
Write-Host "============================`n"

# Run in foreground
Start-Process -FilePath $gst -ArgumentList @('-m', $cmd) -NoNewWindow -Wait
