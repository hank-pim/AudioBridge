from __future__ import annotations

import math
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.requires_gstreamer


def _gst_launch() -> str | None:
    found = shutil.which("gst-launch-1.0")
    if found:
        return found
    if sys.platform == "win32":
        candidate = Path(r"C:\Program Files\gstreamer\1.0\msvc_x86_64\bin\gst-launch-1.0.exe")
        if candidate.is_file():
            return str(candidate)
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_gst(gst: str, description: str, stdout_path: Path, stderr_path: Path) -> subprocess.Popen[str]:
    stdout = stdout_path.open("w", encoding="utf-8", errors="replace")
    stderr = stderr_path.open("w", encoding="utf-8", errors="replace")
    try:
        return subprocess.Popen(
            [gst, "-m", *description.split()],
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
    finally:
        stdout.close()
        stderr.close()


def test_mpegts_opus_srt_8ch_loopback_preserves_discrete_channel_levels(tmp_path: Path) -> None:
    gst = _gst_launch()
    if gst is None:
        pytest.skip("gst-launch-1.0 is not installed")

    port = _free_port()
    duration_sec = 3
    tolerance_db = 5.0
    levels = [
        {"ch": 0, "volume": 0.501, "expected_dbfs": -6.0},
        {"ch": 1, "volume": 0.316, "expected_dbfs": -10.0},
        {"ch": 2, "volume": 0.200, "expected_dbfs": -14.0},
        {"ch": 3, "volume": 0.126, "expected_dbfs": -18.0},
        {"ch": 4, "volume": 0.0794, "expected_dbfs": -22.0},
        {"ch": 5, "volume": 0.0501, "expected_dbfs": -26.0},
        {"ch": 6, "volume": 0.0316, "expected_dbfs": -30.0},
        {"ch": 7, "volume": 0.0200, "expected_dbfs": -34.0},
    ]
    channel_count = len(levels)
    num_buffers = int((duration_sec * 48000) / 1024)

    rx_parts = [
        f"srtsrc uri=srt://127.0.0.1:{port}?mode=listener",
        "! tsdemux ! opusdec",
        f"! audio/x-raw,format=S16LE,rate=48000,channels={channel_count}",
        "! deinterleave name=d",
    ]
    for entry in levels:
        rx_parts.append(
            f"d.src_{entry['ch']} ! queue ! level name=lvl{entry['ch']} "
            "message=true interval=100000000 ! fakesink sync=false"
        )
    rx_description = " ".join(rx_parts)

    tx_parts = [
        "interleave name=fake_asio",
        f"! audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels={channel_count},channel-mask=(bitmask)0x0",
        "! deinterleave name=dante_in",
        "interleave name=tx_ic",
        f"! audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels={channel_count},channel-mask=(bitmask)0x0",
        "! audioconvert",
        "! opusenc bitrate=256000",
        "! mpegtsmux alignment=7 pat-interval=900 pmt-interval=900",
        f"! srtsink uri=srt://127.0.0.1:{port}?mode=caller async=false",
    ]
    for entry in levels:
        ch = entry["ch"]
        tx_parts.append(
            "audiotestsrc wave=sine freq=1000 "
            f"volume={entry['volume']} num-buffers={num_buffers} "
            "! audioconvert ! audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x0 "
            f"! fake_asio.sink_{ch}"
        )
        tx_parts.append(
            f"dante_in.src_{ch} ! queue ! level name=txlvl{ch} message=true interval=100000000 "
            f"! tee name=t{ch}"
        )
        tx_parts.append(f"t{ch}. ! queue ! tx_ic.sink_{ch}")
        tx_parts.append(f"t{ch}. ! queue ! fakesink sync=false")
    tx_description = " ".join(tx_parts)

    rx_out = tmp_path / "rx.out"
    rx_err = tmp_path / "rx.err"
    tx_out = tmp_path / "tx.out"
    tx_err = tmp_path / "tx.err"
    rx_proc = _run_gst(gst, rx_description, rx_out, rx_err)
    try:
        time.sleep(0.5)
        tx_proc = _run_gst(gst, tx_description, tx_out, tx_err)
        tx_returncode = tx_proc.wait(timeout=duration_sec + 10)
        assert tx_returncode == 0, tx_err.read_text(encoding="utf-8", errors="replace")
        time.sleep(1)
    finally:
        if rx_proc.poll() is None:
            rx_proc.terminate()
            try:
                rx_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                rx_proc.kill()
                rx_proc.wait(timeout=2)

    rx_text = rx_out.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(r'element "lvl(\d+)".*?rms=\(GValueArray\)<\s*([-0-9eE.,\s]+?)\s*>', rx_text)
    assert matches, rx_err.read_text(encoding="utf-8", errors="replace") or rx_text[:2048]

    per_channel: dict[int, list[float]] = {}
    for channel_text, values_text in matches:
        channel = int(channel_text)
        value = float(values_text.split(",")[0].strip())
        if math.isfinite(value):
            per_channel.setdefault(channel, []).append(value)

    measured: dict[int, float] = {}
    for entry in levels:
        channel = entry["ch"]
        samples = per_channel.get(channel, [])
        assert samples, f"ch{channel}: no level messages"
        stable = samples[2:] if len(samples) > 2 else samples
        average = sum(stable) / len(stable)
        measured[channel] = average
        assert abs(average - entry["expected_dbfs"]) <= tolerance_db

    for left in measured:
        for right in measured:
            if left >= right:
                continue
            assert abs(measured[left] - measured[right]) >= 1.0, (
                f"channels {left} and {right} collapsed near {measured[left]:.2f} dBFS"
            )
