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
        Adds missing entries; preserves user edits to existing ones.
        Does not prune extras — operators may have channels temporarily unrouted."""
        existing = {source.id for source in self._config.sources}
        sources = list(self._config.sources)
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
            ))
            added = True
        if added:
            data = self._config.model_dump(mode="python")
            data["sources"] = [s.model_dump(mode="python") for s in sources]
            self._config = EndpointConfig.model_validate(data)
            self.save()
        return self._config

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
