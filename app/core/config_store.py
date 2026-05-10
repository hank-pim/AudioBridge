from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import tomllib
import tomli_w
from pydantic import SecretStr

from app.core.config import DEFAULT_CONFIG_PATH, EndpointConfig


def _plain(value: Any, redact_secrets: bool = False) -> Any:
    if isinstance(value, SecretStr):
        return "********" if redact_secrets else value.get_secret_value()
    if isinstance(value, dict):
        return {key: _plain(item, redact_secrets) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_plain(item, redact_secrets) for item in value]
    return value


class ConfigStore:
    def __init__(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.path = path
        self._config = self.load()

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
