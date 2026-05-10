from __future__ import annotations

import ctypes
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.telemetry import TelemetryService


GST_STATE_NULL = 1
GST_STATE_PLAYING = 4
GST_CLOCK_TIME_NONE = (1 << 64) - 1
GST_MESSAGE_ANY = 0xFFFFFFFF


_LEVEL_MESSAGE_RE = re.compile(
    r"level,.*?rms=\([^)]+\)<(?P<rms>[^>]*)>.*?peak=\([^)]+\)<(?P<peak>[^>]*)>",
    re.IGNORECASE,
)
_STAT_FIELD_RE = re.compile(r"([A-Za-z0-9_-]+)=\([^)]+\)([^,;]+)")


class GstMiniObject(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_size_t),
        ("refcount", ctypes.c_int),
        ("lockstate", ctypes.c_int),
        ("flags", ctypes.c_uint),
        ("copy", ctypes.c_void_p),
        ("dispose", ctypes.c_void_p),
        ("free", ctypes.c_void_p),
        ("priv_uint", ctypes.c_uint),
        ("priv_pointer", ctypes.c_void_p),
    ]


class GstMessage(ctypes.Structure):
    _fields_ = [
        ("mini_object", GstMiniObject),
        ("type", ctypes.c_int),
        ("timestamp", ctypes.c_uint64),
        ("src", ctypes.c_void_p),
        ("seqnum", ctypes.c_uint),
    ]


class GstMapInfo(ctypes.Structure):
    _fields_ = [
        ("memory", ctypes.c_void_p),
        ("flags", ctypes.c_int),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
        ("size", ctypes.c_size_t),
        ("maxsize", ctypes.c_size_t),
        ("user_data", ctypes.c_void_p * 4),
        ("_gst_reserved", ctypes.c_void_p * 4),
    ]


GST_MAP_READ = 1


@dataclass
class CtypesGst:
    root: Path
    gst: ctypes.CDLL
    gobject: ctypes.CDLL
    glib: ctypes.CDLL
    gstapp: ctypes.CDLL | None = None

    @classmethod
    def load(cls, gst_launch_executable: str) -> "CtypesGst":
        gst_launch_path = Path(gst_launch_executable)
        root = gst_launch_path.parent.parent if gst_launch_path.is_file() else Path("C:/Program Files/gstreamer/1.0/msvc_x86_64")
        bin_dir = root / "bin"
        lib_dir = root / "lib"
        os.environ["PATH"] = f"{bin_dir};{os.environ.get('PATH', '')}"
        os.environ.setdefault("GST_PLUGIN_PATH_1_0", str(lib_dir / "gstreamer-1.0"))
        os.environ.setdefault("GST_PLUGIN_SYSTEM_PATH_1_0", str(lib_dir / "gstreamer-1.0"))
        os.environ.setdefault("GST_PLUGIN_SCANNER_1_0", str(lib_dir / "exec/gstreamer-1.0/gst-plugin-scanner.exe"))
        ctypes.windll.kernel32.SetDllDirectoryW(str(bin_dir))

        gstapp_path = bin_dir / "gstapp-1.0-0.dll"
        runtime = cls(
            root=root,
            gst=ctypes.CDLL(str(bin_dir / "gstreamer-1.0-0.dll")),
            gobject=ctypes.CDLL(str(bin_dir / "gobject-2.0-0.dll")),
            glib=ctypes.CDLL(str(bin_dir / "glib-2.0-0.dll")),
            gstapp=ctypes.CDLL(str(gstapp_path)) if gstapp_path.exists() else None,
        )
        runtime._bind()
        argc = ctypes.c_void_p()
        argv = ctypes.c_void_p()
        runtime.gst.gst_init(ctypes.byref(argc), ctypes.byref(argv))
        return runtime

    def _bind(self) -> None:
        self.gst.gst_init.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
        self.gst.gst_parse_launch.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        self.gst.gst_parse_launch.restype = ctypes.c_void_p
        self.gst.gst_element_set_state.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.gst.gst_element_set_state.restype = ctypes.c_int
        self.gst.gst_bin_get_by_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.gst.gst_bin_get_by_name.restype = ctypes.c_void_p
        self.gst.gst_element_get_bus.argtypes = [ctypes.c_void_p]
        self.gst.gst_element_get_bus.restype = ctypes.c_void_p
        self.gst.gst_bus_timed_pop_filtered.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint]
        self.gst.gst_bus_timed_pop_filtered.restype = ctypes.c_void_p
        self.gst.gst_message_get_structure.argtypes = [ctypes.c_void_p]
        self.gst.gst_message_get_structure.restype = ctypes.c_void_p
        self.gst.gst_structure_to_string.argtypes = [ctypes.c_void_p]
        self.gst.gst_structure_to_string.restype = ctypes.c_void_p
        self.gst.gst_object_get_name.argtypes = [ctypes.c_void_p]
        self.gst.gst_object_get_name.restype = ctypes.c_void_p
        self.gst.gst_mini_object_unref.argtypes = [ctypes.c_void_p]
        self.gst.gst_mini_object_unref.restype = None
        self.glib.g_free.argtypes = [ctypes.c_void_p]
        self.glib.g_free.restype = None
        self.gobject.g_object_get.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]
        self.gobject.g_object_get.restype = None
        self.gobject.g_object_unref.argtypes = [ctypes.c_void_p]
        self.gobject.g_object_unref.restype = None
        self.gst.gst_parse_bin_from_description.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        self.gst.gst_parse_bin_from_description.restype = ctypes.c_void_p
        self.gst.gst_bin_add.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_bin_add.restype = ctypes.c_int
        self.gst.gst_bin_remove.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_bin_remove.restype = ctypes.c_int
        self.gst.gst_element_release_request_pad.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_element_release_request_pad.restype = None
        self.gst.gst_element_get_static_pad.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.gst.gst_element_get_static_pad.restype = ctypes.c_void_p
        self.gst.gst_element_sync_state_with_parent.argtypes = [ctypes.c_void_p]
        self.gst.gst_element_sync_state_with_parent.restype = ctypes.c_int
        self.gst.gst_pad_link.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_pad_link.restype = ctypes.c_int
        self.gst.gst_pad_unlink.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_pad_unlink.restype = ctypes.c_int
        # request_pad_simple is GStreamer 1.20+; fall back to get_request_pad on older builds.
        if hasattr(self.gst, "gst_element_request_pad_simple"):
            self.gst.gst_element_request_pad_simple.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            self.gst.gst_element_request_pad_simple.restype = ctypes.c_void_p
        self.gst.gst_element_get_request_pad.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.gst.gst_element_get_request_pad.restype = ctypes.c_void_p

    def request_tee_src_pad(self, tee: int) -> int | None:
        fn = getattr(self.gst, "gst_element_request_pad_simple", None) or self.gst.gst_element_get_request_pad
        pad = fn(tee, b"src_%u")
        return pad or None

    def bind_appsink(self) -> None:
        if self.gstapp is None:
            raise RuntimeError("gstapp-1.0-0.dll not found; appsink not available")
        self.gstapp.gst_app_sink_try_pull_sample.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        self.gstapp.gst_app_sink_try_pull_sample.restype = ctypes.c_void_p
        self.gstapp.gst_app_sink_is_eos.argtypes = [ctypes.c_void_p]
        self.gstapp.gst_app_sink_is_eos.restype = ctypes.c_int
        self.gst.gst_sample_get_buffer.argtypes = [ctypes.c_void_p]
        self.gst.gst_sample_get_buffer.restype = ctypes.c_void_p
        self.gst.gst_buffer_map.argtypes = [ctypes.c_void_p, ctypes.POINTER(GstMapInfo), ctypes.c_int]
        self.gst.gst_buffer_map.restype = ctypes.c_int
        self.gst.gst_buffer_unmap.argtypes = [ctypes.c_void_p, ctypes.POINTER(GstMapInfo)]
        self.gst.gst_buffer_unmap.restype = None

    def pull_appsink_sample(self, appsink: int, timeout_ns: int) -> bytes | None:
        if self.gstapp is None:
            return None
        sample = self.gstapp.gst_app_sink_try_pull_sample(appsink, timeout_ns)
        if not sample:
            return None
        try:
            buffer_ptr = self.gst.gst_sample_get_buffer(sample)
            if not buffer_ptr:
                return None
            info = GstMapInfo()
            if not self.gst.gst_buffer_map(buffer_ptr, ctypes.byref(info), GST_MAP_READ):
                return None
            try:
                return ctypes.string_at(info.data, info.size)
            finally:
                self.gst.gst_buffer_unmap(buffer_ptr, ctypes.byref(info))
        finally:
            self.gst.gst_mini_object_unref(sample)

    def string_and_free(self, ptr: int | None) -> str | None:
        if not ptr:
            return None
        try:
            return ctypes.string_at(ptr).decode("utf-8", "replace")
        finally:
            self.glib.g_free(ptr)


@dataclass
class AttachedBranch:
    handle: str
    tap_name: str
    description: str
    bin_element: int
    tee_element: int
    tee_src_pad: int
    branch_sink_pad: int


@dataclass
class CtypesManagedPipeline:
    name: str
    graph: str
    transport_id: str
    telemetry: TelemetryService
    runtime: CtypesGst
    pipeline: int
    srt_element: int | None
    bus: int | None
    output_tail: deque[str] = field(default_factory=lambda: deque(maxlen=120))
    started_at: float = field(default_factory=time.time)
    _stopped: bool = False
    _last_bytes: int | None = None
    _last_stats_at: float | None = None
    _last_stats_tail_at: float | None = None
    _thread: threading.Thread | None = None
    _branches: dict[str, AttachedBranch] = field(default_factory=dict)
    _branch_lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def start(
        cls,
        *,
        name: str,
        graph: str,
        srt_element_name: str,
        transport_id: str,
        telemetry: TelemetryService,
        runtime: CtypesGst,
    ) -> "CtypesManagedPipeline":
        error = ctypes.c_void_p()
        pipeline = runtime.gst.gst_parse_launch(graph.encode("utf-8"), ctypes.byref(error))
        if not pipeline:
            raise RuntimeError(f"failed to create GStreamer pipeline for {name}")
        srt_element = runtime.gst.gst_bin_get_by_name(pipeline, srt_element_name.encode("utf-8"))
        bus = runtime.gst.gst_element_get_bus(pipeline)
        result = runtime.gst.gst_element_set_state(pipeline, GST_STATE_PLAYING)
        if result == 0:
            runtime.gst.gst_element_set_state(pipeline, GST_STATE_NULL)
            raise RuntimeError(f"{name} pipeline failed to enter PLAYING")
        managed = cls(
            name=name,
            graph=graph,
            transport_id=transport_id,
            telemetry=telemetry,
            runtime=runtime,
            pipeline=pipeline,
            srt_element=srt_element,
            bus=bus,
        )
        managed._thread = threading.Thread(target=managed._poll, name=f"{name}_gst_runtime", daemon=True)
        managed._thread.start()
        return managed

    @property
    def argv(self) -> list[str]:
        return ["managed-gstreamer", self.graph]

    def describe(self, *, include_output_tail: bool = True) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "pid": None,
            "argv": self.argv,
            "running": not self._stopped,
            "returncode": None,
            "started_at": self.started_at,
            "engine": "ctypes-gstreamer",
        }
        if include_output_tail:
            payload["output_tail"] = list(self.output_tail)
        return payload

    def stop(self) -> None:
        self._stopped = True
        with self._branch_lock:
            for branch in list(self._branches.values()):
                self._teardown_branch_locked(branch)
            self._branches.clear()
        self.runtime.gst.gst_element_set_state(self.pipeline, GST_STATE_NULL)
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self.bus:
            self.runtime.gobject.g_object_unref(self.bus)
            self.bus = None
        if self.srt_element:
            self.runtime.gobject.g_object_unref(self.srt_element)
            self.srt_element = None
        if self.pipeline:
            self.runtime.gobject.g_object_unref(self.pipeline)
            self.pipeline = 0

    def attach_branch(self, tap_name: str, description: str) -> AttachedBranch:
        if self._stopped:
            raise RuntimeError("pipeline is stopped")
        gst = self.runtime.gst
        gobject = self.runtime.gobject

        tee = gst.gst_bin_get_by_name(self.pipeline, tap_name.encode("utf-8"))
        if not tee:
            raise RuntimeError(f"tap '{tap_name}' not found in pipeline '{self.name}'")

        bin_element = 0
        tee_src_pad = 0
        branch_sink_pad = 0
        added_to_pipeline = False
        try:
            error = ctypes.c_void_p()
            bin_element = gst.gst_parse_bin_from_description(
                description.encode("utf-8"), 1, ctypes.byref(error)
            )
            if not bin_element:
                raise RuntimeError(f"failed to parse branch description: {description!r}")

            if not gst.gst_bin_add(self.pipeline, bin_element):
                raise RuntimeError("gst_bin_add rejected the branch bin")
            added_to_pipeline = True

            tee_src_pad = self.runtime.request_tee_src_pad(tee) or 0
            if not tee_src_pad:
                raise RuntimeError(f"tap '{tap_name}' refused a request src pad")

            branch_sink_pad = gst.gst_element_get_static_pad(bin_element, b"sink")
            if not branch_sink_pad:
                raise RuntimeError("branch bin has no ghost sink pad")

            link_result = gst.gst_pad_link(tee_src_pad, branch_sink_pad)
            if link_result != 0:
                raise RuntimeError(f"gst_pad_link failed (code {link_result})")

            if not gst.gst_element_sync_state_with_parent(bin_element):
                raise RuntimeError("branch failed to sync state with parent")

            handle = uuid.uuid4().hex
            branch = AttachedBranch(
                handle=handle,
                tap_name=tap_name,
                description=description,
                bin_element=bin_element,
                tee_element=tee,
                tee_src_pad=tee_src_pad,
                branch_sink_pad=branch_sink_pad,
            )
            with self._branch_lock:
                self._branches[handle] = branch
            return branch
        except Exception:
            if branch_sink_pad:
                gobject.g_object_unref(branch_sink_pad)
            if tee_src_pad:
                gst.gst_element_release_request_pad(tee, tee_src_pad)
                gobject.g_object_unref(tee_src_pad)
            if added_to_pipeline:
                gst.gst_bin_remove(self.pipeline, bin_element)
            elif bin_element:
                gobject.g_object_unref(bin_element)
            gobject.g_object_unref(tee)
            raise

    def detach_branch(self, handle: str) -> bool:
        with self._branch_lock:
            branch = self._branches.pop(handle, None)
            if branch is None:
                return False
            self._teardown_branch_locked(branch)
            return True

    def list_branches(self) -> list[dict[str, Any]]:
        with self._branch_lock:
            return [
                {"handle": branch.handle, "tap_name": branch.tap_name, "description": branch.description}
                for branch in self._branches.values()
            ]

    def _teardown_branch_locked(self, branch: AttachedBranch) -> None:
        gst = self.runtime.gst
        gobject = self.runtime.gobject
        try:
            gst.gst_pad_unlink(branch.tee_src_pad, branch.branch_sink_pad)
            gst.gst_element_set_state(branch.bin_element, GST_STATE_NULL)
            gst.gst_bin_remove(self.pipeline, branch.bin_element)
            gst.gst_element_release_request_pad(branch.tee_element, branch.tee_src_pad)
        finally:
            if branch.tee_src_pad:
                gobject.g_object_unref(branch.tee_src_pad)
            if branch.branch_sink_pad:
                gobject.g_object_unref(branch.branch_sink_pad)
            if branch.tee_element:
                gobject.g_object_unref(branch.tee_element)

    def _poll(self) -> None:
        while not self._stopped:
            self._drain_bus()
            self._poll_srt_stats()
            time.sleep(0.1)

    def _drain_bus(self, *, max_messages: int = 100) -> None:
        for _ in range(max_messages):
            if not self._poll_bus_once():
                return

    def _poll_bus_once(self) -> bool:
        if not self.bus:
            return False
        message = self.runtime.gst.gst_bus_timed_pop_filtered(self.bus, 0, GST_MESSAGE_ANY)
        if not message:
            return False
        try:
            structure = self.runtime.gst.gst_message_get_structure(message)
            if not structure:
                return True
            text = self.runtime.string_and_free(self.runtime.gst.gst_structure_to_string(structure))
            if not text:
                return True
            source = GstMessage.from_address(message).src
            source_name = self.runtime.string_and_free(self.runtime.gst.gst_object_get_name(source)) if source else None
            line = f'{source_name or "unknown"}: {text}'
            self.output_tail.append(line)
            self._observe_clock(source_name, text)
            self._observe_level(source_name, text)
            return True
        finally:
            self.runtime.gst.gst_mini_object_unref(message)

    def _poll_srt_stats(self) -> None:
        if not self.srt_element:
            return
        stats = ctypes.c_void_p()
        self.runtime.gobject.g_object_get(self.srt_element, b"stats", ctypes.byref(stats), None)
        if not stats.value:
            return
        text = self.runtime.string_and_free(self.runtime.gst.gst_structure_to_string(stats))
        if not text:
            return
        now = time.time()
        if self._last_stats_tail_at is None or now - self._last_stats_tail_at >= 1.0:
            self.output_tail.append(f"srtstats: {text}")
            self._last_stats_tail_at = now
        fields = _parse_stats(text)
        bytes_total = _first_int(fields, "pkti-send-bytes", "bytes-sent-total", "bytes-received-total")
        bitrate_kbps = _mbps_to_kbps(_first_float(fields, "send-rate-mbps"))
        if bytes_total is not None and self._last_bytes is not None and self._last_stats_at is not None:
            elapsed = max(now - self._last_stats_at, 0.001)
            delta_bitrate_kbps = max((bytes_total - self._last_bytes) * 8 / elapsed / 1000, 0)
            bitrate_kbps = bitrate_kbps if bitrate_kbps is not None else delta_bitrate_kbps
        if bytes_total is not None:
            self._last_bytes = bytes_total
            self._last_stats_at = now
        packets_sent = _first_int(fields, "packets-sent")
        packets_lost = _first_int(fields, "packets-sent-lost", "pkti-send-loss", "packets-lost", "pkt-snd-loss-total", "pkt-rcv-loss-total")
        self.telemetry.observe_srt_transport(
            self.transport_id,
            bitrate_kbps=bitrate_kbps,
            send_bitrate_kbps=bitrate_kbps,
            rtt_ms=_first_float(fields, "pkti-link-rtt-ms", "ms-rtt", "rtt-ms", "rtt"),
            rtt_variance_ms=_first_float(fields, "pkti-link-jitter-ms", "rtt-variance-ms"),
            packets_lost=packets_lost,
            packet_loss_percent=_loss_percent(packets_lost, packets_sent),
            packets_retransmitted=_first_int(fields, "packets-retransmitted", "pkti-send-retrans", "pkt-retrans-total"),
            raw_stats=text,
        )

    def _observe_level(self, source_name: str | None, text: str) -> None:
        if not source_name or not source_name.startswith(("dbmeter_out_", "dbmeter_in_")):
            return
        channel_text = source_name.rsplit("_", 1)[-1]
        if not channel_text.isdigit():
            return
        match = _LEVEL_MESSAGE_RE.search(text)
        if match is None:
            return
        observer = self.telemetry.observe_input_meter if source_name.startswith("dbmeter_in_") else self.telemetry.observe_output_meter
        observer(
            int(channel_text),
            peak_dbfs=_first_level_value(match.group("peak")),
            rms_dbfs=_first_level_value(match.group("rms")),
        )

    def _observe_clock(self, source_name: str | None, text: str) -> None:
        if text.startswith("GstMessageNewClock"):
            self.telemetry.observe_clock(lock_state="running")
            return
        if source_name and source_name.startswith(("dbmeter_out_", "dbmeter_in_")):
            self.telemetry.observe_clock(lock_state="running")


def _parse_stats(text: str) -> dict[str, str]:
    return {match.group(1).lower(): match.group(2).strip() for match in _STAT_FIELD_RE.finditer(text)}


def _first_float(fields: dict[str, str], *names: str) -> float | None:
    for name in names:
        value = fields.get(name.lower())
        if value is None:
            continue
        try:
            return float(value)
        except ValueError:
            continue
    return None


def _first_int(fields: dict[str, str], *names: str) -> int | None:
    value = _first_float(fields, *names)
    return int(value) if value is not None else None


def _mbps_to_kbps(value: float | None) -> float | None:
    return round(value * 1000, 3) if value is not None else None


def _loss_percent(lost: int | None, sent: int | None) -> float | None:
    if lost is None or sent is None:
        return None
    total = sent + lost
    if total <= 0:
        return 0.0
    return round((lost / total) * 100, 3)


def _first_level_value(raw_values: str) -> float | None:
    values = [part.strip() for part in raw_values.split(",") if part.strip()]
    if not values:
        return None
    try:
        value = float(values[0])
    except ValueError:
        return None
    return round(value, 3)
