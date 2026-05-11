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


def _dante_input_source_id(channel: int) -> str:
    return f"dante-in-{channel:02d}"


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
        if self._config.audio.channel_count > 0:
            existing = {s.id for s in self._config.sources}
            for ch in range(1, self._config.audio.channel_count + 1):
                if _dante_input_source_id(ch) not in existing:
                    mutated = True
                    break
        if mutated:
            # Persist seed sources; sync_dante_input_sources will fill in dante-in-XX.
            self.save()
            self.sync_dante_input_sources(self._config.audio.channel_count)

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

    def sync_dante_input_sources(self, channel_count: int) -> EndpointConfig:
        """Ensure dante-in-01..NN sources exist for the active channel count.
        New sources inherit the global default device (config.audio.*) so the
        pipeline can open a capture for them. Existing sources are left alone.
        Does not prune extras — operators may have channels temporarily unrouted."""
        existing = {source.id for source in self._config.sources}
        sources = list(self._config.sources)
        default_iface = self._config.audio.interface_name
        default_driver = self._config.audio.interface_driver
        default_device_id = self._config.audio.interface_device_id
        added = False
        for ch in range(1, channel_count + 1):
            sid = _dante_input_source_id(ch)
            if sid in existing:
                continue
            sources.append(SourceConfig(
                id=sid,
                name=f"Dante In {ch:02d}",
                kind=SourceKind.dante_input,
                dante_channel=ch,
                interface_name=default_iface,
                interface_driver=default_driver,
                interface_device_id=default_device_id,
            ))
            added = True
        if added:
            data = self._config.model_dump(mode="python")
            data["sources"] = [s.model_dump(mode="python") for s in sources]
            self._config = EndpointConfig.model_validate(data)
            self.save()
        return self._config

    def add_audio_device(
        self,
        interface_name: str,
        interface_driver: str | None,
        interface_device_id: str | None,
        channel_count: int,
    ) -> list[str]:
        """Seed dante_input sources for an additional capture device using
        '{slug}-in-NN' IDs so multiple devices coexist. Returns the list of
        newly created source IDs (existing IDs are preserved)."""
        slug = _slugify(interface_name) or "device"
        sources = list(self._config.sources)
        existing = {s.id for s in sources}
        added: list[str] = []
        for ch in range(1, channel_count + 1):
            sid = f"{slug}-in-{ch:02d}"
            if sid in existing:
                continue
            sources.append(SourceConfig(
                id=sid,
                name=f"{interface_name} · Ch {ch:02d}",
                kind=SourceKind.dante_input,
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
        """Remove all dante_input sources for a registered device. Returns IDs removed."""
        keep: list[SourceConfig] = []
        removed: list[str] = []
        for source in self._config.sources:
            if source.kind == SourceKind.dante_input and source.interface_name == interface_name:
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
        legacy dante_input source. Returns True if config was mutated."""
        global_iface = self._config.audio.interface_name
        global_driver = self._config.audio.interface_driver
        global_device_id = self._config.audio.interface_device_id
        if not global_iface and global_driver in (None, "unknown"):
            return False
        sources = list(self._config.sources)
        changed = False
        for i, source in enumerate(sources):
            if source.kind != SourceKind.dante_input:
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
