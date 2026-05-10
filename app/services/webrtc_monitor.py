from __future__ import annotations

import asyncio
import fractions
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
import av
import numpy as np

from app.core.config import EndpointConfig
from app.services.media import MediaController
from app.services.media_graph import MediaGraphBuilder


_SAMPLES_PER_FRAME = 960  # 20 ms at 48 kHz
_FRAME_INTERVAL_S = _SAMPLES_PER_FRAME / 48000


class _AppsinkAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._queue: asyncio.Queue[av.AudioFrame] = asyncio.Queue(maxsize=50)
        self._pts: int = 0
        self._closed = False

    async def recv(self) -> av.AudioFrame:
        if self._closed:
            raise asyncio.CancelledError()
        return await self._queue.get()

    def push_pcm(self, pcm_bytes: bytes, loop: asyncio.AbstractEventLoop) -> None:
        if self._closed or not pcm_bytes:
            return
        samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        if samples.size == 0:
            return
        offset = 0
        while offset < samples.size:
            chunk = samples[offset : offset + _SAMPLES_PER_FRAME]
            offset += _SAMPLES_PER_FRAME
            if chunk.size < _SAMPLES_PER_FRAME:
                # Pad the trailing fragment so aiortc gets a full 20 ms frame.
                padded = np.zeros(_SAMPLES_PER_FRAME, dtype=np.int16)
                padded[: chunk.size] = chunk
                chunk = padded
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = 48000
            frame.time_base = fractions.Fraction(1, 48000)
            frame.pts = self._pts
            self._pts += _SAMPLES_PER_FRAME
            try:
                loop.call_soon_threadsafe(self._queue.put_nowait, frame)
            except RuntimeError:
                return

    def close_track(self) -> None:
        self._closed = True


@dataclass
class _Session:
    id: str
    transport_id: str
    tap_id: str
    peer_connection: RTCPeerConnection
    track: _AppsinkAudioTrack
    branch_handle: str
    pipeline: Any
    appsink_ptr: int
    pull_thread: threading.Thread
    stop_event: threading.Event = field(default_factory=threading.Event)
    created_at: float = field(default_factory=time.time)


class WebRtcMonitorService:
    def __init__(self, media: MediaController) -> None:
        self._media = media
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    async def create_session(
        self, config: EndpointConfig, transport_id: str, tap_id: str | None = None
    ) -> dict[str, Any]:
        pipeline = self._media._srt_transport_pipelines.get(transport_id)
        if pipeline is None:
            raise RuntimeError(f"SRT transport '{transport_id}' is not running")
        if not hasattr(pipeline, "attach_branch"):
            raise RuntimeError(f"SRT transport '{transport_id}' is not on the managed runtime")

        plan = MediaGraphBuilder(gst_launch_executable=self._media.gst_launch_executable).plan_srt_transport(
            config, transport_id, raise_on_error=True
        )
        taps = plan["gstreamer"]["monitor_taps"]
        if tap_id is None:
            if not taps:
                raise ValueError(f"transport '{transport_id}' has no monitor taps")
            tap_id = taps[0]["id"]
        elif not any(tap["id"] == tap_id for tap in taps):
            raise ValueError(f"tap '{tap_id}' is not exposed by transport '{transport_id}'")

        runtime = self._media._gst_runtime
        if runtime is None:
            raise RuntimeError("GStreamer runtime not initialized")
        if runtime.gstapp is None:
            raise RuntimeError("gstapp library not available; cannot create appsink branch")
        runtime.bind_appsink()

        session_id = uuid.uuid4().hex
        appsink_name = f"monitor_appsink_{session_id}"
        description = (
            "queue max-size-buffers=50 leaky=downstream ! "
            "opusdec ! audioconvert ! audioresample ! "
            "audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved ! "
            f"appsink name={appsink_name} sync=false max-buffers=20 drop=true"
        )
        branch = pipeline.attach_branch(tap_name=tap_id, description=description)
        appsink_ptr = runtime.gst.gst_bin_get_by_name(pipeline.pipeline, appsink_name.encode("utf-8"))
        if not appsink_ptr:
            pipeline.detach_branch(branch.handle)
            raise RuntimeError(f"failed to locate appsink '{appsink_name}' after attach")

        track = _AppsinkAudioTrack()
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
        pc.addTrack(track)

        loop = asyncio.get_running_loop()
        stop_event = threading.Event()

        def pull_loop() -> None:
            timeout_ns = int(_FRAME_INTERVAL_S * 1_000_000_000)
            while not stop_event.is_set():
                pcm = runtime.pull_appsink_sample(appsink_ptr, timeout_ns)
                if pcm:
                    track.push_pcm(pcm, loop)

        thread = threading.Thread(target=pull_loop, name=f"monitor_pull_{session_id}", daemon=True)
        thread.start()

        session = _Session(
            id=session_id,
            transport_id=transport_id,
            tap_id=tap_id,
            peer_connection=pc,
            track=track,
            branch_handle=branch.handle,
            pipeline=pipeline,
            appsink_ptr=appsink_ptr,
            pull_thread=thread,
            stop_event=stop_event,
        )

        @pc.on("connectionstatechange")
        async def _on_state_change() -> None:
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                await self.close_session(session_id)

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        with self._lock:
            self._sessions[session_id] = session

        return {
            "session_id": session_id,
            "transport_id": transport_id,
            "tap_id": tap_id,
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        }

    async def set_answer(self, session_id: str, sdp: str, type_: str) -> None:
        session = self._get(session_id)
        await session.peer_connection.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=type_))

    async def close_session(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.stop_event.set()
        session.track.close_track()
        try:
            await session.peer_connection.close()
        except Exception:
            pass
        try:
            session.pipeline.detach_branch(session.branch_handle)
        except Exception:
            pass
        if session.appsink_ptr:
            runtime = self._media._gst_runtime
            if runtime is not None:
                runtime.gobject.g_object_unref(session.appsink_ptr)
        session.pull_thread.join(timeout=1)
        return True

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "session_id": s.id,
                    "transport_id": s.transport_id,
                    "tap_id": s.tap_id,
                    "connection_state": s.peer_connection.connectionState,
                    "created_at": s.created_at,
                }
                for s in self._sessions.values()
            ]

    async def shutdown(self) -> None:
        for session_id in list(self._sessions):
            await self.close_session(session_id)

    def _get(self, session_id: str) -> _Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"monitor session '{session_id}' not found")
        return session
