from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


_FALLBACK_AUDIO_INTERFACES = [
    {
        "id": "fallback:dante-virtual-soundcard",
        "name": "Dante Virtual Soundcard",
        "driver": "wasapi",
        "direction": "duplex",
        "sample_rate": 48000,
        "source": "fallback",
    },
    {
        "id": "fallback:system-default-output",
        "name": "System Default Output",
        "driver": "wasapi",
        "direction": "output",
        "sample_rate": 48000,
        "source": "fallback",
    },
]


_SUPPORTED_CONFIG_DRIVERS = {"wasapi", "coreaudio", "alsa", "asio", "unknown"}


def discover_audio_interfaces(gst_launch_executable: str = "gst-launch-1.0") -> list[dict[str, Any]]:
    devices = _discover_gstreamer_devices(gst_launch_executable)
    if not devices:
        devices = _discover_platform_devices()
        
    if sys.platform == "win32":
        # Strip {} from GStreamer device_ids so registry matches logic works
        seen_ids = {str(d.get("device_id")).strip("{}").lower() for d in devices if d.get("device_id")}
        for asio_dev in _discover_windows_asio_devices():
            asio_id = str(asio_dev.get("device_id")).strip("{}").lower()
            if asio_id not in seen_ids:
                devices.append(asio_dev)
                seen_ids.add(asio_id)
                
    merged = devices or [dict(device) for device in _FALLBACK_AUDIO_INTERFACES]
    
    unique_devices = {}
    for d in merged:
        key = (d.get("name"), d.get("driver"))
        if key not in unique_devices:
            unique_devices[key] = d
        else:
            # If we already have it but found another direction, mark it duplex
            existing = unique_devices[key]
            if existing.get("direction") != d.get("direction") and existing.get("direction") != "duplex":
                existing["direction"] = "duplex"
                
    return list(unique_devices.values())


def normalize_config_driver(driver: str | None) -> str:
    normalized = _normalize_driver(driver)
    return normalized if normalized in _SUPPORTED_CONFIG_DRIVERS else "unknown"


def _discover_gstreamer_devices(gst_launch_executable: str) -> list[dict[str, Any]]:
    executable = _gst_device_monitor_executable(gst_launch_executable)
    if executable is None:
        return []
    try:
        completed = subprocess.run(
            [executable, "Audio/Source", "Audio/Sink"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    return _parse_gst_device_monitor(completed.stdout)


def _gst_device_monitor_executable(gst_launch_executable: str) -> str | None:
    launch_path = Path(gst_launch_executable)
    monitor_name = "gst-device-monitor-1.0.exe" if launch_path.suffix.lower() == ".exe" else "gst-device-monitor-1.0"
    if launch_path.is_file():
        candidate = launch_path.with_name(monitor_name)
        if candidate.is_file():
            return str(candidate)
    return shutil.which(monitor_name)


def _parse_gst_device_monitor(output: str) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_properties = False
    launch_line: str | None = None

    def finish() -> None:
        nonlocal current, launch_line
        if current is None:
            return
        name = current.get("name") or current.get("properties", {}).get("device.description")
        device_class = str(current.get("class") or "")
        if not name or "Audio" not in device_class:
            current = None
            launch_line = None
            return
        properties = current.get("properties", {})
        driver = _normalize_driver(
            properties.get("device.api")
            or properties.get("device.provider")
            or launch_line
            or sys.platform
        )
        direction = _direction_from_gst_class(device_class)
        device_id = (
            properties.get("device.id")
            or properties.get("device.name")
            or properties.get("device.strid")
            or _device_id_from_launch_line(launch_line)
            or launch_line
            or name
        )
        stable_id = f"{direction}:{device_id}"
        devices.append({
            "id": f"gst:{_stable_token(driver)}:{_stable_token(str(stable_id))}",
            "name": str(name),
            "driver": driver,
            "direction": direction,
            "sample_rate": 48000,
            "device_id": str(device_id),
            "gst_class": device_class,
            "gst_launch": launch_line,
            "source": "gstreamer",
        })
        current = None
        launch_line = None

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Device found"):
            finish()
            current = {"properties": {}}
            in_properties = False
            continue
        if current is None:
            continue
        if stripped == "properties:":
            in_properties = True
            continue
        if stripped.startswith("gst-launch-1.0"):
            launch_line = stripped
            in_properties = False
            continue
        key, sep, value = stripped.partition(":")
        if sep:
            if in_properties:
                prop_key, prop_sep, prop_value = stripped.partition("=")
                if prop_sep:
                    current["properties"][prop_key.strip()] = prop_value.strip().strip('"')
            else:
                current[key.strip()] = value.strip()
            continue
        if in_properties and "=" in stripped:
            prop_key, _, prop_value = stripped.partition("=")
            current["properties"][prop_key.strip()] = prop_value.strip().strip('"')

    finish()
    return devices


def _discover_platform_devices() -> list[dict[str, Any]]:
    if sys.platform == "win32":
        return _discover_windows_audio_endpoints()
    return []


def _discover_windows_audio_endpoints() -> list[dict[str, Any]]:
    rows = _windows_pnp_audio_endpoints() or _windows_sound_devices()
    devices: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("FriendlyName") or row.get("Name")
        if not name:
            continue
        instance_id = str(row.get("InstanceId") or name)
        devices.append({
            "id": f"windows:{_stable_token(instance_id)}",
            "name": str(name),
            "driver": "wasapi",
            "direction": _direction_from_windows_name(str(name)),
            "sample_rate": 48000,
            "device_id": instance_id,
            "state": row.get("Status"),
            "source": "windows",
        })
    return devices


def _windows_pnp_audio_endpoints() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-PnpDevice -Class AudioEndpoint | Select-Object FriendlyName,InstanceId,Status | ConvertTo-Json -Depth 3",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    return _json_rows(completed.stdout)


def _windows_sound_devices() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_SoundDevice | Select-Object Name,DeviceID,Status | ConvertTo-Json -Depth 3",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    rows = _json_rows(completed.stdout)
    for row in rows:
        if "DeviceID" in row:
            row["InstanceId"] = row["DeviceID"]
    return rows


def _json_rows(raw: str) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    rows = decoded if isinstance(decoded, list) else [decoded]
    return [row for row in rows if isinstance(row, dict)]


def _direction_from_gst_class(device_class: str) -> str:
    if "Source" in device_class:
        return "input"
    if "Sink" in device_class:
        return "output"
    return "duplex"


def _direction_from_windows_name(name: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ("microphone", "input", "capture", "line in")):
        return "input"
    if any(token in lowered for token in ("speaker", "headphone", "output", "render")):
        return "output"
    return "duplex"


def _normalize_driver(value: str | None) -> str:
    lowered = (value or "").lower()
    if "wasapi" in lowered or lowered == "win32":
        return "wasapi"
    if "asio" in lowered:
        return "asio"
    if "coreaudio" in lowered or "osx" in lowered or "darwin" in lowered:
        return "coreaudio"
    if "alsa" in lowered or "linux" in lowered:
        return "alsa"
    return "unknown"


def _device_id_from_launch_line(launch_line: str | None) -> str | None:
    if not launch_line:
        return None
    clsid_match = re.search(r"device-clsid='?([^'\s]+)'?", launch_line)
    if clsid_match:
        return clsid_match.group(1).strip("{}")
    device_match = re.search(r"device='?([^'\s]+)'?", launch_line)
    if device_match:
        return device_match.group(1).strip("{}")
    return None


def _stable_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return token or "device"


def platform_audio_driver() -> str:
    return _normalize_driver(platform.system())


def _discover_windows_asio_devices() -> list[dict[str, Any]]:
    import winreg
    
    devices: list[dict[str, Any]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ASIO") as asio_key:
            for i in range(1024):
                try:
                    sub_key_name = winreg.EnumKey(asio_key, i)
                    with winreg.OpenKey(asio_key, sub_key_name) as sub_key:
                        clsid, _ = winreg.QueryValueEx(sub_key, "CLSID")
                        try:
                            desc, _ = winreg.QueryValueEx(sub_key, "Description")
                        except OSError:
                            desc = sub_key_name
                            
                        devices.append({
                            "id": f"asio:{_stable_token(clsid)}",
                            "name": str(desc),
                            "driver": "asio",
                            "direction": "duplex",
                            "sample_rate": 48000,
                            "device_id": str(clsid),
                            "source": "windows_registry",
                        })
                except OSError:
                    break
    except OSError:
        pass
        
    return devices

