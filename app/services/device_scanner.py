from __future__ import annotations
import os
from dataclasses import dataclass
from typing import List

GARMIN_VOLUME_HINTS = [
    "GARMIN",
    "PRIMARY",
]

@dataclass
class DeviceMount:
    mount_point: str
    serial: str | None = None


def list_mass_storage_mounts() -> List[DeviceMount]:
    mounts: List[DeviceMount] = []
    # In a container, USB passthrough may mount under /media or /mnt.
    # We'll scan a few common roots. On Windows host with docker, use volume mounts.
    for root in ["/media", "/mnt", "/usb", "/data/mnt"]:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            # Heuristic: presence of GARMIN folder or .fit files
            garmin_dir = os.path.join(path, "GARMIN")
            if os.path.isdir(garmin_dir) or name.upper() in GARMIN_VOLUME_HINTS:
                mounts.append(DeviceMount(mount_point=path))
    return mounts


def find_fit_files(mount: DeviceMount) -> List[str]:
    fit_files: List[str] = []
    for base in ["", "GARMIN", "GARMIN/Activity", "GARMIN/Activities"]:
        candidate = os.path.join(mount.mount_point, base)
        if not os.path.isdir(candidate):
            continue
        for root, _, files in os.walk(candidate):
            for f in files:
                if f.lower().endswith(".fit"):
                    fit_files.append(os.path.join(root, f))
    return fit_files
