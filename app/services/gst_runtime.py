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
        self.gst.gst_pad_get_peer.argtypes = [ctypes.c_void_p]
        self.gst.gst_pad_get_peer.restype = ctypes.c_void_p
        self.gst.gst_pad_set_active.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.gst.gst_pad_set_active.restype = ctypes.c_int
        self.gst.gst_element_link_pads_filtered.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p,
        ]
        self.gst.gst_element_link_pads_filtered.restype = ctypes.c_int
        self.gst.gst_caps_from_string.argtypes = [ctypes.c_char_p]
        self.gst.gst_caps_from_string.restype = ctypes.c_void_p
        self.gst.gst_caps_to_string.argtypes = [ctypes.c_void_p]
        self.gst.gst_caps_to_string.restype = ctypes.c_void_p
        self.gst.gst_pad_query_caps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_pad_query_caps.restype = ctypes.c_void_p
        if hasattr(self.gst, "gst_caps_unref"):
            self.gst.gst_caps_unref.argtypes = [ctypes.c_void_p]
            self.gst.gst_caps_unref.restype = None
        self.gst.gst_pad_unlink.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_pad_unlink.restype = ctypes.c_int
        # Used by detach_branch to flush a branch (notably srtsink) cleanly before
        # the NULL state transition. Without the EOS push, srtsink can hang in
        # PAUSED→NULL waiting on its own internal flush.
        self.gst.gst_event_new_eos.argtypes = []
        self.gst.gst_event_new_eos.restype = ctypes.c_void_p
        self.gst.gst_pad_send_event.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_pad_send_event.restype = ctypes.c_int
        # Used by attach_branch_multi to create predictable ghost sink pads on a
        # parsed bin so multi-channel TX legs can be wired to multiple spine
        # capture tees in one attach operation.
        self.gst.gst_ghost_pad_new.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        self.gst.gst_ghost_pad_new.restype = ctypes.c_void_p
        if hasattr(self.gst, "gst_ghost_pad_new_from_template"):
            self.gst.gst_ghost_pad_new_from_template.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]
            self.gst.gst_ghost_pad_new_from_template.restype = ctypes.c_void_p
        if hasattr(self.gst, "gst_pad_get_pad_template"):
            self.gst.gst_pad_get_pad_template.argtypes = [ctypes.c_void_p]
            self.gst.gst_pad_get_pad_template.restype = ctypes.c_void_p
        self.gst.gst_element_add_pad.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_element_add_pad.restype = ctypes.c_int
        # request_pad_simple is GStreamer 1.20+; fall back to get_request_pad on older builds.
        self.gst.gst_element_request_pad.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
        self.gst.gst_element_request_pad.restype = ctypes.c_void_p
        self.gst.gst_element_get_pad_template.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.gst.gst_element_get_pad_template.restype = ctypes.c_void_p
        if hasattr(self.gst, "gst_element_request_pad_simple"):
            self.gst.gst_element_request_pad_simple.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            self.gst.gst_element_request_pad_simple.restype = ctypes.c_void_p
        self.gst.gst_element_get_request_pad.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.gst.gst_element_get_request_pad.restype = ctypes.c_void_p

    def request_pad(self, element: int, template: bytes, caps_string: bytes | None = None) -> int | None:
        if caps_string:
            pad_template = self.gst.gst_element_get_pad_template(element, template)
            caps = self.gst.gst_caps_from_string(caps_string)
            try:
                if pad_template and caps:
                    pad = self.gst.gst_element_request_pad(element, pad_template, None, caps)
                    if pad:
                        return pad
            finally:
                if caps and hasattr(self.gst, "gst_caps_unref"):
                    self.gst.gst_caps_unref(caps)
        fn = getattr(self.gst, "gst_element_request_pad_simple", None) or self.gst.gst_element_get_request_pad
        pad = fn(element, template)
        return pad or None

    def request_tee_src_pad(self, tee: int) -> int | None:
        return self.request_pad(tee, b"src_%u")

    def request_mixer_sink_pad(self, mixer: int) -> int | None:
        return self.request_pad(
            mixer,
            b"sink_%u",
            None,
        )

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

    def pad_caps_string(self, pad: int, filter_caps: int | None = None) -> str:
        caps = self.gst.gst_pad_query_caps(pad, filter_caps or 0)
        if not caps:
            return "<no caps>"
        try:
            return self.string_and_free(self.gst.gst_caps_to_string(caps)) or "<caps unavailable>"
        finally:
            if hasattr(self.gst, "gst_caps_unref"):
                self.gst.gst_caps_unref(caps)


@dataclass
class AttachedBranch:
    """A bin attached to one or more tee taps in a running pipeline.

    Single-tap branches (existing monitor-branch use case) populate the
    ``tee_links`` list with a single entry. Multi-tap branches (multichannel TX
    legs pulling from N spine capture tees) populate it with one entry per tap.
    The scalar ``tap_name``/``tee_element``/``tee_src_pad``/``branch_sink_pad``
    fields are kept as a convenience view of the first link so the existing
    single-tap callers and tests do not need to be rewritten.
    """

    handle: str
    tap_name: str
    description: str
    bin_element: int
    tee_element: int
    tee_src_pad: int
    branch_sink_pad: int
    tee_links: list[tuple[str, int, int, int]] = field(default_factory=list)
    link_direction: str = "tee_to_branch"
    """List of (tap_name, tee_element, tee_src_pad, branch_sink_pad) for each
    tee → ghost-pad connection in this branch."""


@dataclass
class CtypesManagedPipeline:
    name: str
    graph: str
    telemetry: TelemetryService
    runtime: CtypesGst
    pipeline: int
    # Each entry is (transport_id, gst_element_ptr) for one srtsink/srtsrc inside
    # the pipeline. Single-transport pipelines (RX, single-leg TX, diagnostic
    # streams) have one entry. The endpoint TX bundle has one entry per enabled
    # TX SRT transport so the poll thread can attribute stats per transport.
    srt_endpoints: list[tuple[str, int]]
    bus: int | None
    # Maps a meter element name (the GStreamer ``level`` element's name field)
    # to (transport_id, direction, channel_index). Lets bus message handling
    # attribute per-channel level activity to the right transport in a bundled
    # pipeline. Single-transport pipelines may leave this empty; bus parsing
    # falls back to the legacy channel-only path when a name is unknown.
    meter_lookup: dict[str, tuple[str, str, int]] = field(default_factory=dict)
    output_tail: deque[str] = field(default_factory=lambda: deque(maxlen=120))
    started_at: float = field(default_factory=time.time)
    _stopped: bool = False
    _last_bytes: dict[str, int] = field(default_factory=dict)
    _last_stats_at: dict[str, float] = field(default_factory=dict)
    _last_stats_tail_at: float | None = None
    _thread: threading.Thread | None = None
    _branches: dict[str, AttachedBranch] = field(default_factory=dict)
    _branch_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def transport_id(self) -> str | None:
        """First transport id; convenient for single-transport pipelines.

        Returns None for empty bundles. Bundled TX pipelines should iterate
        ``srt_endpoints`` instead of relying on this field.
        """
        return self.srt_endpoints[0][0] if self.srt_endpoints else None

    @property
    def transport_ids(self) -> list[str]:
        return [tid for tid, _ in self.srt_endpoints]

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
        return cls.start_bundle(
            name=name,
            graph=graph,
            srt_endpoints=[(transport_id, srt_element_name)],
            telemetry=telemetry,
            runtime=runtime,
        )

    @classmethod
    def start_bundle(
        cls,
        *,
        name: str,
        graph: str,
        srt_endpoints: list[tuple[str, str]],
        telemetry: TelemetryService,
        runtime: CtypesGst,
        meter_lookup: dict[str, tuple[str, str, int]] | None = None,
    ) -> "CtypesManagedPipeline":
        error = ctypes.c_void_p()
        pipeline = runtime.gst.gst_parse_launch(graph.encode("utf-8"), ctypes.byref(error))
        if not pipeline:
            raise RuntimeError(f"failed to create GStreamer pipeline for {name}")
        resolved_endpoints: list[tuple[str, int]] = []
        for transport_id, element_name in srt_endpoints:
            element_ptr = runtime.gst.gst_bin_get_by_name(pipeline, element_name.encode("utf-8"))
            if not element_ptr:
                # Unwind on failure: drop everything already taken, free the pipeline.
                for _tid, ptr in resolved_endpoints:
                    runtime.gobject.g_object_unref(ptr)
                runtime.gst.gst_element_set_state(pipeline, GST_STATE_NULL)
                runtime.gobject.g_object_unref(pipeline)
                raise RuntimeError(
                    f"{name} pipeline does not contain srt element '{element_name}' for transport '{transport_id}'"
                )
            resolved_endpoints.append((transport_id, element_ptr))
        bus = runtime.gst.gst_element_get_bus(pipeline)
        result = runtime.gst.gst_element_set_state(pipeline, GST_STATE_PLAYING)
        if result == 0:
            runtime.gst.gst_element_set_state(pipeline, GST_STATE_NULL)
            for _tid, ptr in resolved_endpoints:
                runtime.gobject.g_object_unref(ptr)
            if bus:
                runtime.gobject.g_object_unref(bus)
            runtime.gobject.g_object_unref(pipeline)
            raise RuntimeError(f"{name} pipeline failed to enter PLAYING")
        managed = cls(
            name=name,
            graph=graph,
            telemetry=telemetry,
            runtime=runtime,
            pipeline=pipeline,
            srt_endpoints=resolved_endpoints,
            bus=bus,
            meter_lookup=dict(meter_lookup) if meter_lookup else {},
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
        for _transport_id, element_ptr in self.srt_endpoints:
            if element_ptr:
                self.runtime.gobject.g_object_unref(element_ptr)
        self.srt_endpoints = []
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
                tee_links=[(tap_name, tee, tee_src_pad, branch_sink_pad)],
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

    def attach_branch_multi(
        self,
        *,
        tap_names: list[str],
        entry_element_names: list[str],
        description: str,
    ) -> AttachedBranch:
        """Attach a bin to N tees in one operation.

        ``tap_names`` and ``entry_element_names`` are parallel lists. For each
        position K, the spine tee named ``tap_names[K]`` is linked to a ghost
        sink pad on the new bin that proxies the sink pad of the bin-internal
        element named ``entry_element_names[K]`` (typically a ``queue`` placed at
        the head of the per-channel chain in the branch description).

        The description must NOT use ``gst_parse_bin_from_description``'s auto-
        ghosting; this method creates explicit, deterministically-named ghost
        pads (``sink_0``, ``sink_1``, ...) so callers do not have to discover
        them. The description may have any internal topology after the named
        entry elements — common shape is per-channel ``audioconvert/level``
        legs feeding a single ``interleave`` followed by encode + transport.
        """
        if self._stopped:
            raise RuntimeError("pipeline is stopped")
        if len(tap_names) != len(entry_element_names):
            raise ValueError("tap_names and entry_element_names must be parallel lists")
        if not tap_names:
            raise ValueError("attach_branch_multi requires at least one tap")
        gst = self.runtime.gst
        gobject = self.runtime.gobject

        # Resolve all tees up front. Failing fast here means we have nothing to
        # unwind beyond a few unrefs.
        tees: list[tuple[str, int]] = []
        try:
            for tap_name in tap_names:
                tee = gst.gst_bin_get_by_name(self.pipeline, tap_name.encode("utf-8"))
                if not tee:
                    raise RuntimeError(f"tap '{tap_name}' not found in pipeline '{self.name}'")
                tees.append((tap_name, tee))
        except Exception:
            for _name, ptr in tees:
                gobject.g_object_unref(ptr)
            raise

        bin_element = 0
        added_to_pipeline = False
        created_ghosts: list[int] = []
        link_records: list[tuple[str, int, int, int]] = []
        try:
            error = ctypes.c_void_p()
            # ghost_unlinked_pads=0: we create our own ghost pads with predictable
            # names instead of relying on the parser to auto-ghost.
            bin_element = gst.gst_parse_bin_from_description(
                description.encode("utf-8"), 0, ctypes.byref(error)
            )
            if not bin_element:
                raise RuntimeError(f"failed to parse branch description: {description!r}")

            # For each entry element, fetch its static sink pad, wrap in a ghost
            # pad with a predictable name, and add the ghost to the bin. The
            # ghost pad takes a ref on the internal target pad, so we drop ours
            # immediately after add_pad.
            for index, entry_name in enumerate(entry_element_names):
                entry_element = gst.gst_bin_get_by_name(bin_element, entry_name.encode("utf-8"))
                if not entry_element:
                    raise RuntimeError(
                        f"branch description has no entry element named '{entry_name}'"
                    )
                entry_sink_pad = 0
                try:
                    entry_sink_pad = gst.gst_element_get_static_pad(entry_element, b"sink")
                    if not entry_sink_pad:
                        raise RuntimeError(
                            f"entry element '{entry_name}' has no static sink pad"
                        )
                    ghost_name = f"sink_{index}".encode("ascii")
                    ghost = gst.gst_ghost_pad_new(ghost_name, entry_sink_pad)
                    if not ghost:
                        raise RuntimeError(f"gst_ghost_pad_new failed for '{entry_name}'")
                    if not gst.gst_element_add_pad(bin_element, ghost):
                        gobject.g_object_unref(ghost)
                        raise RuntimeError(f"gst_element_add_pad rejected ghost for '{entry_name}'")
                    created_ghosts.append(ghost)
                finally:
                    if entry_sink_pad:
                        gobject.g_object_unref(entry_sink_pad)
                    gobject.g_object_unref(entry_element)

            if not gst.gst_bin_add(self.pipeline, bin_element):
                raise RuntimeError("gst_bin_add rejected the branch bin")
            added_to_pipeline = True

            # Link each tee to its ghost pad. Failure unwinds via the outer
            # except by releasing all request pads recorded so far.
            for (tap_name, tee), ghost in zip(tees, created_ghosts):
                tee_src_pad = self.runtime.request_tee_src_pad(tee) or 0
                if not tee_src_pad:
                    raise RuntimeError(f"tap '{tap_name}' refused a request src pad")
                link_result = gst.gst_pad_link(tee_src_pad, ghost)
                if link_result != 0:
                    gst.gst_element_release_request_pad(tee, tee_src_pad)
                    gobject.g_object_unref(tee_src_pad)
                    raise RuntimeError(
                        f"gst_pad_link failed (code {link_result}) for tap '{tap_name}'"
                    )
                link_records.append((tap_name, tee, tee_src_pad, ghost))

            if not gst.gst_element_sync_state_with_parent(bin_element):
                raise RuntimeError("branch failed to sync state with parent")

            handle = uuid.uuid4().hex
            first_tap, first_tee, first_tee_src, first_ghost = link_records[0]
            branch = AttachedBranch(
                handle=handle,
                tap_name=first_tap,
                description=description,
                bin_element=bin_element,
                tee_element=first_tee,
                tee_src_pad=first_tee_src,
                branch_sink_pad=first_ghost,
                tee_links=list(link_records),
            )
            with self._branch_lock:
                self._branches[handle] = branch
            return branch
        except Exception:
            for tap_name, tee, tee_src_pad, _ghost in link_records:
                if tee_src_pad:
                    gst.gst_element_release_request_pad(tee, tee_src_pad)
                    gobject.g_object_unref(tee_src_pad)
            if added_to_pipeline and bin_element:
                gst.gst_bin_remove(self.pipeline, bin_element)
            elif bin_element:
                gobject.g_object_unref(bin_element)
            for _tap_name, tee in tees:
                gobject.g_object_unref(tee)
            raise

    def attach_branch_outputs_multi(
        self,
        *,
        mixer_names: list[str],
        exit_element_names: list[str],
        description: str,
    ) -> AttachedBranch:
        """Attach a bin's N source pads to N spine audiomixers."""
        if self._stopped:
            raise RuntimeError("pipeline is stopped")
        if len(mixer_names) != len(exit_element_names):
            raise ValueError("mixer_names and exit_element_names must be parallel lists")
        if not mixer_names:
            raise ValueError("attach_branch_outputs_multi requires at least one mixer")
        gst = self.runtime.gst
        gobject = self.runtime.gobject

        mixers: list[tuple[str, int]] = []
        try:
            for mixer_name in mixer_names:
                mixer = gst.gst_bin_get_by_name(self.pipeline, mixer_name.encode("utf-8"))
                if not mixer:
                    raise RuntimeError(f"mixer '{mixer_name}' not found in pipeline '{self.name}'")
                mixers.append((mixer_name, mixer))
        except Exception:
            for _name, ptr in mixers:
                gobject.g_object_unref(ptr)
            raise

        bin_element = 0
        added_to_pipeline = False
        created_ghosts: list[int] = []
        link_records: list[tuple[str, int, int, int]] = []
        try:
            error = ctypes.c_void_p()
            bin_element = gst.gst_parse_bin_from_description(
                description.encode("utf-8"), 0, ctypes.byref(error)
            )
            if not bin_element:
                raise RuntimeError(f"failed to parse branch description: {description!r}")

            for index, exit_name in enumerate(exit_element_names):
                exit_element = gst.gst_bin_get_by_name(bin_element, exit_name.encode("utf-8"))
                if not exit_element:
                    raise RuntimeError(f"branch description has no exit element named '{exit_name}'")
                exit_src_pad = 0
                try:
                    exit_src_pad = gst.gst_element_get_static_pad(exit_element, b"src")
                    if not exit_src_pad:
                        raise RuntimeError(f"exit element '{exit_name}' has no static src pad")
                    ghost_name = f"src_{index}".encode("ascii")
                    template = (
                        gst.gst_pad_get_pad_template(exit_src_pad)
                        if hasattr(gst, "gst_pad_get_pad_template")
                        else 0
                    )
                    new_from_template = getattr(gst, "gst_ghost_pad_new_from_template", None)
                    ghost = (
                        new_from_template(ghost_name, exit_src_pad, template)
                        if new_from_template and template
                        else gst.gst_ghost_pad_new(ghost_name, exit_src_pad)
                    )
                    if not ghost:
                        raise RuntimeError(f"gst_ghost_pad_new failed for '{exit_name}'")
                    if not gst.gst_pad_set_active(ghost, 1):
                        gobject.g_object_unref(ghost)
                        raise RuntimeError(f"gst_pad_set_active failed for ghost '{exit_name}'")
                    if not gst.gst_element_add_pad(bin_element, ghost):
                        gobject.g_object_unref(ghost)
                        raise RuntimeError(f"gst_element_add_pad rejected ghost for '{exit_name}'")
                    created_ghosts.append(ghost)
                finally:
                    if exit_src_pad:
                        gobject.g_object_unref(exit_src_pad)
                    gobject.g_object_unref(exit_element)

            if not gst.gst_bin_add(self.pipeline, bin_element):
                raise RuntimeError("gst_bin_add rejected the branch bin")
            added_to_pipeline = True

            for index, ((mixer_name, mixer), ghost) in enumerate(zip(mixers, created_ghosts)):
                mixer_sink_pad = self.runtime.request_mixer_sink_pad(mixer) or 0
                if not mixer_sink_pad:
                    raise RuntimeError(f"mixer '{mixer_name}' refused a request sink pad")
                if not gst.gst_pad_set_active(mixer_sink_pad, 1):
                    gst.gst_element_release_request_pad(mixer, mixer_sink_pad)
                    gobject.g_object_unref(mixer_sink_pad)
                    raise RuntimeError(f"gst_pad_set_active failed for mixer '{mixer_name}' sink pad")
                pad_name = self.runtime.string_and_free(gst.gst_object_get_name(mixer_sink_pad))
                link_result = gst.gst_pad_link(ghost, mixer_sink_pad)
                if link_result != 0:
                    src_caps = self.runtime.pad_caps_string(ghost)
                    sink_caps = self.runtime.pad_caps_string(mixer_sink_pad)
                    gst.gst_element_release_request_pad(mixer, mixer_sink_pad)
                    gobject.g_object_unref(mixer_sink_pad)
                    raise RuntimeError(
                        f"gst_pad_link failed (code {link_result}) for mixer '{mixer_name}' pad '{pad_name}' "
                        f"(src caps: {src_caps}; sink caps: {sink_caps})"
                    )
                link_records.append((mixer_name, mixer, mixer_sink_pad, ghost))

            if not gst.gst_element_sync_state_with_parent(bin_element):
                raise RuntimeError("branch failed to sync state with parent")

            handle = uuid.uuid4().hex
            first_mixer, first_mixer_element, first_mixer_sink, first_ghost = link_records[0]
            branch = AttachedBranch(
                handle=handle,
                tap_name=first_mixer,
                description=description,
                bin_element=bin_element,
                tee_element=first_mixer_element,
                tee_src_pad=first_mixer_sink,
                branch_sink_pad=first_ghost,
                tee_links=list(link_records),
                link_direction="branch_to_mixer",
            )
            with self._branch_lock:
                self._branches[handle] = branch
            return branch
        except Exception:
            for _mixer_name, mixer, mixer_sink_pad, _ghost in link_records:
                if mixer_sink_pad:
                    gst.gst_element_release_request_pad(mixer, mixer_sink_pad)
                    gobject.g_object_unref(mixer_sink_pad)
            if added_to_pipeline and bin_element:
                gst.gst_bin_remove(self.pipeline, bin_element)
            elif bin_element:
                gobject.g_object_unref(bin_element)
            for _mixer_name, mixer in mixers:
                gobject.g_object_unref(mixer)
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
        # Use the link records when available so multi-tap branches release every
        # tee request pad. Older single-tap callers populate tee_links with one
        # entry; the unified loop handles both.
        links = branch.tee_links or [
            (branch.tap_name, branch.tee_element, branch.tee_src_pad, branch.branch_sink_pad)
        ]
        try:
            # For RX branches (branch_to_mixer), we must unlink BEFORE setting
            # the state to NULL, otherwise the shutting-down source elements
            # inside the branch will send an EOS downstream into our master
            # mixer, killing the entire spine playback chain.
            if branch.link_direction == "branch_to_mixer":
                for _tap, _tee, tee_src, ghost_pad in links:
                    if tee_src and ghost_pad:
                        gst.gst_pad_unlink(ghost_pad, tee_src)

            # For TX branches (tee_to_branch), we keep them linked and push an
            # explicit EOS into every ghost sink pad before the NULL transition.
            # set_state(NULL) blocks until the branch has fully stopped, so
            # sending EOS first lets srtsink flush its buffers and close cleanly.
            if branch.link_direction == "tee_to_branch":
                for _tap, _tee, _tee_src, ghost_pad in links:
                    if ghost_pad:
                        eos_event = gst.gst_event_new_eos()
                        if eos_event:
                            gst.gst_pad_send_event(ghost_pad, eos_event)

            gst.gst_element_set_state(branch.bin_element, GST_STATE_NULL)

            # TX branches wait to be unlinked until after they enter NULL state
            if branch.link_direction == "tee_to_branch":
                for _tap, _tee, tee_src, ghost_pad in links:
                    if tee_src and ghost_pad:
                        gst.gst_pad_unlink(tee_src, ghost_pad)

            gst.gst_bin_remove(self.pipeline, branch.bin_element)
            for _tap, tee, tee_src, _ghost in links:
                if tee_src:
                    gst.gst_element_release_request_pad(tee, tee_src)
        finally:
            for _tap, tee, tee_src, ghost_pad in links:
                if tee_src:
                    gobject.g_object_unref(tee_src)
                # Ghost pads were added to the branch bin, so the bin owns their
                # lifetime. We keep borrowed pointers only long enough to unlink
                # before removing the bin.
                if tee:
                    gobject.g_object_unref(tee)

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
        if not self.srt_endpoints:
            return
        now = time.time()
        emit_tail = self._last_stats_tail_at is None or now - self._last_stats_tail_at >= 1.0
        for transport_id, element_ptr in self.srt_endpoints:
            if not element_ptr:
                continue
            stats = ctypes.c_void_p()
            self.runtime.gobject.g_object_get(element_ptr, b"stats", ctypes.byref(stats), None)
            if not stats.value:
                continue
            text = self.runtime.string_and_free(self.runtime.gst.gst_structure_to_string(stats))
            if not text:
                continue
            if emit_tail:
                self.output_tail.append(f"srtstats[{transport_id}]: {text}")
            fields = _parse_stats(text)
            # Bitrate from byte-counter deltas — see notes on the single-pipeline
            # case before; here we keep a counter per transport_id so multi-leg
            # bundles compute deltas independently for each srtsink.
            bytes_total = _first_int(
                fields,
                "bytes-received-total", "bytes-sent-total",
                "pkti-recv-bytes", "pkti-send-bytes",
            )
            bitrate_kbps: float | None = None
            previous_bytes = self._last_bytes.get(transport_id)
            previous_at = self._last_stats_at.get(transport_id)
            if bytes_total is not None and previous_bytes is not None and previous_at is not None:
                elapsed = max(now - previous_at, 0.001)
                bitrate_kbps = max((bytes_total - previous_bytes) * 8 / elapsed / 1000, 0)
            if bitrate_kbps is None:
                send_rate = _mbps_to_kbps(_first_float(fields, "send-rate-mbps"))
                recv_rate = _mbps_to_kbps(_first_float(fields, "receive-rate-mbps", "recv-rate-mbps"))
                bitrate_kbps = recv_rate if (recv_rate and recv_rate > 0) else send_rate
            if bytes_total is not None:
                self._last_bytes[transport_id] = bytes_total
                self._last_stats_at[transport_id] = now
            packets_sent = _first_int(fields, "packets-sent", "packets-received")
            packets_lost = _first_int(
                fields,
                "packets-sent-lost", "packets-received-lost",
                "pkti-send-loss", "pkti-recv-loss",
                "packets-lost", "pkt-snd-loss-total", "pkt-rcv-loss-total",
            )
            self.telemetry.observe_srt_transport(
                transport_id,
                bitrate_kbps=bitrate_kbps,
                send_bitrate_kbps=bitrate_kbps,
                rtt_ms=_first_float(fields, "pkti-link-rtt-ms", "ms-rtt", "rtt-ms", "rtt"),
                rtt_variance_ms=_first_float(fields, "pkti-link-jitter-ms", "rtt-variance-ms"),
                packets_lost=packets_lost,
                packet_loss_percent=_loss_percent(packets_lost, packets_sent),
                packets_retransmitted=_first_int(fields, "packets-retransmitted", "packets-received-retransmitted", "pkti-send-retrans", "pkti-recv-retrans", "pkt-retrans-total"),
                raw_stats=text,
            )
        if emit_tail:
            self._last_stats_tail_at = now

    def _observe_level(self, source_name: str | None, text: str) -> None:
        if not source_name or not source_name.startswith(("dbmeter_out_", "dbmeter_in_")):
            return
        match = _LEVEL_MESSAGE_RE.search(text)
        if match is None:
            return
        peak = _first_level_value(match.group("peak"))
        rms = _first_level_value(match.group("rms"))

        # Prefer the explicit lookup populated at bundle construction. Falls back
        # to legacy "trailing-integer is the channel" parsing for pipelines that
        # don't supply one (RX, single-transport TX, diagnostic monitors).
        lookup_hit = self.meter_lookup.get(source_name)
        if lookup_hit is not None:
            transport_id, direction, channel = lookup_hit
            if direction == "in":
                self.telemetry.observe_input_meter(channel, peak_dbfs=peak, rms_dbfs=rms, transport_id=transport_id)
            else:
                self.telemetry.observe_output_meter(channel, peak_dbfs=peak, rms_dbfs=rms, transport_id=transport_id)
            return

        channel_text = source_name.rsplit("_", 1)[-1]
        if not channel_text.isdigit():
            return
        # No transport attribution available — for single-transport pipelines we
        # still know who owns the pipeline, so attribute the meter to the first
        # (and only) transport id when present.
        fallback_transport = self.srt_endpoints[0][0] if self.srt_endpoints else None
        if source_name.startswith("dbmeter_in_"):
            self.telemetry.observe_input_meter(
                int(channel_text), peak_dbfs=peak, rms_dbfs=rms, transport_id=fallback_transport
            )
        else:
            self.telemetry.observe_output_meter(
                int(channel_text), peak_dbfs=peak, rms_dbfs=rms, transport_id=fallback_transport
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
