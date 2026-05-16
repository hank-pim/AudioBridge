from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import audio_devices
from app.services import media as media_module


@pytest.fixture(autouse=True)
def isolated_config_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DANTEBRIDGE_CONFIG_PATH", str(tmp_path / "endpoint.toml"))


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.stdout = iter(())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_monitor_then_tone_lifecycle(monkeypatch) -> None:
    spawned: list[tuple[list[str], FakeProcess]] = []

    def fake_popen(argv, **kwargs):
        process = FakeProcess(pid=1000 + len(spawned))
        spawned.append((argv, process))
        return process

    monkeypatch.setattr(media_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(media_module.MediaController, "_gst_supports_element", lambda self, _: False)

    with TestClient(create_app()) as client:
        monitor_response = client.post("/api/diagnostics/monitor", json={})
        assert monitor_response.status_code == 200
        monitor_body = monitor_response.json()
        assert monitor_body["monitor"] == "running"
        assert monitor_body["transport"] == {"host": None, "port": 9000, "srt_mode": "listener"}
        assert spawned[0][0][2] == "srtsrc"
        assert any(arg == "uri=srt://:9000?mode=listener&latency=240" for arg in spawned[0][0])

        tone_response = client.post(
            "/api/diagnostics/tone",
            json={"frequency_hz": 440, "level_dbfs": -12, "waveform": "square"},
        )
        assert tone_response.status_code == 200
        tone_body = tone_response.json()
        assert tone_body["tone"] == "running"
        assert tone_body["transport"] == {"host": "127.0.0.1", "port": 9000, "srt_mode": "caller"}
        assert spawned[1][0][2] == "audiotestsrc"
        assert any(arg == "uri=srt://127.0.0.1:9000?mode=caller&latency=240" for arg in spawned[1][0])

        tone_stop = client.post("/api/diagnostics/tone/stop")
        monitor_stop = client.delete("/api/diagnostics/monitor")
        assert tone_stop.status_code == 200
        assert monitor_stop.status_code == 200
        assert spawned[1][1].terminated is True
        assert spawned[0][1].terminated is True


def test_rejects_second_tone_start(monkeypatch) -> None:
    def fake_popen(argv, **kwargs):
        return FakeProcess(pid=2001)

    monkeypatch.setattr(media_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(media_module.MediaController, "_gst_supports_element", lambda self, _: False)

    with TestClient(create_app()) as client:
        first = client.post("/api/diagnostics/tone", json={})
        second = client.post("/api/diagnostics/tone", json={})

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["detail"] == "tone pipeline is already running"


def test_audio_interfaces_endpoint_reports_discovered_devices(monkeypatch) -> None:
    discovered = [
        {
            "id": "gst:wasapi:input-dvs",
            "name": "Dante Virtual Soundcard",
            "driver": "wasapi",
            "direction": "duplex",
            "sample_rate": 48000,
            "source": "gstreamer",
        },
        {
            "id": "gst:wasapi:headphones",
            "name": "Headphones",
            "driver": "wasapi",
            "direction": "output",
            "sample_rate": 48000,
            "source": "gstreamer",
        },
    ]
    monkeypatch.setattr(media_module.MediaController, "discover_audio_interfaces", lambda self: discovered)

    with TestClient(create_app()) as client:
        select = client.post("/api/interfaces/audio", json={"name": "Dante Virtual Soundcard", "channel_count": 128})
        assert select.status_code == 200
        assert select.json()["interface_name"] == "Dante Virtual Soundcard"
        assert select.json()["interface_driver"] == "wasapi"
        assert select.json()["channel_count"] == 64

        response = client.get("/api/interfaces/audio")
        assert response.status_code == 200
        body = response.json()
        assert body["selected"]["name"] == "Dante Virtual Soundcard"
        assert body["interfaces"][0]["selected"] is True
        assert body["interfaces"][1]["selected"] is False


def test_audio_interface_selection_accepts_device_id_and_normalizes_unknown_driver(monkeypatch) -> None:
    monkeypatch.setattr(media_module.MediaController, "discover_audio_interfaces", lambda self: [
        {
            "id": "gst:pulse:studio-capture",
            "name": "Studio Capture",
            "driver": "pulseaudio",
            "direction": "input",
            "sample_rate": 48000,
            "source": "gstreamer",
        },
    ])

    with TestClient(create_app()) as client:
        response = client.post("/api/interfaces/audio", json={"name": "gst:pulse:studio-capture", "channel_count": 4})

        assert response.status_code == 200
        assert response.json()["interface_name"] == "Studio Capture"
        assert response.json()["interface_driver"] == "unknown"
        assert response.json()["channel_count"] == 4


def test_gstreamer_audio_device_monitor_output_is_parsed() -> None:
    output = """
Device found:

    name  : Dante Virtual Soundcard
    class : Audio/Source
    caps  : audio/x-raw, rate=(int)48000, channels=(int)[ 1, 64 ]
    properties:
        device.api = wasapi
        device.id = DVS
        device.description = Dante Virtual Soundcard
    gst-launch-1.0 wasapisrc device=DVS

Device found:

    name  : Dante Virtual Soundcard
    class : Audio/Sink
    caps  : audio/x-raw, rate=(int)48000, channels=(int)64
    properties:
        device.api = wasapi
        device.id = DVS
    gst-launch-1.0 wasapisink device=DVS
"""

    devices = audio_devices._parse_gst_device_monitor(output)

    assert devices == [
        {
            "id": "gst:wasapi:input-dvs",
            "name": "Dante Virtual Soundcard",
            "driver": "wasapi",
            "direction": "input",
            "sample_rate": 48000,
            "device_id": "DVS",
            "gst_class": "Audio/Source",
            "gst_launch": "gst-launch-1.0 wasapisrc device=DVS",
            "source": "gstreamer",
        },
        {
            "id": "gst:wasapi:output-dvs",
            "name": "Dante Virtual Soundcard",
            "driver": "wasapi",
            "direction": "output",
            "sample_rate": 48000,
            "device_id": "DVS",
            "gst_class": "Audio/Sink",
            "gst_launch": "gst-launch-1.0 wasapisink device=DVS",
            "source": "gstreamer",
        },
    ]


def test_gstreamer_audio_device_monitor_keeps_asio_source_and_sink_separate() -> None:
    output = """
Device found:

    name  : Dante Virtual Soundcard (x64)
    class : Audio/Source
    caps  : audio/x-raw
    gst-launch-1.0 asiosrc device-clsid='{B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8}' ! ...

Device found:

    name  : Dante Virtual Soundcard (x64)
    class : Audio/Sink
    caps  : audio/x-raw
    gst-launch-1.0 ... ! asiosink device-clsid='{B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8}'
"""

    devices = audio_devices._parse_gst_device_monitor(output)

    assert [device["driver"] for device in devices] == ["asio", "asio"]
    assert [device["direction"] for device in devices] == ["input", "output"]
    assert [device["device_id"] for device in devices] == [
        "B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
        "B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
    ]


def test_media_controller_falls_back_to_standard_windows_gstreamer_path(monkeypatch) -> None:
    expected = "C:/Program Files/gstreamer/1.0/msvc_x86_64/bin/gst-launch-1.0.exe"

    monkeypatch.setattr(media_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(media_module.Path, "is_file", lambda self: str(self).replace('\\', '/') == expected)

    controller = media_module.MediaController(telemetry=media_module.TelemetryService())

    assert controller.gst_launch_executable.replace('\\', '/') == expected


def test_program_start_uses_listener_pipeline(monkeypatch) -> None:
    spawned: list[tuple[list[str], FakeProcess]] = []

    def fake_popen(argv, **kwargs):
        process = FakeProcess(pid=3000 + len(spawned))
        spawned.append((argv, process))
        return process

    monkeypatch.setattr(media_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(media_module.MediaController, "_gst_supports_element", lambda self, _: False)

    with TestClient(create_app()) as client:
        patch_response = client.patch("/api/config", json={"program": {"srt_mode": "listener"}})
        assert patch_response.status_code == 200

        response = client.post("/api/program/start", json={})

        assert response.status_code == 200
        body = response.json()
        assert body["program"] == "running"
        assert body["pipeline"]["kind"] == "receiver"
        assert body["pipeline"]["srt_mode"] == "listener"
        assert any(arg == "uri=srt://:9000?mode=listener&latency=240" for arg in spawned[0][0])

        stop_response = client.post("/api/program/stop")
        assert stop_response.status_code == 200
        assert spawned[0][1].terminated is True


def test_program_start_uses_tone_sender_for_caller_mode(monkeypatch) -> None:
    spawned: list[tuple[list[str], FakeProcess]] = []

    def fake_popen(argv, **kwargs):
        process = FakeProcess(pid=4000 + len(spawned))
        spawned.append((argv, process))
        return process

    monkeypatch.setattr(media_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(media_module.MediaController, "_gst_supports_element", lambda self, _: False)

    with TestClient(create_app()) as client:
        patch_response = client.patch("/api/config", json={"program": {"srt_mode": "caller"}})
        assert patch_response.status_code == 200

        response = client.post(
            "/api/program/start",
            json={"host": "192.0.2.55", "frequency_hz": 440, "level_dbfs": -12, "waveform": "square"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["program"] == "running"
        assert body["pipeline"]["kind"] == "tone_sender"
        assert body["pipeline"]["host"] == "192.0.2.55"
        assert body["pipeline"]["tone"] == {"frequency_hz": 440.0, "level_dbfs": -12.0, "waveform": "square"}
        assert any(arg == "uri=srt://192.0.2.55:9000?mode=caller&latency=240" for arg in spawned[0][0])


def test_program_start_requires_host_for_caller_mode(monkeypatch) -> None:
    monkeypatch.setattr(media_module.subprocess, "Popen", lambda argv, **kwargs: FakeProcess(pid=5000))
    monkeypatch.setattr(media_module.MediaController, "_gst_supports_element", lambda self, _: False)

    with TestClient(create_app()) as client:
        patch_response = client.patch("/api/config", json={"program": {"srt_mode": "caller"}})
        assert patch_response.status_code == 200

        response = client.post("/api/program/start", json={})

        assert response.status_code == 409
        assert response.json()["detail"] == "program start requires host when srt_mode is caller or rendezvous"


def test_srt_transport_lifecycle_updates_only_selected_transport(monkeypatch) -> None:
    spawned: list[tuple[str, str, str]] = []

    class FakeManagedPipeline:
        def __init__(self, name: str, graph: str, srt_element_name: str) -> None:
            self.name = name
            self.graph = graph
            self.srt_element_name = srt_element_name
            self.stopped = False

        def describe(self, *, include_output_tail: bool = True):
            return {
                "name": self.name,
                "pid": None,
                "argv": ["managed-gstreamer", self.graph],
                "running": not self.stopped,
                "returncode": None,
                "engine": "ctypes-gstreamer",
            }

        def stop(self) -> None:
            self.stopped = True

    def fake_spawn_managed(self, *, name, graph, srt_element_name, transport_id):
        spawned.append((graph, srt_element_name, transport_id))
        return FakeManagedPipeline(name, graph, srt_element_name)

    monkeypatch.setattr(media_module.MediaController, "_spawn_managed_gst_pipeline", fake_spawn_managed)

    with TestClient(create_app()) as client:
        first_transport = {
            "id": "tx-a",
            "name": "Transport A",
            "direction": "rx",
            "mode": "listener",
            "port": 9100,
        }
        second_transport = {
            "id": "tx-b",
            "name": "Transport B",
            "direction": "rx",
            "mode": "listener",
            "port": 9200,
        }

        assert client.post("/api/srt-transports", json=first_transport).status_code == 200
        assert client.post("/api/srt-transports", json=second_transport).status_code == 200

        start_response = client.post("/api/srt-transports/tx-a/start", json={})
        assert start_response.status_code == 200
        graph, srt_element_name, transport_id = spawned[0]
        assert transport_id == "tx-a"
        assert srt_element_name == "srtstats_rx_tx_a"
        assert "uri=srt://:9100?mode=listener&latency=240" in graph
        assert "tee name=monitor_tap_rx_tx_a allow-not-linked=true" in graph

        status = client.get("/api/status")
        assert status.status_code == 200
        body = status.json()
        state_by_id = {item["id"]: item["state"] for item in body["srt_transports"]}
        assert state_by_id["tx-a"] == "running"
        assert state_by_id["tx-b"] == "stopped"

        stop_response = client.post("/api/srt-transports/tx-a/stop")
        assert stop_response.status_code == 200


def test_media_pipelines_endpoint_exposes_running_pipeline(monkeypatch) -> None:
    spawned: list[tuple[str, str, str]] = []

    class FakeManagedPipeline:
        def __init__(self, name: str, graph: str, srt_element_name: str) -> None:
            self.name = name
            self.graph = graph
            self.srt_element_name = srt_element_name
            self.stopped = False

        def describe(self, *, include_output_tail: bool = True):
            return {
                "name": self.name,
                "pid": None,
                "argv": ["managed-gstreamer", self.graph],
                "running": not self.stopped,
                "returncode": None,
                "engine": "ctypes-gstreamer",
            }

        def stop(self) -> None:
            self.stopped = True

    def fake_spawn_managed(self, *, name, graph, srt_element_name, transport_id):
        spawned.append((graph, srt_element_name, transport_id))
        return FakeManagedPipeline(name, graph, srt_element_name)

    monkeypatch.setattr(media_module.MediaController, "_spawn_managed_gst_pipeline", fake_spawn_managed)

    with TestClient(create_app()) as client:
        transport = {
            "id": "tx-a",
            "name": "Transport A",
            "direction": "rx",
            "mode": "listener",
            "port": 9100,
        }

        assert client.post("/api/srt-transports", json=transport).status_code == 200
        assert client.post("/api/srt-transports/tx-a/start", json={}).status_code == 200

        pipelines_response = client.get("/api/media/pipelines")
        assert pipelines_response.status_code == 200
        pipelines = pipelines_response.json()
        assert len(pipelines) == 1
        assert pipelines[0]["name"] == "srt_transport_tx-a_rx"
        assert pipelines[0]["running"] is True
        assert pipelines[0]["pid"] is None
        assert pipelines[0]["engine"] == "ctypes-gstreamer"


def test_monitor_rejects_tx_transport(monkeypatch) -> None:
    monkeypatch.setattr(media_module.subprocess, "Popen", lambda argv, **kwargs: FakeProcess(pid=6200))
    monkeypatch.setattr(media_module.MediaController, "_gst_supports_element", lambda self, _: False)

    with TestClient(create_app()) as client:
        transport = {
            "id": "tx-only",
            "name": "TX only",
            "direction": "tx",
            "mode": "caller",
            "host": "192.0.2.10",
            "port": 9300,
        }

        assert client.post("/api/srt-transports", json=transport).status_code == 200

        response = client.post("/api/diagnostics/monitor", json={"transport_id": "tx-only"})

        assert response.status_code == 409
        assert response.json()["detail"] == "local monitor is only available for RX SRT transports; 'tx-only' is tx"


def test_monitor_requires_host_for_caller_mode(monkeypatch) -> None:
    monkeypatch.setattr(media_module.subprocess, "Popen", lambda argv, **kwargs: FakeProcess(pid=6300))
    monkeypatch.setattr(media_module.MediaController, "_gst_supports_element", lambda self, _: False)

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/diagnostics/monitor",
            json={"srt_mode": "caller", "port": 9300},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "SRT mode 'caller' requires a host"


def test_webrtc_start_reports_unimplemented_runtime() -> None:
    with TestClient(create_app()) as client:
        stream = {
            "id": "rtc-a",
            "name": "Talkback A",
            "direction": "rx",
        }

        assert client.post("/api/webrtc-streams", json=stream).status_code == 200

        response = client.post("/api/webrtc-streams/rtc-a/start")

        assert response.status_code == 409
        assert response.json()["detail"] == "WebRTC media runtime is not implemented yet for stream 'rtc-a'; use SRT transport monitoring instead"


def test_webrtc_streams_remain_stopped_without_runtime() -> None:
    with TestClient(create_app()) as client:
        first_stream = {
            "id": "wb-a",
            "name": "Talkback A",
            "direction": "tx",
            "source_id": "mic-a",
        }
        second_stream = {
            "id": "wb-b",
            "name": "Talkback B",
            "direction": "rx",
        }

        assert client.post("/api/webrtc-streams", json=first_stream).status_code == 200
        assert client.post("/api/webrtc-streams", json=second_stream).status_code == 200

        start_response = client.post("/api/webrtc-streams/wb-a/start")
        assert start_response.status_code == 409

        status = client.get("/api/status")
        assert status.status_code == 200
        body = status.json()
        state_by_id = {item["id"]: item["state"] for item in body["webrtc_streams"]}
        assert state_by_id == {"wb-a": "stopped", "wb-b": "stopped"}

        stop_response = client.post("/api/webrtc-streams/wb-a/stop")
        assert stop_response.status_code == 200


def test_encode_group_crud_persists_channel_map() -> None:
    with TestClient(create_app()) as client:
        source = {
            "id": "src-1",
            "name": "Dante 1",
            "kind": "dante_input",
            "dante_channel": 1,
        }
        group = {
            "id": "enc-1",
            "name": "Stereo Pair",
            "channel_count": 2,
            "channels": [
                {"index": 1, "source_id": "src-1", "label": "L"},
                {"index": 2, "source_id": None, "label": "R"},
            ],
        }

        assert client.post("/api/sources", json=source).status_code == 200
        create_response = client.post("/api/encode-groups", json=group)
        assert create_response.status_code == 200

        config_response = client.get("/api/config")
        assert config_response.status_code == 200
        body = config_response.json()
        assert any(s["id"] == "src-1" for s in body["sources"])
        assert body["encode_groups"][0]["channels"][0]["source_id"] == "src-1"


def test_status_exposes_runtime_capabilities_without_fabricated_observations() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/status")

        assert response.status_code == 200
        body = response.json()
        assert body["runtime"]["engine"] == "managed-gstreamer+gst-launch"
        assert body["runtime"]["capabilities"]["first_class_media_runtime"] is True
        assert body["runtime"]["capabilities"]["srt_managed_transport"] is True
        assert body["runtime"]["graph_plan"]["error_count"] >= 0
        assert body["runtime"]["capabilities"]["observed_telemetry"] is False
        assert body["srt"]["rtt_ms"] is None
        assert body["srt"]["send_bitrate_kbps"] is None
        assert body["system"]["cpu_percent"] is None
        assert body["meters"]["inputs"][0]["peak_dbfs"] is None


def test_telemetry_merges_gstreamer_observations_without_fabricating_missing_values() -> None:
    telemetry = media_module.TelemetryService()
    config = media_module.EndpointConfig.model_validate({
        "audio": {"channel_count": 2},
        "srt_transports": [
            {
                "id": "srt-main",
                "name": "Main",
                "direction": "tx",
                "mode": "listener",
                "encode_group_ids": [],
            }
        ],
    })

    telemetry.mark_srt_transport("srt-main", True)
    telemetry.observe_srt_transport("srt-main", rtt_ms=12.5, send_bitrate_kbps=96.0)
    telemetry.observe_output_meter(1, peak_dbfs=-3.2, rms_dbfs=-18.7)

    body = telemetry.snapshot(config)

    assert body["srt"]["rtt_ms"] == 12.5
    assert body["srt"]["send_bitrate_kbps"] == 96.0
    assert body["srt"]["receive_bitrate_kbps"] is None
    assert body["srt_transports"][0]["rtt_ms"] == 12.5
    assert body["meters"]["outputs"][0] == {"channel": 1, "peak_dbfs": -3.2, "rms_dbfs": -18.7}
    assert body["meters"]["outputs"][1] == {"channel": 2, "peak_dbfs": None, "rms_dbfs": None}


def test_media_graph_plan_exposes_tx_graph_from_configured_tone_sources() -> None:
    with TestClient(create_app()) as client:
        response = client.put(
            "/api/config",
            json={
                "sources": [
                    {"id": "tone-l", "name": "Tone L", "kind": "tone", "tone_frequency_hz": 440},
                ],
                "encode_groups": [
                    {
                        "id": "enc-main",
                        "name": "Main Mono",
                        "channel_count": 1,
                        "channels": [
                            {"index": 1, "source_id": "tone-l", "label": "L"},
                        ],
                    }
                ],
                "srt_transports": [
                    {
                        "id": "srt-main",
                        "name": "Main TX",
                        "direction": "tx",
                        "mode": "listener",
                        "port": 9100,
                        "encode_group_ids": ["enc-main"],
                    }
                ],
            },
        )
        assert response.status_code == 200

        plan_response = client.get("/api/media/graph-plan")

        assert plan_response.status_code == 200
        plan = plan_response.json()
        assert plan["valid"] is True
        transport_plan = plan["srt_transports"][0]
        assert transport_plan["transport"]["id"] == "srt-main"
        assert transport_plan["groups"][0]["id"] == "enc-main"
        assert transport_plan["sources"] == [
            {"group_id": "enc-main", "channel_index": 1, "source_id": "tone-l", "kind": "tone", "name": "Tone L"},
        ]
        assert "rtpopuspay" in transport_plan["gstreamer"]["graph"]
        assert "srtsink" in transport_plan["gstreamer"]["graph"]
        assert transport_plan["gstreamer"]["monitor_taps"] == [
            {
                "id": "monitor_tap_tx_srt_main_enc_main_1",
                "direction": "tx",
                "stage": "post-encode-pre-pay",
                "codec": "opus",
                "group_id": "enc-main",
                "channel_index": 1,
                "source_id": "tone-l",
            },
        ]
        assert "level" in transport_plan["gstreamer"]["argv"]
        assert "name=dbmeter_out_enc_main_1" in transport_plan["gstreamer"]["argv"]
        assert "audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=1,channel-mask=(bitmask)0x4" in transport_plan["gstreamer"]["argv"]
        assert "audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x4" in transport_plan["gstreamer"]["argv"]
        assert "name=monitor_tap_tx_srt_main_enc_main_1" in transport_plan["gstreamer"]["argv"]
        assert "wait-for-connection=true" in transport_plan["gstreamer"]["argv"]
        assert "freq=440.0" in transport_plan["gstreamer"]["argv"]
        assert "uri=srt://:9100?mode=listener&latency=240" in transport_plan["gstreamer"]["argv"]


def test_media_graph_plan_exposes_rx_monitor_tap() -> None:
    with TestClient(create_app()) as client:
        response = client.put(
            "/api/config",
            json={
                "srt_transports": [
                    {
                        "id": "srt-rx",
                        "name": "Main RX",
                        "direction": "rx",
                        "mode": "listener",
                        "port": 9200,
                    }
                ],
            },
        )
        assert response.status_code == 200

        plan_response = client.get("/api/media/graph-plan")

        assert plan_response.status_code == 200
        plan = plan_response.json()
        transport_plan = plan["srt_transports"][0]
        assert transport_plan["transport"]["id"] == "srt-rx"
        assert transport_plan["gstreamer"]["monitor_taps"] == [
            {
                "id": "monitor_tap_rx_srt_rx",
                "direction": "rx",
                "stage": "post-depay-pre-decode",
                "codec": "opus",
                "channel_index": 1,
            }
        ]
        assert "name=srtstats_rx_srt_rx" in transport_plan["gstreamer"]["argv"]
        assert "name=monitor_tap_rx_srt_rx" in transport_plan["gstreamer"]["argv"]
        assert "name=dbmeter_in_srt_rx_1" in transport_plan["gstreamer"]["argv"]
        assert "fakesink" in transport_plan["gstreamer"]["argv"]


def test_media_graph_plan_reports_validation_errors() -> None:
    with TestClient(create_app()) as client:
        response = client.put(
            "/api/config",
            json={
                "sources": [
                    {"id": "dante-1", "name": "Dante 1", "kind": "dante_input", "dante_channel": 1},
                ],
                "encode_groups": [
                    {
                        "id": "enc-empty",
                        "name": "Empty",
                        "channel_count": 1,
                        "channels": [],
                    },
                    {
                        "id": "enc-bad",
                        "name": "Bad Count",
                        "channel_count": 2,
                        "channels": [
                            {"index": 1, "source_id": "missing-src"},
                        ],
                    },
                    {
                        "id": "enc-dante",
                        "name": "Dante",
                        "channel_count": 1,
                        "channels": [
                            {"index": 1, "source_id": "dante-1"},
                        ],
                    },
                ],
                "srt_transports": [
                    {
                        "id": "tx-empty",
                        "name": "TX Empty",
                        "direction": "tx",
                        "mode": "listener",
                        "encode_group_ids": [],
                    },
                    {
                        "id": "tx-bad",
                        "name": "TX Bad",
                        "direction": "tx",
                        "mode": "listener",
                        "encode_group_ids": ["enc-empty", "enc-bad", "enc-dante"],
                    },
                ],
            },
        )
        assert response.status_code == 200

        plan_response = client.get("/api/media/graph-plan")

        assert plan_response.status_code == 200
        plan = plan_response.json()
        codes = {error["code"] for error in plan["errors"]}
        assert plan["valid"] is False
        assert {
            "tx_transport_has_no_groups",
            "empty_encode_group",
            "wrong_channel_count",
            "missing_source_id",
            "audio_interface_not_selected",
        }.issubset(codes)


def test_tx_dante_capture_uses_shared_deinterleave_per_os() -> None:
    """A TX group with dante_input channels must emit one shared capture node
    (per the configured driver) followed by deinterleave; per-channel branches
    pull from the deinterleave src pad keyed on the source's dante_channel."""
    with TestClient(create_app()) as client:
        response = client.put(
            "/api/config",
            json={
                "audio": {
                    "interface_name": "Dante Virtual Soundcard",
                    "interface_driver": "wasapi",
                    "interface_device_id": "{0.0.1.dvs}",
                    "channel_count": 8,
                },
                "sources": [
                    {"id": "dante-in-01", "name": "Dante 1", "kind": "dante_input", "dante_channel": 1},
                    {"id": "dante-in-05", "name": "Dante 5", "kind": "dante_input", "dante_channel": 5},
                    {"id": "silence-default", "name": "Silence", "kind": "silence"},
                ],
                "encode_groups": [
                    {
                        "id": "enc-mix",
                        "name": "Mix",
                        "channel_count": 3,
                        "channels": [
                            {"index": 1, "source_id": "dante-in-01"},
                            {"index": 2, "source_id": "dante-in-05"},
                            {"index": 3, "source_id": "silence-default"},
                        ],
                    },
                ],
                "srt_transports": [
                    {
                        "id": "srt-mix",
                        "name": "Mix TX",
                        "direction": "tx",
                        "mode": "listener",
                        "port": 9100,
                        "encode_group_ids": ["enc-mix"],
                    },
                ],
            },
        )
        assert response.status_code == 200

        plan = client.get("/api/media/graph-plan").json()
        assert plan["valid"] is True
        graph = plan["srt_transports"][0]["gstreamer"]["graph"]

        # Single shared capture node, addressed by device id, deinterleaved.
        assert "wasapisrc" in graph
        assert 'device="{0.0.1.dvs}"' in graph
        assert "deinterleave name=dante_in_enc_mix" in graph
        # Dante channels 1 and 5 map to deinterleave src pads 0 and 4.
        assert "dante_in_enc_mix.src_0 !" in graph
        assert "dante_in_enc_mix.src_4 !" in graph
        # The silence channel still uses audiotestsrc.
        assert "audiotestsrc is-live=true wave=silence" in graph
        assert "audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=3,channel-mask=(bitmask)0x7" in graph
        assert "audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x1" in graph
        assert "audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x2" in graph
        assert "audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x4" in graph
        # Each channel terminates at the matching interleave sink pad.
        assert "il_enc_mix.sink_0" in graph
        assert "il_enc_mix.sink_1" in graph
        assert "il_enc_mix.sink_2" in graph


def test_tx_dante_capture_switches_element_per_driver() -> None:
    from app.core.config import (
        AudioConfig,
        EncodeGroupChannelConfig,
        EncodeGroupConfig,
        EndpointConfig,
        OpusStreamConfig,
        SourceConfig,
        SourceKind,
        SrtMode,
        SrtTransportConfig,
        SrtTransportDirection,
    )
    from app.services.media_graph import MediaGraphBuilder

    base_kwargs = dict(
        sources=[
            SourceConfig(id="silence-default", name="Silence", kind=SourceKind.silence),
            SourceConfig(id="dante-in-01", name="D1", kind=SourceKind.dante_input, dante_channel=1),
        ],
        encode_groups=[
            EncodeGroupConfig(
                id="enc-1", name="g", channel_count=1,
                channels=[EncodeGroupChannelConfig(index=1, source_id="dante-in-01")],
                opus=OpusStreamConfig(bitrate_kbps=96),
            ),
        ],
        srt_transports=[
            SrtTransportConfig(
                id="srt-1", name="t", direction=SrtTransportDirection.tx,
                mode=SrtMode.listener, port=9100, latency_ms=240, encode_group_ids=["enc-1"],
            ),
        ],
    )
    cases = [
        ("wasapi", "wasapisrc"),
        ("coreaudio", "osxaudiosrc"),
        ("alsa", "alsasrc"),
    ]
    for driver, expected_element in cases:
        cfg = EndpointConfig(
            audio=AudioConfig(interface_name="DVS", interface_driver=driver, channel_count=8),
            **base_kwargs,
        )
        plan = MediaGraphBuilder().plan_srt_transport(cfg, "srt-1")
        assert plan["valid"] is True, f"{driver}: {plan['errors']}"
        graph = plan["gstreamer"]["graph"]
        assert expected_element in graph, f"{driver} should use {expected_element}"

    cfg = EndpointConfig(
        audio=AudioConfig(
            interface_name="DVS",
            interface_driver="asio",
            interface_device_id="B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
            channel_count=16,
        ),
        **base_kwargs,
    )
    plan = MediaGraphBuilder().plan_srt_transport(cfg, "srt-1")
    assert plan["valid"] is True, plan["errors"]
    graph = plan["gstreamer"]["graph"]
    assert "asiosrc device-clsid=\"{B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8}\" input-channels=0,1" in graph
    assert "audio/x-raw,rate=48000,channels=2,format=S16LE" in graph
    assert "dante_in_enc_1.src_0 !" in graph


def test_plan_endpoint_tx_bundle_collapses_two_tx_legs_into_one_pipeline() -> None:
    """Two enabled TX SRT transports should plan into ONE argv with a single shared
    dante capture node and two distinct srtsinks. This is what unblocks the
    'second TX subprocess fails on DVS ASIO' crash in production."""
    from app.core.config import (
        AudioConfig,
        EncodeGroupChannelConfig,
        EncodeGroupConfig,
        EndpointConfig,
        OpusStreamConfig,
        SourceConfig,
        SourceKind,
        SrtMode,
        SrtTransportConfig,
        SrtTransportDirection,
    )
    from app.services.media_graph import MediaGraphBuilder

    cfg = EndpointConfig(
        audio=AudioConfig(
            interface_name="DVS",
            interface_driver="asio",
            interface_device_id="B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
            channel_count=16,
        ),
        sources=[
            SourceConfig(id="d-in-1", name="D1", kind=SourceKind.dante_input, dante_channel=1),
            SourceConfig(id="d-in-2", name="D2", kind=SourceKind.dante_input, dante_channel=2),
        ],
        encode_groups=[
            EncodeGroupConfig(
                id="enc-a", name="A", channel_count=1,
                channels=[EncodeGroupChannelConfig(index=1, source_id="d-in-1")],
                opus=OpusStreamConfig(bitrate_kbps=96),
            ),
            EncodeGroupConfig(
                id="enc-b", name="B", channel_count=1,
                channels=[EncodeGroupChannelConfig(index=1, source_id="d-in-2")],
                opus=OpusStreamConfig(bitrate_kbps=128),
            ),
        ],
        srt_transports=[
            SrtTransportConfig(
                id="tx-a", name="TX A", direction=SrtTransportDirection.tx,
                mode=SrtMode.listener, port=9101, latency_ms=240, encode_group_ids=["enc-a"],
            ),
            SrtTransportConfig(
                id="tx-b", name="TX B", direction=SrtTransportDirection.tx,
                mode=SrtMode.listener, port=9102, latency_ms=240, encode_group_ids=["enc-b"],
            ),
        ],
    )

    plan = MediaGraphBuilder().plan_endpoint_tx_bundle(cfg)
    assert plan["valid"] is True, plan["errors"]
    assert plan["transport_ids"] == ["tx-a", "tx-b"]

    graph = plan["gstreamer"]["graph"]
    # One shared asiosrc, not two — this is the whole point of the bundle.
    assert graph.count("asiosrc") == 1
    # Both Dante channels feed the same shared deinterleave.
    assert "deinterleave name=dante_in_shared" in graph
    assert "dante_in_shared.src_0" in graph
    assert "dante_in_shared.src_1" in graph
    # Each TX leg gets its own srtsink with its own URI and srtstats name.
    assert "srtsink name=srtstats_tx_tx_a" in graph
    assert "srtsink name=srtstats_tx_tx_b" in graph
    assert "uri=srt://:9101" in graph
    assert "uri=srt://:9102" in graph
    # Bitrates differ per leg.
    assert "opusenc bitrate=96000" in graph
    assert "opusenc bitrate=128000" in graph
    # Element names are transport-scoped so two legs sharing names would not collide.
    assert "name=il_tx_a_enc_a" in graph
    assert "name=il_tx_b_enc_b" in graph
    assert "name=dbmeter_out_tx_a_enc_a_1" in graph
    assert "name=dbmeter_out_tx_b_enc_b_1" in graph

    # Per-transport srt endpoint metadata is exposed so the runtime can attribute
    # SRT stats to each leg independently.
    endpoints = plan["gstreamer"]["srt_endpoints"]
    assert endpoints == [
        {"transport_id": "tx-a", "element_name": "srtstats_tx_tx_a"},
        {"transport_id": "tx-b", "element_name": "srtstats_tx_tx_b"},
    ]


def test_plan_endpoint_tx_bundle_with_no_tx_returns_empty() -> None:
    from app.core.config import EndpointConfig
    from app.services.media_graph import MediaGraphBuilder

    plan = MediaGraphBuilder().plan_endpoint_tx_bundle(EndpointConfig())
    assert plan == {"valid": True, "errors": [], "transport_ids": [], "gstreamer": None}


def test_two_tx_starts_share_one_bundle_pipeline(monkeypatch) -> None:
    """Starting two TX transports should result in exactly one running pipeline.

    Each start rebuilds the bundle with the cumulative membership. The reported
    pipeline list never grows beyond one TX entry because the bundle is shared.
    """
    spawned: list[tuple[str, list[tuple[str, str]]]] = []

    class FakeManagedPipeline:
        def __init__(self, name: str, graph: str) -> None:
            self.name = name
            self.graph = graph
            self.stopped = False

        def describe(self, *, include_output_tail: bool = True):
            return {
                "name": self.name,
                "pid": None,
                "argv": ["managed-gstreamer", self.graph],
                "running": not self.stopped,
                "returncode": None,
                "engine": "ctypes-gstreamer",
            }

        def stop(self) -> None:
            self.stopped = True

    def fake_spawn_bundle(self, *, name, graph, srt_endpoints, meter_lookup=None):
        spawned.append((name, list(srt_endpoints)))
        return FakeManagedPipeline(name, graph)

    monkeypatch.setattr(media_module.MediaController, "_spawn_managed_tx_bundle", fake_spawn_bundle)

    with TestClient(create_app()) as client:
        client.put(
            "/api/config",
            json={
                "sources": [
                    {"id": "tone-a", "name": "A", "kind": "tone", "tone_frequency_hz": 440},
                    {"id": "tone-b", "name": "B", "kind": "tone", "tone_frequency_hz": 880},
                ],
                "encode_groups": [
                    {"id": "g-a", "name": "A", "channel_count": 1, "channels": [{"index": 1, "source_id": "tone-a"}]},
                    {"id": "g-b", "name": "B", "channel_count": 1, "channels": [{"index": 1, "source_id": "tone-b"}]},
                ],
                "srt_transports": [
                    {"id": "tx-a", "name": "TX A", "direction": "tx", "mode": "listener", "port": 9201, "encode_group_ids": ["g-a"]},
                    {"id": "tx-b", "name": "TX B", "direction": "tx", "mode": "listener", "port": 9202, "encode_group_ids": ["g-b"]},
                ],
            },
        )

        assert client.post("/api/srt-transports/tx-a/start", json={}).status_code == 200
        assert client.post("/api/srt-transports/tx-b/start", json={}).status_code == 200

        # Bundle was rebuilt once per start (the second start picks up tx-a as
        # an existing member, so the second spawn membership covers both).
        assert len(spawned) == 2
        assert dict(spawned[0][1]) == {"tx-a": "srtstats_tx_tx_a"}
        assert dict(spawned[1][1]) == {"tx-a": "srtstats_tx_tx_a", "tx-b": "srtstats_tx_tx_b"}

        # Exactly one pipeline reported by /api/media/pipelines, despite two
        # transports being active — the de-dup logic in list_pipelines handles
        # multiple keys pointing at the same bundle object.
        pipelines = client.get("/api/media/pipelines").json()
        assert len(pipelines) == 1
        assert pipelines[0]["name"] == "tx_bundle_tx-a_tx-b"

        # Stopping one TX rebuilds the bundle for the remaining member.
        assert client.post("/api/srt-transports/tx-a/stop").status_code == 200
        assert len(spawned) == 3
        assert dict(spawned[2][1]) == {"tx-b": "srtstats_tx_tx_b"}

        # Stopping the last TX tears the bundle down — no pipelines left.
        assert client.post("/api/srt-transports/tx-b/stop").status_code == 200
        pipelines_after = client.get("/api/media/pipelines").json()
        assert pipelines_after == []


def test_tx_srt_transport_start_uses_graph_plan(monkeypatch) -> None:
    # TX transports run inside a single shared bundle so DVS/ASIO is opened once.
    # This test asserts the bundle gets a graph containing the expected per-leg
    # elements (with transport-scoped names so multiple TXs can coexist later).
    spawned: list[tuple[str, str, list[tuple[str, str]]]] = []

    class FakeManagedPipeline:
        def __init__(self, name: str, graph: str) -> None:
            self.name = name
            self.graph = graph
            self.stopped = False

        def describe(self, *, include_output_tail: bool = True):
            return {
                "name": self.name,
                "pid": None,
                "argv": ["managed-gstreamer", self.graph],
                "running": not self.stopped,
                "returncode": None,
                "engine": "ctypes-gstreamer",
            }

        def stop(self) -> None:
            self.stopped = True

    def fake_spawn_bundle(self, *, name, graph, srt_endpoints, meter_lookup=None):
        spawned.append((name, graph, list(srt_endpoints)))
        return FakeManagedPipeline(name, graph)

    monkeypatch.setattr(media_module.MediaController, "_spawn_managed_tx_bundle", fake_spawn_bundle)

    with TestClient(create_app()) as client:
        assert client.put(
            "/api/config",
            json={
                "sources": [
                    {"id": "tone-l", "name": "Tone L", "kind": "tone", "tone_frequency_hz": 440},
                ],
                "encode_groups": [
                    {
                        "id": "enc-main",
                        "name": "Main Mono",
                        "channel_count": 1,
                        "channels": [
                            {"index": 1, "source_id": "tone-l"},
                        ],
                    }
                ],
                "srt_transports": [
                    {
                        "id": "tx-main",
                        "name": "TX Main",
                        "direction": "tx",
                        "mode": "listener",
                        "port": 9100,
                        "encode_group_ids": ["enc-main"],
                    }
                ],
            },
        ).status_code == 200

        start_response = client.post("/api/srt-transports/tx-main/start", json={})

        assert start_response.status_code == 200
        body = start_response.json()
        assert body["pipeline"]["graph_plan"]["transport"]["id"] == "tx-main"
        assert len(spawned) == 1
        bundle_name, graph, srt_endpoints = spawned[0]
        assert bundle_name == "tx_bundle_tx-main"
        assert srt_endpoints == [("tx-main", "srtstats_tx_tx_main")]
        assert "rtpopuspay" in graph
        assert "srtsink" in graph
        assert "audiotestsrc" in graph
        assert "level" in graph
        # Bundle names embed the transport id so two TX legs sharing an encode
        # group won't collide on element names.
        assert "name=dbmeter_out_tx_main_enc_main_1" in graph
        assert "audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=1,channel-mask=(bitmask)0x4" in graph
        assert "audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask)0x4" in graph
        assert "tee name=monitor_tap_tx_tx_main_enc_main_1 allow-not-linked=true" in graph
        assert "wait-for-connection=true" in graph
        assert "freq=440.0" in graph
        assert "uri=srt://:9100?mode=listener&latency=240" in graph


def test_bundled_pipeline_attributes_meters_per_transport() -> None:
    """Level messages from a bundled TX pipeline must land in the per-transport
    meter store keyed by element name, not the flat channel-keyed dict."""
    from app.services.gst_runtime import CtypesManagedPipeline

    telemetry = media_module.TelemetryService()
    telemetry.mark_srt_transport("tx-a", True)
    telemetry.mark_srt_transport("tx-b", True)

    meter_lookup = {
        "dbmeter_out_tx_a_g_a_1": ("tx-a", "out", 1),
        "dbmeter_out_tx_b_g_b_1": ("tx-b", "out", 1),
    }
    pipeline = CtypesManagedPipeline(
        name="bundle",
        graph="",
        telemetry=telemetry,
        runtime=None,  # type: ignore[arg-type]
        pipeline=0,
        srt_endpoints=[("tx-a", 0), ("tx-b", 0)],
        bus=None,
        meter_lookup=meter_lookup,
    )

    pipeline._observe_level(
        "dbmeter_out_tx_a_g_a_1",
        "level, rms=(GValueArray)< -23.0 >, peak=(GValueArray)< -3.0 >;",
    )
    pipeline._observe_level(
        "dbmeter_out_tx_b_g_b_1",
        "level, rms=(GValueArray)< -45.5 >, peak=(GValueArray)< -30.0 >;",
    )

    by_transport = telemetry._meter_snapshot_by_transport()
    assert "tx-a" in by_transport and "tx-b" in by_transport
    assert by_transport["tx-a"]["outputs"] == [{"channel": 1, "peak_dbfs": -3.0, "rms_dbfs": -23.0}]
    assert by_transport["tx-b"]["outputs"] == [{"channel": 1, "peak_dbfs": -30.0, "rms_dbfs": -45.5}]

    # Stopping a transport drops its per-transport meter store but leaves the others.
    telemetry.mark_srt_transport("tx-a", False)
    remaining = telemetry._meter_snapshot_by_transport()
    assert "tx-a" not in remaining
    assert "tx-b" in remaining


def test_media_controller_parses_gstreamer_level_messages() -> None:
    controller = media_module.MediaController(telemetry=media_module.TelemetryService())

    controller._observe_gstreamer_line(
        'Got message #12 from element "dbmeter_out_enc_main_2" (element): '
        'level, rms=(GValueArray)< -21.25 >, peak=(GValueArray)< -4.5 >, decay=(GValueArray)< -4.5 >;'
    )

    status = controller.telemetry.snapshot(media_module.EndpointConfig.model_validate({"audio": {"channel_count": 2}}))

    assert status["meters"]["outputs"][1] == {"channel": 2, "peak_dbfs": -4.5, "rms_dbfs": -21.25}
    assert controller.runtime_status(media_module.EndpointConfig())["capabilities"]["audio_metering"] is True


def test_srt_stats_parser_maps_sender_srt_trace_fields() -> None:
    from app.services.gst_runtime import _first_float, _first_int, _loss_percent, _mbps_to_kbps, _parse_stats

    fields = _parse_stats(
        "application/x-srt-statistics, "
        "packets-sent=(gint64)100, "
        "packets-sent-lost=(int)2, "
        "packets-retransmitted=(int)3, "
        "send-rate-mbps=(double)0.094, "
        "rtt-ms=(double)14.25, "
        "bytes-sent-total=(guint64)123456;"
    )

    assert _first_float(fields, "rtt-ms") == 14.25
    assert _first_int(fields, "packets-sent") == 100
    assert _first_int(fields, "packets-sent-lost") == 2
    assert _first_int(fields, "packets-retransmitted") == 3
    assert _mbps_to_kbps(_first_float(fields, "send-rate-mbps")) == 94.0
    assert _first_int(fields, "bytes-sent-total") == 123456
    assert _loss_percent(2, 100) == 1.961


def test_managed_gstreamer_meter_activity_marks_clock_running() -> None:
    from app.services.gst_runtime import CtypesManagedPipeline

    telemetry = media_module.TelemetryService()
    pipeline = CtypesManagedPipeline(
        name="test",
        graph="",
        telemetry=telemetry,
        runtime=None,  # type: ignore[arg-type]
        pipeline=0,
        srt_endpoints=[],
        bus=None,
    )

    pipeline._observe_clock(
        "dbmeter_out_enc_main_1",
        "level, rms=(GValueArray)< -21.25 >, peak=(GValueArray)< -4.5 >;",
    )

    status = telemetry.snapshot(media_module.EndpointConfig())
    assert status["clock"]["lock_state"] == "running"
    assert status["clock"]["frequency_ratio_ppm"] is None


def test_monitor_branch_attach_detach_round_trip(monkeypatch) -> None:
    class FakeManagedPipeline:
        def __init__(self, name: str, graph: str, srt_element_name: str) -> None:
            self.name = name
            self.graph = graph
            self.srt_element_name = srt_element_name
            self.stopped = False
            self._branches: dict[str, dict] = {}
            self._counter = 0

        def describe(self, *, include_output_tail: bool = True):
            return {
                "name": self.name,
                "pid": None,
                "argv": ["managed-gstreamer", self.graph],
                "running": not self.stopped,
                "returncode": None,
                "engine": "ctypes-gstreamer",
            }

        def stop(self) -> None:
            self.stopped = True

        def attach_branch(self, tap_name: str, description: str):
            self._counter += 1
            handle = f"h{self._counter}"
            entry = {"handle": handle, "tap_name": tap_name, "description": description}
            self._branches[handle] = entry
            class _B:
                pass
            b = _B()
            b.handle = handle
            return b

        def detach_branch(self, handle: str) -> bool:
            return self._branches.pop(handle, None) is not None

        def list_branches(self):
            return list(self._branches.values())

    def fake_spawn_managed(self, *, name, graph, srt_element_name, transport_id):
        return FakeManagedPipeline(name, graph, srt_element_name)

    monkeypatch.setattr(media_module.MediaController, "_spawn_managed_gst_pipeline", fake_spawn_managed)

    with TestClient(create_app()) as client:
        client.post("/api/srt-transports", json={
            "id": "tx-mon",
            "name": "Mon",
            "direction": "rx",
            "mode": "listener",
            "port": 9300,
        })
        assert client.post("/api/srt-transports/tx-mon/start", json={}).status_code == 200

        # rejects unknown tap
        bad = client.post("/api/srt-transports/tx-mon/monitor-branches", json={"tap_id": "nope"})
        assert bad.status_code == 409
        assert "tap 'nope'" in bad.json()["detail"]

        # attaches default audible branch
        attach = client.post(
            "/api/srt-transports/tx-mon/monitor-branches",
            json={"tap_id": "monitor_tap_rx_tx_mon"},
        )
        assert attach.status_code == 200
        body = attach.json()
        assert body["transport_id"] == "tx-mon"
        assert body["audible"] is True
        assert "sink" in body["branch_description"]
        assert body["tap"]["id"] == "monitor_tap_rx_tx_mon"
        handle = body["handle"]

        # listing reflects the attachment
        listed = client.get("/api/srt-transports/tx-mon/monitor-branches").json()
        assert [b["handle"] for b in listed["branches"]] == [handle]

        # detach
        detach = client.delete(f"/api/srt-transports/tx-mon/monitor-branches/{handle}")
        assert detach.status_code == 200
        assert detach.json() == {"monitor_branch": "detached", "handle": handle}

        # second detach 404s
        assert client.delete(f"/api/srt-transports/tx-mon/monitor-branches/{handle}").status_code == 404

        # cannot attach when transport not running
        client.post("/api/srt-transports/tx-mon/stop")
        not_running = client.post(
            "/api/srt-transports/tx-mon/monitor-branches",
            json={"tap_id": "monitor_tap_rx_tx_mon"},
        )
        assert not_running.status_code == 409
        assert "not running" in not_running.json()["detail"]


def test_runtime_status_advertises_dynamic_monitor_branches() -> None:
    with TestClient(create_app()) as client:
        body = client.get("/api/media/runtime").json()
        assert body["capabilities"]["dynamic_monitor_branches"] is True


def test_plan_spine_full_duplex_shape() -> None:
    """The spine plan must emit one asiosrc, one asiosink, N capture tees, and N
    playback mixers (each with a default silence input), all in one graph."""
    from app.core.config import AudioConfig, EndpointConfig
    from app.services.media_graph import MediaGraphBuilder

    cfg = EndpointConfig(
        audio=AudioConfig(
            interface_name="DVS",
            interface_driver="asio",
            interface_device_id="B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
            channel_count=4,
        ),
    )

    plan = MediaGraphBuilder().plan_spine(cfg)
    assert plan["valid"] is True, plan["errors"]
    assert plan["channel_count"] == 4
    assert plan["capture_tee_names"] == [
        "spine_in_tee_1", "spine_in_tee_2", "spine_in_tee_3", "spine_in_tee_4",
    ]
    assert plan["playback_mixer_names"] == [
        "spine_out_mix_1", "spine_out_mix_2", "spine_out_mix_3", "spine_out_mix_4",
    ]

    graph = plan["gstreamer"]["graph"]
    # Exactly one asiosrc and one asiosink element — DVS opens once, full duplex.
    # Assert on the unique element-name attachment.
    assert graph.count("name=spine_asiosrc") == 1
    assert graph.count("name=spine_asiosink") == 1
    assert plan["gstreamer"]["asiosrc_element_name"] == "spine_asiosrc"
    assert plan["gstreamer"]["asiosink_element_name"] == "spine_asiosink"
    # ASIO channel selection covers 0..N-1 explicitly so deinterleave ordering
    # matches Dante channel numbers.
    assert "input-channels=0,1,2,3" in graph
    assert "output-channels=0,1,2,3" in graph
    assert "audio/x-raw,format=S16LE,rate=48000,channels=4,layout=interleaved,channel-mask=(bitmask)0x0" in graph
    # Single shared deinterleave -> N tees with a per-channel input level meter
    # between the queue and tee. The level meters double as Dante-input metering
    # in the UI and as a diagnostic that capture is actually producing buffers.
    assert "deinterleave name=spine_capture_split" in graph
    for tee in plan["capture_tee_names"]:
        assert f"tee name={tee} allow-not-linked=true" in graph
    for channel in (1, 2, 3, 4):
        assert f"level name=dbmeter_in_spine_{channel} message=true interval=100000000" in graph
        assert f"spine_in_tee_{channel}. ! queue ! fakesink sync=false async=false" in graph
    assert graph.count("fakesink sync=false async=false") == 4
    # One interleave on the playback side, fed by N adders. adder keeps the
    # output attach points dynamically linkable after the spine is already PLAYING.
    assert "interleave name=spine_out_interleave" in graph
    for mixer in plan["playback_mixer_names"]:
        assert f"audiomixer name={mixer} latency=20000000 ! audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved ! queue ! spine_out_interleave" in graph
    # Default silence feeders keep each mixer producing buffers when no RX
    # leg is attached, so asiosink never starves.
    assert graph.count("audiotestsrc is-live=true wave=silence") == 4


def test_plan_spine_rejects_unset_audio_interface() -> None:
    from app.core.config import EndpointConfig
    from app.services.media_graph import MediaGraphBuilder

    plan = MediaGraphBuilder().plan_spine(EndpointConfig())
    assert plan["valid"] is False
    assert plan["gstreamer"] is None
    assert any(error["code"] == "audio_interface_not_selected" for error in plan["errors"])


def test_plan_tx_capture_spine_shape() -> None:
    from app.core.config import AudioConfig, EndpointConfig
    from app.services.media_graph import MediaGraphBuilder

    cfg = EndpointConfig(
        audio=AudioConfig(
            interface_name="DVS",
            interface_driver="asio",
            interface_device_id="B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
            channel_count=4,
        ),
    )

    plan = MediaGraphBuilder().plan_tx_capture_spine(cfg)
    assert plan["valid"] is True, plan["errors"]
    assert plan["capture_tee_names"] == [
        "spine_in_tee_1", "spine_in_tee_2", "spine_in_tee_3", "spine_in_tee_4",
    ]
    graph = plan["gstreamer"]["graph"]
    assert "asiosrc" in graph
    assert "asiosink" not in graph
    assert "input-channels=0,1,2,3" in graph
    assert "audio/x-raw,format=S16LE,rate=48000,channels=4,layout=interleaved,channel-mask=(bitmask)0x0" in graph
    assert "deinterleave name=spine_capture_split" in graph
    for channel in (1, 2, 3, 4):
        assert f"level name=dbmeter_in_spine_{channel} message=true interval=100000000" in graph
        assert f"tee name=spine_in_tee_{channel} allow-not-linked=true" in graph
        assert f"spine_in_tee_{channel}. ! queue ! fakesink sync=false async=false" in graph
    assert graph.count("fakesink sync=false async=false") == 4


def test_spine_diagnostics_routes_drive_controller(monkeypatch) -> None:
    """The /diagnostics/spine routes must call start_spine / stop_spine and
    expose describe_spine without touching real GStreamer."""
    calls: list[str] = []

    class FakePipeline:
        def describe(self, **kwargs):
            return {"name": "spine_full_duplex", "running": True}

    def fake_start(self, config):  # noqa: ANN001
        calls.append("start")
        self._spine_pipeline = FakePipeline()
        return {"running": True, "already_running": False, "channel_count": 16, "process": FakePipeline().describe()}

    def fake_stop(self):  # noqa: ANN001
        calls.append("stop")
        self._spine_pipeline = None

    monkeypatch.setattr(media_module.MediaController, "start_spine", fake_start)
    monkeypatch.setattr(media_module.MediaController, "stop_spine", fake_stop)

    with TestClient(create_app()) as client:
        status = client.get("/api/diagnostics/spine").json()
        assert status == {"running": False}

        start = client.post("/api/diagnostics/spine/start")
        assert start.status_code == 200
        assert start.json()["running"] is True

        running = client.get("/api/diagnostics/spine").json()
        assert running["running"] is True

        stop = client.post("/api/diagnostics/spine/stop")
        assert stop.status_code == 200
        assert stop.json() == {"spine": "stopped"}

    # Extra trailing stop is the shutdown hook calling stop_spine on teardown.
    assert calls[:2] == ["start", "stop"]


def test_plan_tx_leg_branch_emits_per_channel_taps_and_one_srtsink() -> None:
    """A multichannel TX leg attached to the spine should produce one branch
    description with N ghost-pad entry queues (in_K), one interleave, one
    opusenc, one srtsink. tap_names must point at the spine capture tees for
    each source channel."""
    from app.core.config import (
        AudioConfig,
        EncodeGroupChannelConfig,
        EncodeGroupConfig,
        EndpointConfig,
        OpusStreamConfig,
        SourceConfig,
        SourceKind,
        SrtMode,
        SrtTransportConfig,
        SrtTransportDirection,
    )
    from app.services.media_graph import MediaGraphBuilder

    cfg = EndpointConfig(
        audio=AudioConfig(
            interface_name="DVS",
            interface_driver="asio",
            interface_device_id="B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
            channel_count=16,
        ),
        sources=[
            SourceConfig(id="d1", name="D1", kind=SourceKind.dante_input, dante_channel=1),
            SourceConfig(id="d3", name="D3", kind=SourceKind.dante_input, dante_channel=3),
            SourceConfig(id="d4", name="D4", kind=SourceKind.dante_input, dante_channel=4),
            SourceConfig(id="d5", name="D5", kind=SourceKind.dante_input, dante_channel=5),
        ],
        encode_groups=[
            EncodeGroupConfig(
                id="enc-pgm", name="PGM", channel_count=4,
                channels=[
                    EncodeGroupChannelConfig(index=1, source_id="d1"),
                    EncodeGroupChannelConfig(index=2, source_id="d3"),
                    EncodeGroupChannelConfig(index=3, source_id="d4"),
                    EncodeGroupChannelConfig(index=4, source_id="d5"),
                ],
                opus=OpusStreamConfig(bitrate_kbps=128),
            ),
        ],
        srt_transports=[
            SrtTransportConfig(
                id="tx-pgm", name="TX PGM", direction=SrtTransportDirection.tx,
                mode=SrtMode.caller, host="1.2.3.4", port=9000, latency_ms=240,
                encode_group_ids=["enc-pgm"],
            ),
        ],
    )

    plan = MediaGraphBuilder().plan_tx_leg_branch(cfg, "tx-pgm")
    assert plan["valid"] is True, plan["errors"]
    # tap_names maps each encode group channel (1..N) onto its source Dante
    # channel's spine tee. Order is preserved so attach_branch_multi can ghost
    # them as sink_0..sink_{N-1} in matching order.
    assert plan["tap_names"] == [
        "spine_in_tee_1", "spine_in_tee_3", "spine_in_tee_4", "spine_in_tee_5",
    ]
    assert plan["entry_element_names"] == ["in_1", "in_2", "in_3", "in_4"]
    assert plan["srt_element_name"] == "srtstats_tx_tx_pgm"

    desc = plan["branch_description"]
    # Per-channel entry queues are named in_K with K=channel.index, so the
    # ghost pad sink_{K-1} is deterministic.
    for k in (1, 2, 3, 4):
        assert f"queue name=in_{k} " in desc
    # One interleave fanning 4 mono legs into a 4-channel bus, then encode.
    assert "interleave name=il_tx_pgm_enc_pgm " in desc
    assert "channels=4" in desc
    assert "opusenc bitrate=128000" in desc
    # Exactly one srtsink with this transport's URI/element name.
    assert desc.count("srtsink") == 1
    assert "srtsink name=srtstats_tx_tx_pgm" in desc
    assert "uri=srt://1.2.3.4:9000?mode=caller&latency=240" in desc
    # wait-for-connection must be OFF for spine-attached legs: with it on, the
    # dynamically-added bin's PAUSED transition deadlocks waiting for the peer
    # and no buffers ever flow through the encoder.
    assert "wait-for-connection=true" not in desc
    # No asiosrc / deinterleave — those belong to the spine, not the branch.
    assert "asiosrc" not in desc
    assert "deinterleave" not in desc

    # Per-channel meter endpoints reuse the existing dbmeter_out_<tx>_<group>_<K>
    # naming so telemetry attribution does not change.
    meters = {(m["channel"], m["element_name"]) for m in plan["meter_endpoints"]}
    assert meters == {
        (1, "dbmeter_out_tx_pgm_enc_pgm_1"),
        (2, "dbmeter_out_tx_pgm_enc_pgm_2"),
        (3, "dbmeter_out_tx_pgm_enc_pgm_3"),
        (4, "dbmeter_out_tx_pgm_enc_pgm_4"),
    }


def test_plan_tx_leg_branch_rejects_non_dante_sources() -> None:
    """tone/silence sources cannot be pulled from a spine tee. The planner must
    reject them rather than emitting a branch that can never link."""
    from app.core.config import (
        AudioConfig,
        EncodeGroupChannelConfig,
        EncodeGroupConfig,
        EndpointConfig,
        OpusStreamConfig,
        SourceConfig,
        SourceKind,
        SrtMode,
        SrtTransportConfig,
        SrtTransportDirection,
    )
    from app.services.media_graph import MediaGraphBuilder

    cfg = EndpointConfig(
        audio=AudioConfig(
            interface_name="DVS",
            interface_driver="asio",
            interface_device_id="B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
            channel_count=2,
        ),
        sources=[
            SourceConfig(id="tone-1", name="T", kind=SourceKind.tone, tone_frequency_hz=440.0),
        ],
        encode_groups=[
            EncodeGroupConfig(
                id="enc", name="E", channel_count=1,
                channels=[EncodeGroupChannelConfig(index=1, source_id="tone-1")],
                opus=OpusStreamConfig(bitrate_kbps=96),
            ),
        ],
        srt_transports=[
            SrtTransportConfig(
                id="tx-t", name="TT", direction=SrtTransportDirection.tx,
                mode=SrtMode.caller, host="h", port=9001, latency_ms=240,
                encode_group_ids=["enc"],
            ),
        ],
    )

    plan = MediaGraphBuilder().plan_tx_leg_branch(cfg, "tx-t")
    assert plan["valid"] is False
    assert plan["branch_description"] is None
    assert any(e["code"] == "non_dante_source_in_spine_tx" for e in plan["errors"])


def test_plan_rx_leg_branch_emits_mixer_outputs_and_no_fakesink() -> None:
    from app.core.config import (
        AudioConfig,
        EncodeGroupChannelConfig,
        EncodeGroupConfig,
        EndpointConfig,
        SourceConfig,
        SourceKind,
        SrtMode,
        SrtTransportConfig,
        SrtTransportDirection,
    )
    from app.services.media_graph import MediaGraphBuilder

    cfg = EndpointConfig(
        audio=AudioConfig(
            interface_name="DVS",
            interface_driver="asio",
            interface_device_id="B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
            channel_count=16,
        ),
        sources=[SourceConfig(id="silence", name="S", kind=SourceKind.silence)],
        encode_groups=[
            EncodeGroupConfig(
                id="rx-g", name="RX", channel_count=2,
                channels=[
                    EncodeGroupChannelConfig(index=1, source_id="silence"),
                    EncodeGroupChannelConfig(index=2, source_id="silence"),
                ],
            ),
        ],
        srt_transports=[
            SrtTransportConfig(
                id="rx-a", name="RX A", direction=SrtTransportDirection.rx,
                mode=SrtMode.caller, host="1.2.3.4", port=9000, latency_ms=240,
                encode_group_ids=["rx-g"],
            ),
        ],
    )

    plan = MediaGraphBuilder().plan_rx_leg_branch(cfg, "rx-a")
    assert plan["valid"] is True, plan["errors"]
    assert plan["mixer_names"] == ["spine_out_mix_1", "spine_out_mix_2"]
    assert plan["exit_element_names"] == ["out_1", "out_2"]
    assert plan["srt_element_name"] == "srtstats_rx_rx_a"
    desc = plan["branch_description"]
    assert "srtsrc name=srtstats_rx_rx_a uri=srt://1.2.3.4:9000?mode=caller&latency=240" in desc
    assert "deinterleave name=rx_split_rx_a" in desc
    assert "rx_split_rx_a.src_0 ! queue ! level name=dbmeter_in_rx_a_1" in desc
    assert "rx_split_rx_a.src_1 ! queue ! level name=dbmeter_in_rx_a_2" in desc
    assert "audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved ! queue name=out_1" in desc
    assert "audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved ! queue name=out_2" in desc
    assert "fakesink" not in desc


def test_spine_tx_path_attaches_branch_without_restarting_spine(monkeypatch) -> None:
    """Starting a TX transport with dante_input sources must:
    1. lazily start the spine,
    2. call attach_branch_multi on the spine with the right taps + entry names,
    3. NOT call the legacy bundle spawn,
    4. tolerate a second TX start without rebuilding the spine."""
    spine_starts: list[str] = []
    attach_calls: list[dict[str, Any]] = []
    bundle_calls: list[Any] = []

    from typing import Any as _Any  # local import to satisfy the annotation
    Any = _Any  # noqa: F841

    class FakeSpine:
        def __init__(self) -> None:
            self.pipeline = 0
            self.srt_endpoints: list[tuple[str, int]] = []
            self.meter_lookup: dict[str, tuple[str, str, int]] = {}
            self._next_handle = 0
            self.detached: list[str] = []

        def describe(self, *, include_output_tail: bool = True):
            return {"name": "spine_full_duplex", "running": True}

        def attach_branch_multi(self, *, tap_names, entry_element_names, description):
            self._next_handle += 1
            handle = f"branch_{self._next_handle}"
            attach_calls.append({
                "tap_names": list(tap_names),
                "entry_element_names": list(entry_element_names),
                "description": description,
                "handle": handle,
            })

            class _Branch:
                pass

            b = _Branch()
            b.handle = handle  # noqa: SLF001
            return b

        def detach_branch(self, handle: str) -> bool:
            self.detached.append(handle)
            return True

        def stop(self) -> None:
            pass

    fake_spine = FakeSpine()

    def fake_start_spine(self, config):  # noqa: ANN001
        spine_starts.append("started")
        self._spine_pipeline = fake_spine
        return {"running": True, "already_running": False, "channel_count": 16}

    def fake_register(self, transport_id, element_name):  # noqa: ANN001
        # No-op in tests — real version resolves the element pointer in the
        # running pipeline which doesn't exist here.
        return

    def fake_unregister(self, transport_id):  # noqa: ANN001
        return

    def fake_spawn_bundle(self, *, name, graph, srt_endpoints, meter_lookup=None):
        bundle_calls.append((name, graph))
        raise AssertionError("legacy bundle path must not be hit for spine-eligible TX")

    monkeypatch.setattr(media_module.MediaController, "start_spine", fake_start_spine)
    monkeypatch.setattr(media_module.MediaController, "_register_spine_srt_endpoint", fake_register)
    monkeypatch.setattr(media_module.MediaController, "_unregister_spine_srt_endpoint", fake_unregister)
    monkeypatch.setattr(media_module.MediaController, "_spawn_managed_tx_bundle", fake_spawn_bundle)

    with TestClient(create_app()) as client:
        # A dante-input source on Dante channel 1 with audio configured to
        # asio/DVS makes the leg spine-eligible.
        assert client.put(
            "/api/config",
            json={
                "audio": {
                    "interface_name": "DVS",
                    "interface_driver": "asio",
                    "interface_device_id": "B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
                    "channel_count": 16,
                },
                "sources": [
                    {"id": "d1", "name": "D1", "kind": "dante_input", "dante_channel": 1},
                    {"id": "d2", "name": "D2", "kind": "dante_input", "dante_channel": 2},
                ],
                "encode_groups": [
                    {"id": "g-a", "name": "A", "channel_count": 1, "channels": [{"index": 1, "source_id": "d1"}]},
                    {"id": "g-b", "name": "B", "channel_count": 1, "channels": [{"index": 1, "source_id": "d2"}]},
                ],
                "srt_transports": [
                    {"id": "tx-a", "name": "TX A", "direction": "tx", "mode": "listener", "port": 9301, "encode_group_ids": ["g-a"]},
                    {"id": "tx-b", "name": "TX B", "direction": "tx", "mode": "listener", "port": 9302, "encode_group_ids": ["g-b"]},
                ],
            },
        ).status_code == 200

        assert client.post("/api/srt-transports/tx-a/start", json={}).status_code == 200
        assert client.post("/api/srt-transports/tx-b/start", json={}).status_code == 200

        # Spine started exactly once; second TX reused it.
        assert spine_starts == ["started"]
        # Two branches attached, in start order.
        assert len(attach_calls) == 2
        assert attach_calls[0]["tap_names"] == ["spine_in_tee_1"]
        assert attach_calls[0]["entry_element_names"] == ["in_1"]
        assert "srtsink name=srtstats_tx_tx_a" in attach_calls[0]["description"]
        assert attach_calls[1]["tap_names"] == ["spine_in_tee_2"]
        assert "srtsink name=srtstats_tx_tx_b" in attach_calls[1]["description"]
        # Legacy bundle was never touched.
        assert bundle_calls == []

        # Stopping one TX detaches its branch without affecting the other.
        assert client.post("/api/srt-transports/tx-a/stop").status_code == 200
        assert fake_spine.detached == ["branch_1"]

        # Second stop detaches the other branch.
        assert client.post("/api/srt-transports/tx-b/stop").status_code == 200
        assert fake_spine.detached == ["branch_1", "branch_2"]


def test_spine_rx_path_decodes_to_output_spine_bus(monkeypatch) -> None:
    spine_starts: list[str] = []
    spawn_calls: list[dict[str, Any]] = []
    attach_calls: list[dict[str, Any]] = []

    from typing import Any as _Any
    Any = _Any  # noqa: F841

    class FakeSpine:
        def __init__(self) -> None:
            self.pipeline = 0
            self.srt_endpoints: list[tuple[str, int]] = []
            self.meter_lookup: dict[str, tuple[str, str, int]] = {}
            self._next_handle = 0
            self.detached: list[str] = []

        def describe(self, *, include_output_tail: bool = True):
            return {"name": "spine_full_duplex", "running": True}

        def stop(self) -> None:
            pass

        def attach_branch_outputs_multi(self, mixer_names, exit_element_names, description):
            self._next_handle += 1
            handle = f"branch_{self._next_handle}"
            attach_calls.append({
                "mixer_names": list(mixer_names),
                "exit_element_names": list(exit_element_names),
                "description": description,
                "handle": handle,
            })
            class _Branch:
                pass
            b = _Branch()
            b.handle = handle
            return b

        def detach_branch(self, handle: str) -> bool:
            self.detached.append(handle)
            return True

    fake_spine = FakeSpine()

    def fake_start_spine(self, config):  # noqa: ANN001
        spine_starts.append("started")
        self._spine_pipeline = fake_spine
        return {"running": True, "already_running": False, "channel_count": 16}

    def fake_register(self, transport_id, element_name):  # noqa: ANN001
        return

    def fake_unregister(self, transport_id):  # noqa: ANN001
        return

    class FakePipeline:
        def __init__(self, transport_id: str) -> None:
            self.transport_id = transport_id
            self.output_tail: list[str] = []

        def describe(self, *, include_output_tail: bool = True):
            return {"name": "rx_spine_output", "running": True}

        def stop(self) -> None:
            pass

    def fake_spawn_bundle(self, *, name, graph, srt_endpoints, meter_lookup=None):
        spawn_calls.append({
            "name": name,
            "graph": graph,
            "srt_endpoints": list(srt_endpoints),
            "meter_lookup": dict(meter_lookup or {}),
        })
        return FakePipeline(srt_endpoints[0][0])

    monkeypatch.setattr(media_module.MediaController, "start_spine", fake_start_spine)
    monkeypatch.setattr(media_module.MediaController, "_register_spine_srt_endpoint", fake_register)
    monkeypatch.setattr(media_module.MediaController, "_unregister_spine_srt_endpoint", fake_unregister)
    monkeypatch.setattr(media_module.MediaController, "_spawn_managed_tx_bundle", fake_spawn_bundle)

    with TestClient(create_app()) as client:
        assert client.put(
            "/api/config",
            json={
                "audio": {
                    "interface_name": "DVS",
                    "interface_driver": "asio",
                    "interface_device_id": "B5DEF3F2-B191-4F8D-9A67-A77402A6D3D8",
                    "channel_count": 16,
                },
                "sources": [
                    {"id": "silence", "name": "S", "kind": "silence"},
                ],
                "encode_groups": [
                    {"id": "rx-g", "name": "RX", "channel_count": 1, "channels": [{"index": 1, "source_id": "silence"}]},
                ],
                "srt_transports": [
                    {"id": "rx-a", "name": "RX A", "direction": "rx", "mode": "listener", "port": 9401, "encode_group_ids": ["rx-g"]},
                ],
            },
        ).status_code == 200

        assert client.post("/api/srt-transports/rx-a/start", json={}).status_code == 200
        assert spine_starts == ["started"]
        assert len(attach_calls) == 1
        assert "srtsrc name=srtstats_rx_rx_a" in attach_calls[0]["description"]
        assert "queue name=out_1" in attach_calls[0]["description"]
        assert "fakesink" not in attach_calls[0]["description"]

        assert client.post("/api/srt-transports/rx-a/stop").status_code == 200
