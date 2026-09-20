"""Read-only inspection and controlled start of the Armada installer.

The Decky process never imports or executes the installer itself.  Inspection
and preparation only read state; the single mutation is a fixed systemd unit
handed to ``systemd-run`` after the caller confirms a previously prepared plan.
"""

import json
import os
import secrets
import subprocess
import threading
from pathlib import Path

INSTALLER = "/usr/libexec/armada/armada-installer"
UNIT = "armada-installer-plugin.service"
DEFAULT_LOG = Path("/run/armada-installer-plugin.log")
DEFAULT_POWER = Path("/sys/class/power_supply")
LOG_BYTES = 65536
LOG_LINES = 40
# Exact strings the original installer prints, mapped to a coarse progress phase.
PHASES = (("Preparing the internal filesystems", "preparing"),
          ("Checking the complete source repository", "preparing"),
          ("Copying and verifying the selected image", "copying"),
          ("Writing the internal boot image", "boot"))


class InstallerError(Exception):
    """An expected failure with a stable machine-readable code."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _capture(runner, argv, timeout=30):
    return runner(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)


def _read_int(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _detect(runner):
    """Ask the original installer for the current internal storage layout."""
    try:
        result = _capture(runner, [INSTALLER, "detect", "--json"])
    except (OSError, subprocess.SubprocessError) as error:
        raise InstallerError("installer_unavailable", str(error)) from error
    if result.returncode:
        detail = (result.stderr or "").strip() or "the installer could not inspect internal storage"
        raise InstallerError("inspection_failed", detail)
    try:
        info = json.loads(result.stdout)
    except (TypeError, ValueError) as error:
        raise InstallerError("inspection_failed", f"invalid detect output: {error}") from error
    if not isinstance(info, dict):
        raise InstallerError("inspection_failed", "detect did not return an object")
    return info


def _check_boot_disk(device, runner):
    """Refuse to touch the whole disk the running system booted from."""
    try:
        mount = "/sysroot" if Path("/sysroot").is_mount() else "/"
        result = _capture(runner, ["findmnt", "--noheadings", "--output", "SOURCE", "--target", mount])
        if result.returncode:
            raise InstallerError("boot_disk_unknown", "could not determine the running system's boot source")
        source = (result.stdout or "").strip().split("[", 1)[0]
        if not source or not Path(source).is_block_device():
            raise InstallerError("boot_disk_unknown", f"{source or 'the boot source'} is not a block device")
        result = _capture(runner, ["lsblk", "--inverse", "--noheadings", "--raw", "--output", "MAJ:MIN", source])
        if result.returncode:
            raise InstallerError("boot_disk_unknown", "could not trace the boot disk ancestry")
        ancestors = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        ident = os.stat(device).st_rdev
        if not ancestors or f"{os.major(ident)}:{os.minor(ident)}" in ancestors:
            raise InstallerError("boot_disk_forbidden", str(device))
    except InstallerError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise InstallerError("boot_disk_unknown", str(error)) from error


def _power(power_dir):
    """Summarise sysfs power supplies; unknown is reported as None, not False."""
    external, percent = None, None
    try:
        supplies = sorted(Path(power_dir).iterdir())
    except OSError:
        return {"external_power": None, "battery_percent": None}
    for supply in supplies:
        try:
            kind = (supply / "type").read_text().strip()
        except OSError:
            continue
        if kind == "Battery":
            if percent is None:
                percent = _read_int(supply / "capacity")
            continue
        online = _read_int(supply / "online")
        if online is None:
            continue
        external = bool(external) or bool(online)
    return {"external_power": external, "battery_percent": percent}


def _parse_show(text):
    fields = {}
    for line in (text or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def _state_of(fields):
    load, active = fields.get("LoadState", ""), fields.get("ActiveState", "")
    sub, result, code = fields.get("SubState", ""), fields.get("Result", ""), fields.get("ExecMainStatus", "")
    if load == "not-found":
        return "idle"
    if active in ("activating", "reloading", "deactivating"):
        return "running"
    if active == "active":
        if sub == "running":
            return "running"
        if sub == "exited":
            return "succeeded" if result == "success" and code == "0" else "failed"
        raise InstallerError("status_unavailable", f"unexpected active unit state: {sub!r}")
    if active == "failed":
        return "failed"
    if active == "inactive":
        return "failed" if (result not in ("", "success") or code not in ("", "0")) else "idle"
    raise InstallerError("status_unavailable", "could not read the installer unit state")


def _unavailable(code, detail, power):
    return {"layout": "unavailable", "info": {}, "can_install": False,
            "reason": code, "detail": detail, "power": power}


class InstallerService:
    def __init__(self, runner=subprocess.run, log_path=DEFAULT_LOG, power_dir=DEFAULT_POWER):
        self._runner = runner
        self._log_path = Path(log_path)
        self._power_dir = Path(power_dir)
        self._lock = threading.RLock()
        self._token = None
        self._snapshot = None

    # -- inspection ---------------------------------------------------------

    def inspect(self):
        with self._lock:
            return self._inspect()

    def _inspect(self):
        power = _power(self._power_dir)
        try:
            info = _detect(self._runner)
        except InstallerError as error:
            return _unavailable(error.code, error.detail, power)
        mode = info.get("mode")
        seen = {"layout": mode if isinstance(mode, str) and mode else "unavailable",
                "info": info, "can_install": False, "reason": "", "detail": "", "power": power}
        if mode not in ("fresh", "occupied"):
            seen["reason"] = "toosmall" if mode == "toosmall" else "inspection_failed"
            seen["detail"] = f"unsupported internal storage layout: {mode!r}"
            return seen
        device = info.get("device")
        if not isinstance(device, str) or not device:
            seen["reason"], seen["detail"] = "inspection_failed", "detect did not report a device"
            return seen
        fingerprint = info.get("table_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            seen["reason"], seen["detail"] = "inspection_failed", "detect did not report a table fingerprint"
            return seen
        try:
            _check_boot_disk(device, self._runner)
        except InstallerError as error:
            seen["reason"], seen["detail"] = error.code, error.detail
            return seen
        if power["external_power"] is False:
            seen["reason"], seen["detail"] = "battery_only", "connect external power before installing"
            return seen
        seen["can_install"] = True
        return seen

    # -- preparation --------------------------------------------------------

    def prepare(self, android_gib=None):
        with self._lock:
            if _state_of(self._show()) == "running":
                raise InstallerError("unit_running", "an installation is already running")
            seen = self._inspect()
            if not seen["can_install"]:
                raise InstallerError(seen["reason"] or "inspection_failed", seen["detail"])
            info, mode = seen["info"], seen["info"]["mode"]
            fingerprint = info["table_fingerprint"]
            if mode == "occupied":
                if android_gib is not None:
                    raise InstallerError("invalid_android_size", "an Android size only applies to a fresh install")
                # The existing Android partition is kept as-is, so current equals retained.
                plan_android = info.get("android_gib")
                plan_current = info.get("android_gib")
                linux_gib = info.get("linux_gib")
                snapshot_android = None
            else:
                if type(android_gib) is not int:
                    raise InstallerError("invalid_android_size", "the Android size must be a whole number of GiB")
                minimum, maximum = info.get("android_min_gib"), info.get("android_max_gib")
                if type(minimum) is not int or type(maximum) is not int:
                    raise InstallerError("inspection_failed", "detect did not report the Android size bounds")
                if not minimum <= android_gib <= maximum:
                    raise InstallerError("invalid_android_size",
                                         f"the Android size must be {minimum}..{maximum} GiB")
                current = info.get("android_current_gib")
                plan_android, plan_current = android_gib, current
                linux_gib = current - android_gib if type(current) is int else None
                snapshot_android = android_gib
            token = secrets.token_hex(16)
            snapshot = {"mode": mode, "device": info["device"], "table_fingerprint": fingerprint,
                        "android_gib": snapshot_android, "power": seen["power"],
                        "partition_names": info.get("partition_names", "")}
            self._token, self._snapshot = token, snapshot
            return {"token": token, "device": info["device"], "table_fingerprint": fingerprint,
                    "mode": mode, "android_gib": plan_android, "android_current_gib": plan_current,
                    "linux_gib": linux_gib, "partition_names": snapshot["partition_names"],
                    "power": seen["power"]}

    # -- start --------------------------------------------------------------

    def start(self, token, confirmed=False):
        if confirmed is not True:
            raise InstallerError("confirmation_required", "explicit confirmation is required")
        with self._lock:
            # A consumed or forged token is always unknown_token, before any state work.
            if not token or self._token is None or token != self._token:
                raise InstallerError("unknown_token", "prepare the installation again")
            fields = self._show()
            state = _state_of(fields)
            if state == "running":
                raise InstallerError("unit_running", "an installation is already running")
            snapshot = self._snapshot
            seen = self._inspect()
            if not seen["can_install"]:
                raise InstallerError(seen["reason"] or "inspection_failed", seen["detail"])
            info = seen["info"]
            if (info.get("mode") != snapshot["mode"] or info.get("device") != snapshot["device"]
                    or info.get("table_fingerprint") != snapshot["table_fingerprint"]
                    or info.get("partition_names", "") != snapshot["partition_names"]):
                raise InstallerError("target_changed", "the target device or its layout changed; prepare again")
            if snapshot["mode"] == "fresh":
                minimum, maximum = info.get("android_min_gib"), info.get("android_max_gib")
                if (type(minimum) is not int or type(maximum) is not int
                        or not minimum <= snapshot["android_gib"] <= maximum):
                    raise InstallerError("target_changed", "the Android size bounds changed; prepare again")
            if state in ("succeeded", "failed"):
                self._reset_unit(state)
            # The token is single use: consume it before the launch is attempted.
            self._token, self._snapshot = None, None
            self._truncate_log()
            try:
                result = _capture(self._runner, self._argv(snapshot))
            except (OSError, subprocess.SubprocessError) as error:
                raise InstallerError("launch_failed", str(error)) from error
            if result.returncode:
                detail = (result.stderr or "").strip() or "systemd-run could not start the installer"
                raise InstallerError("launch_failed", detail)
            return {"state": "running", "phase": "working", "log_tail": ""}

    def _argv(self, snapshot):
        argv = ["systemd-run", "--unit", UNIT,
                "--property", "Type=exec",
                "--property", "RemainAfterExit=yes",
                "--property", f"StandardOutput=append:{self._log_path}",
                "--property", f"StandardError=append:{self._log_path}",
                "--", INSTALLER, "--device", snapshot["device"], "install", "--assume-yes",
                "--expect-table", snapshot["table_fingerprint"]]
        if snapshot["mode"] == "fresh":
            argv += ["--userdata-gib", str(snapshot["android_gib"])]
        return argv

    def _reset_unit(self, state):
        # Only ever touches this plugin's own fixed unit, and never ignores a real error.
        # Exactly one command: a failed transient unit must be cleared with reset-failed,
        # because stopping it first can unload the unit and make a following reset-failed
        # fail against a unit that no longer exists.
        argv = (["systemctl", "reset-failed", UNIT] if state == "failed"
                else ["systemctl", "stop", UNIT])
        result = self._systemctl(argv)
        if result.returncode:
            action = "clear the failed" if state == "failed" else "stop the previous"
            detail = (result.stderr or "").strip() or f"systemctl could not {action} installer unit"
            raise InstallerError("launch_failed", detail)

    def _systemctl(self, argv):
        try:
            return _capture(self._runner, argv)
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallerError("launch_failed", str(error)) from error

    def _truncate_log(self):
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("wb"):
                pass
        except OSError as error:
            raise InstallerError("launch_failed", f"could not reset the installer log: {error}") from error

    # -- status -------------------------------------------------------------

    def status(self):
        with self._lock:
            fields = self._show()
            state = _state_of(fields)
            tail = self._log_tail()
            return {"state": state, "phase": self._phase(state, tail), "log_tail": tail}

    def _show(self):
        argv = ["systemctl", "show", UNIT,
                "--property=LoadState,ActiveState,SubState,ExecMainStatus,Result", "--no-pager"]
        try:
            result = _capture(self._runner, argv)
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallerError("status_unavailable", str(error)) from error
        if result.returncode:
            raise InstallerError("status_unavailable", "systemctl could not report the installer unit")
        return _parse_show(result.stdout)

    def _log_tail(self):
        try:
            with self._log_path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - LOG_BYTES))
                data = stream.read()
        except OSError:
            return ""
        text = "\n".join(data.decode("utf-8", errors="replace").splitlines()[-LOG_LINES:])
        encoded = text.encode("utf-8")
        return encoded[-LOG_BYTES:].decode("utf-8", errors="ignore") if len(encoded) > LOG_BYTES else text

    def _phase(self, state, tail):
        for line in reversed(tail.splitlines()):
            for marker, phase in PHASES:
                if marker in line:
                    return phase
        return "working" if state == "running" else ""

    # -- power off ----------------------------------------------------------

    def poweroff(self):
        with self._lock:
            if _state_of(self._show()) != "succeeded":
                raise InstallerError("not_succeeded", "power off is only available after a successful install")
            try:
                result = _capture(self._runner, ["systemctl", "poweroff"])
            except (OSError, subprocess.SubprocessError) as error:
                raise InstallerError("poweroff_failed", str(error)) from error
            if result.returncode:
                detail = (result.stderr or "").strip() or "systemctl could not power off the device"
                raise InstallerError("poweroff_failed", detail)
