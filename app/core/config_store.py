from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import tomllib
import tomli_w
from pydantic import SecretStr

from app.core.config import DEFAULT_CONFIG_PATH, EndpointConfig, SourceConfig, SourceKind


SILENCE_DEFAULT_SOURCE_ID = "silence-default"


def _slugify(value: str) -> str:
    chars: list[str] = []
    last_dash = False
    for ch in (value or "").lower():
        if ch.isalnum():
            chars.append(ch)
            last_dash = False
        elif not last_dash:
            chars.append("-")
            last_dash = True
    return "".join(chars).strip("-")


def _plain(value: Any, redact_secrets: bool = False) -> Any:
    if isinstance(value, SecretStr):
        return "********" if redact_secrets else value.get_secret_value()
    if isinstance(value, dict):
        return {key: _plain(item, redact_secrets) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_plain(item, redact_secrets) for item in value]
    return value


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        env_path = os.environ.get("DANTEBRIDGE_CONFIG_PATH")
        self.path = Path(env_path) if env_path else (path or DEFAULT_CONFIG_PATH)
        self._config = self.load()
        mutated = self._ensure_seed_sources()
        if self._migrate_source_devices():
            mutated = True
        # One-time migration: every registered dante_input gets a dante_output
        # twin on the same device + channel so RX has a destination.
        if self._seed_output_twins_for_existing_inputs():
            mutated = True
        # One-time migration: rewrite RX encode-group channel source_ids that
        # still point at dante_input sources to their dante_output twin.
        if self._migrate_rx_channel_sources():
            mutated = True
        # One-time migration: drop legacy global `dante-in-NN` / `dante-out-NN`
        # sources, redirecting any references to the equivalent device-scoped
        # source. Sources are now only created by add_audio_device.
        if self._purge_legacy_global_sources():
            mutated = True
        if mutated:
            self.save()

    @property
    def config(self) -> EndpointConfig:
        return self._config

    def load(self) -> EndpointConfig:
        if not self.path.exists():
            return EndpointConfig()
        with self.path.open("rb") as file:
            raw = tomllib.load(file)
        return EndpointConfig.model_validate(raw)

    def save(self, config: EndpointConfig | None = None) -> EndpointConfig:
        if config is not None:
            self._config = config

        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = _plain(self._config.model_dump(mode="python", exclude_none=True))
        encoded = tomli_w.dumps(data)

        with tempfile.NamedTemporaryFile("w", delete=False, dir=self.path.parent, encoding="utf-8") as file:
            file.write(encoded)
            temp_name = file.name
        os.replace(temp_name, self.path)
        return self._config

    def update(self, patch: dict[str, Any]) -> EndpointConfig:
        data = self._config.model_dump(mode="python")
        merged = _deep_merge(data, patch)
        self._config = EndpointConfig.model_validate(merged)
        return self.save()

    def add_audio_device(
        self,
        interface_name: str,
        interface_driver: str | None,
        interface_device_id: str | None,
        channel_count: int,
    ) -> list[str]:
        """Seed both dante_input (TX capture) and dante_output (RX playback)
        sources for an additional device using '{slug}-in-NN' / '{slug}-out-NN'
        IDs so multiple devices coexist. Returns newly created source IDs."""
        slug = _slugify(interface_name) or "device"
        sources = list(self._config.sources)
        existing = {s.id for s in sources}
        added: list[str] = []
        for ch in range(1, channel_count + 1):
            for direction, kind, label in (
                ("in", SourceKind.dante_input, "In"),
                ("out", SourceKind.dante_output, "Out"),
            ):
                sid = f"{slug}-{direction}-{ch:02d}"
                if sid in existing:
                    continue
                sources.append(SourceConfig(
                    id=sid,
                    name=f"{interface_name} · {label} {ch:02d}",
                    kind=kind,
                    dante_channel=ch,
                    interface_name=interface_name,
                    interface_driver=interface_driver,
                    interface_device_id=interface_device_id,
                ))
                added.append(sid)
        if added:
            data = self._config.model_dump(mode="python")
            data["sources"] = [s.model_dump(mode="python") for s in sources]
            self._config = EndpointConfig.model_validate(data)
            self.save()
        return added

    def remove_audio_device(self, interface_name: str) -> list[str]:
        """Remove all dante_input and dante_output sources tied to a device. Returns IDs removed."""
        keep: list[SourceConfig] = []
        removed: list[str] = []
        for source in self._config.sources:
            if (
                source.kind in (SourceKind.dante_input, SourceKind.dante_output)
                and source.interface_name == interface_name
            ):
                removed.append(source.id)
                continue
            keep.append(source)
        if not removed:
            return []
        data = self._config.model_dump(mode="python")
        data["sources"] = [s.model_dump(mode="python") for s in keep]
        self._config = EndpointConfig.model_validate(data)
        self.save()
        return removed

    def _migrate_source_devices(self) -> bool:
        """Backfill per-source device fields from the global AudioConfig for any
        legacy dante source missing them. Returns True if config was mutated."""
        global_iface = self._config.audio.interface_name
        global_driver = self._config.audio.interface_driver
        global_device_id = self._config.audio.interface_device_id
        if not global_iface and global_driver in (None, "unknown"):
            return False
        sources = list(self._config.sources)
        changed = False
        for i, source in enumerate(sources):
            if source.kind not in (SourceKind.dante_input, SourceKind.dante_output):
                continue
            if source.interface_name and source.interface_driver and source.interface_driver != "unknown":
                continue
            sources[i] = source.model_copy(update={
                "interface_name": source.interface_name or global_iface,
                "interface_driver": source.interface_driver if (source.interface_driver and source.interface_driver != "unknown") else global_driver,
                "interface_device_id": source.interface_device_id or global_device_id,
            })
            changed = True
        if not changed:
            return False
        data = self._config.model_dump(mode="python")
        data["sources"] = [s.model_dump(mode="python") for s in sources]
        self._config = EndpointConfig.model_validate(data)
        return True

    def _seed_output_twins_for_existing_inputs(self) -> bool:
        """For every dante_input source already present, ensure a dante_output
        sibling exists on the same device + channel. Output IDs use the same
        slug-based convention as add_audio_device. Returns True if mutated."""
        sources = list(self._config.sources)
        existing_outputs = {
            (s.interface_name, s.dante_channel)
            for s in sources
            if s.kind == SourceKind.dante_output
        }
        added = False
        for s in list(sources):
            if s.kind != SourceKind.dante_input:
                continue
            key = (s.interface_name, s.dante_channel)
            if key in existing_outputs:
                continue
            slug = _slugify(s.interface_name or "") or "device"
            ch = s.dante_channel or 1
            sid = f"{slug}-out-{ch:02d}"
            # Avoid id collision if a user already created a custom output with that id.
            if any(x.id == sid for x in sources):
                continue
            sources.append(SourceConfig(
                id=sid,
                name=f"{s.interface_name or 'Device'} · Out {ch:02d}",
                kind=SourceKind.dante_output,
                dante_channel=ch,
                interface_name=s.interface_name,
                interface_driver=s.interface_driver,
                interface_device_id=s.interface_device_id,
            ))
            existing_outputs.add(key)
            added = True
        if not added:
            return False
        data = self._config.model_dump(mode="python")
        data["sources"] = [s.model_dump(mode="python") for s in sources]
        self._config = EndpointConfig.model_validate(data)
        return True

    def _purge_legacy_global_sources(self) -> bool:
        """Remove legacy `dante-in-NN` / `dante-out-NN` sources. For each one
        with a device-scoped twin (same interface_name + dante_channel + kind),
        rewrite encode-group channel references to the twin first. Orphans get
        deleted; the validator will surface broken references rather than
        silently routing somewhere. Returns True if mutated."""
        import re

        legacy_pattern = re.compile(r"^dante-(in|out)-\d{2,}$")
        legacy: list[SourceConfig] = []
        kept: list[SourceConfig] = []
        for s in self._config.sources:
            if (
                s.kind in (SourceKind.dante_input, SourceKind.dante_output)
                and legacy_pattern.match(s.id)
            ):
                legacy.append(s)
            else:
                kept.append(s)
        if not legacy:
            return False

        twin_by_key: dict[tuple[SourceKind, str | None, int | None], str] = {}
        for s in kept:
            if s.kind in (SourceKind.dante_input, SourceKind.dante_output):
                twin_by_key[(s.kind, s.interface_name, s.dante_channel)] = s.id

        rewrite: dict[str, str] = {}
        for s in legacy:
            twin = twin_by_key.get((s.kind, s.interface_name, s.dante_channel))
            if twin:
                rewrite[s.id] = twin

        new_groups = []
        for group in self._config.encode_groups:
            new_channels = []
            for ch in group.channels:
                new_sid = rewrite.get(ch.source_id or "")
                if new_sid:
                    new_channels.append(ch.model_copy(update={"source_id": new_sid}))
                else:
                    new_channels.append(ch)
            new_groups.append(group.model_copy(update={"channels": new_channels}))

        data = self._config.model_dump(mode="python")
        data["sources"] = [s.model_dump(mode="python") for s in kept]
        data["encode_groups"] = [g.model_dump(mode="python") for g in new_groups]
        self._config = EndpointConfig.model_validate(data)
        return True

    def _migrate_rx_channel_sources(self) -> bool:
        """Rewrite RX encode-group channel source_ids that reference a
        dante_input source to the matching dante_output twin (same
        interface_name + dante_channel). Returns True if mutated."""
        from app.core.config import SrtTransportDirection

        sources_by_id = {s.id: s for s in self._config.sources}
        # Build (interface_name, dante_channel) -> dante_output id index.
        output_by_key: dict[tuple[str | None, int | None], str] = {}
        for s in self._config.sources:
            if s.kind == SourceKind.dante_output:
                output_by_key[(s.interface_name, s.dante_channel)] = s.id

        rx_group_ids: set[str] = set()
        for transport in self._config.srt_transports:
            if transport.direction == SrtTransportDirection.rx:
                rx_group_ids.update(transport.encode_group_ids)
        if not rx_group_ids:
            return False

        changed = False
        new_groups = []
        for group in self._config.encode_groups:
            if group.id not in rx_group_ids:
                new_groups.append(group)
                continue
            new_channels = []
            for ch in group.channels:
                src = sources_by_id.get(ch.source_id or "")
                if src is not None and src.kind == SourceKind.dante_input:
                    twin = output_by_key.get((src.interface_name, src.dante_channel))
                    if twin:
                        new_channels.append(ch.model_copy(update={"source_id": twin}))
                        changed = True
                        continue
                new_channels.append(ch)
            new_groups.append(group.model_copy(update={"channels": new_channels}))

        if not changed:
            return False
        data = self._config.model_dump(mode="python")
        data["encode_groups"] = [g.model_dump(mode="python") for g in new_groups]
        self._config = EndpointConfig.model_validate(data)
        return True

    def _ensure_seed_sources(self) -> bool:
        """Idempotently seed the global silence-default source.
        Returns True if config was mutated."""
        if any(s.id == SILENCE_DEFAULT_SOURCE_ID for s in self._config.sources):
            return False
        seeded = SourceConfig(
            id=SILENCE_DEFAULT_SOURCE_ID,
            name="Silence",
            kind=SourceKind.silence,
        )
        data = self._config.model_dump(mode="python")
        data.setdefault("sources", []).insert(0, seeded.model_dump(mode="python"))
        self._config = EndpointConfig.model_validate(data)
        return True

    def export(self, include_secrets: bool = False) -> dict[str, Any]:
        return _plain(self._config.model_dump(mode="python", exclude_none=True), redact_secrets=not include_secrets)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
