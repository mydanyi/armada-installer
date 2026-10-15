"""Read-only inspection and controlled start of the Armada installer.

The Decky process never imports or executes the installer itself.  Inspection
and preparation only read state; the single mutation is a fixed systemd unit
handed to ``systemd-run`` after the caller confirms a previously prepared plan.
"""

import fcntl
import json
import os
import secrets
import subprocess
import threading
from pathlib import Path

from . import tf_state

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


# ---------------------------------------------------------------------------
# TF library: read-only inspection and prepared plans
# ---------------------------------------------------------------------------

DEFAULT_TF_SYSFS = Path("/sys")
DEFAULT_TF_RUNTIME = Path("/run/armada-installer-tf")
DEFAULT_TF_CHECKPOINT = Path(tf_state.CHECKPOINT_DIR)
TF_UNIT = "armada-tf-library.service"
CRITICAL_MOUNTS = ("/var", "/boot", "/home")


def _check_critical_mounts(device, runner):
    """Refuse a card that backs /var, /boot or /home; fail closed if unknown."""
    for target in CRITICAL_MOUNTS:
        if not Path(target).is_mount():
            continue
        try:
            result = _capture(runner, ["findmnt", "--noheadings", "--output", "SOURCE", "--target", target])
            if result.returncode:
                raise InstallerError("boot_disk_unknown", "could not read a critical mount source")
            source = (result.stdout or "").strip().split("[", 1)[0]
            if not source or not Path(source).is_block_device():
                raise InstallerError("boot_disk_unknown", "a critical mount source is not a block device")
            result = _capture(runner, ["lsblk", "--inverse", "--noheadings", "--raw",
                                       "--output", "MAJ:MIN", source])
            if result.returncode:
                raise InstallerError("boot_disk_unknown", "could not trace a critical mount ancestry")
            ancestors = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
            ident = os.stat(device).st_rdev
            if not ancestors or "%d:%d" % (os.major(ident), os.minor(ident)) in ancestors:
                raise InstallerError("boot_disk_forbidden", str(device))
        except InstallerError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallerError("boot_disk_unknown", str(error)) from error


class _ReserveLock:
    """Serialise against the upstream Armada automount/format lock.

    The lock is only ever taken non-blocking: a card already held by the
    automount/format path fails fast with ``card_busy`` instead of hanging the
    caller, and the descriptor is closed on failure so a later re-entrant
    attempt by the worker cannot deadlock on a leaked descriptor.
    """

    def __init__(self, path):
        self._path = Path(path)
        self._handle = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise InstallerError("card_busy", "the card is busy; retry once it is idle") from error
        self._handle = handle
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


class TfLibraryService:
    """Public facade for the Armada TF-card library conversion.

    ``inspect``/``prepare`` are thin, read-only wrappers over the one
    :class:`tf_inspection.TfInspector`.  ``start`` writes a fixed plan/status
    record atomically and hands the *fixed* ``armada-tf-library.service`` unit
    to ``systemd-run``; the unit runs this package's own ``tf_worker`` under the
    Python interpreter with no client-supplied command, so a panel close or a
    plugin reload cannot cancel the running conversion.  ``status`` merges the
    transient worker record with the real systemd unit state.
    """

    def __init__(self, runner=subprocess.run, sysfs_dir=DEFAULT_TF_SYSFS,
                 runtime_dir=DEFAULT_TF_RUNTIME, checkpoint_dir=DEFAULT_TF_CHECKPOINT):
        # Imported locally: ``tf_inspection`` imports ``InstallerError`` and the
        # boot/critical guards from this module, so a module-level import here
        # would be circular.
        from .tf_inspection import TfInspector
        self._runner = runner
        self._runtime = Path(runtime_dir)
        self._checkpoint_dir = Path(checkpoint_dir)
        self._inspector = TfInspector(runner=runner, sysfs_dir=sysfs_dir, runtime_dir=runtime_dir,
                                      checkpoint_dir=self._checkpoint_dir)
        self._lock = threading.RLock()
        self._token = None
        self._plan = None

    # -- paths --------------------------------------------------------------

    @property
    def _plan_path(self):
        return self._runtime / tf_state.PLAN_NAME

    @property
    def _status_path(self):
        return self._runtime / tf_state.STATUS_NAME

    @property
    def _log_path(self):
        return self._runtime / tf_state.LOG_NAME

    # -- public API ---------------------------------------------------------

    def inspect(self):
        with self._lock:
            return self._inspector.inspect_cards()

    def prepare(self, device):
        with self._lock:
            snapshot = self._inspector.snapshot(device)
            token = secrets.token_hex(16)
            self._token, self._plan = token, snapshot
            return {"token": token, "mode": snapshot["mode"], "device": snapshot["device"],
                    "reserve_device": snapshot["reserve_device"], "card_cid": snapshot["card_cid"],
                    "android_bytes": snapshot["android_bytes"], "linux_bytes": snapshot["linux_bytes"],
                    "recovery": bool(snapshot.get("recovery")),
                    "preserves": _preserves(snapshot["table"]),
                    "erase_scope": "reserve_only_format" if snapshot["mode"] == "format" else "none"}

    def start(self, token, confirmed=False):
        """Launch the fixed TF library worker once for one prepared plan.

        The plan is single use and confirmation is mandatory.  The card is
        re-inspected; the CID, GPT geometry, handoff and reserve filesystem
        UUID must still match the prepared snapshot, otherwise the launch is
        refused with ``target_changed``.  A running conversion is refused with
        ``unit_running``.  Before the launch the plan and an initial status
        record are written atomically (``0600``) to fixed runtime paths.
        """
        if confirmed is not True:
            raise InstallerError("confirmation_required", "explicit confirmation is required")
        with self._lock:
            if not token or self._token is None or token != self._token or not isinstance(self._plan, dict):
                raise InstallerError("unknown_token", "prepare the library conversion again")
            plan = self._plan
            # Read the existing unit state once; a running conversion is refused
            # untouched, and the same reading decides the terminal cleanup below.
            state = self._tf_state()
            if state == "running":
                raise InstallerError("unit_running", "a TF library conversion is already running")
            # Re-inspect the physical card before anything is written.  This
            # briefly takes the automount lock via the inspector's read-only
            # mount, and the worker's longer lock is only taken once it runs.
            seen = self._inspector.snapshot(plan["device"])
            self._ensure_unchanged(plan, seen)
            # A stale terminal unit must be cleared before the new run is
            # prepared: a failed transient unit is cleared with reset-failed and
            # a succeeded one with stop.  This runs before the token is consumed
            # and before the plan/status/log are written, so a cleanup failure
            # leaves the previous record and log intact and never launches.
            self._clear_unit(state)
            # The token is single use: consume it before the launch is attempted.
            self._token, self._plan = None, None
            self._write_plan(plan, seen)
            self._write_status({"state": "running", "phase": "preparing", "detail": ""})
            self._truncate_log()
            try:
                result = _capture(self._runner, self._tf_argv())
            except (OSError, subprocess.SubprocessError) as error:
                self._write_status({"state": "failed", "phase": "preparing", "detail": "launch_failed"})
                raise InstallerError("launch_failed", str(error)) from error
            if result.returncode:
                detail = (result.stderr or "").strip() or "systemd-run could not start the TF library worker"
                self._write_status({"state": "failed", "phase": "preparing", "detail": detail})
                raise InstallerError("launch_failed", detail)
            return {"state": "running", "phase": "preparing", "detail": "", "log_tail": ""}

    def status(self):
        """Merge the transient worker record with the real systemd unit state.

        A failed unit always reports ``failed`` even when a stale status record
        still claims success; a running or successful unit likewise overrides
        an out-of-date record.  ``phase`` is one of ``preparing``,
        ``formatting``, ``mounting`` or ``registering`` (or empty when idle).
        """
        with self._lock:
            unit = _state_of(self._tf_show())
            record = tf_state.read_json(self._status_path) or {}
            state, phase = record.get("state"), record.get("phase") or ""
            detail = str(record.get("detail") or "")
            if unit in ("failed", "running", "succeeded"):
                state = unit
                if unit == "running" and phase not in tf_state.PHASES:
                    phase = "preparing"
            if state not in tf_state.STATES:
                state = "idle"
                phase = ""
            return {"state": state, "phase": phase, "detail": detail, "log_tail": self._log_tail(),
                    "library_requested": bool(record.get("library_requested")),
                    "library_registered": bool(record.get("library_registered")),
                    "library_pending": bool(record.get("library_pending"))}

    # -- helpers ------------------------------------------------------------

    def _ensure_unchanged(self, plan, seen):
        from . import tf_inspection
        if tf_inspection.fingerprint(seen) != tf_inspection.fingerprint(plan):
            raise InstallerError("target_changed", "the card changed; prepare the conversion again")

    def _clear_unit(self, state):
        """Clear this plugin's own terminal TF unit before a fresh launch.

        A failed transient unit needs ``reset-failed``; a succeeded one needs
        ``stop``.  Any failure is surfaced as ``launch_failed`` so the caller
        aborts before the plan/status/log are written.
        """
        if state not in ("failed", "succeeded"):
            return
        argv = (["systemctl", "reset-failed", TF_UNIT] if state == "failed"
                else ["systemctl", "stop", TF_UNIT])
        try:
            result = _capture(self._runner, argv)
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallerError("launch_failed", str(error)) from error
        if result.returncode:
            action = "clear the failed" if state == "failed" else "stop the previous"
            detail = (result.stderr or "").strip() or "systemctl could not %s TF worker unit" % action
            raise InstallerError("launch_failed", detail)

    def _write_plan(self, plan, seen):
        from . import tf_inspection
        table = seen.get("table") if isinstance(seen.get("table"), dict) else {}
        parts = table.get("partitions") if isinstance(table.get("partitions"), list) else []
        record = {"device": plan["device"], "reserve_device": plan["reserve_device"],
                  "card_cid": plan["card_cid"], "size_bytes": plan.get("size_bytes"),
                  "mode": plan["mode"], "recovery": bool(plan.get("recovery")),
                  "reserve_type": str((parts[0] if parts else {}).get("type", "")).lower(),
                  "handoff": plan.get("handoff"), "table": table,
                  "filesystem_uuid": plan.get("filesystem_uuid"),
                  "android_bytes": plan.get("android_bytes"), "linux_bytes": plan.get("linux_bytes"),
                  # A readable, comparable fingerprint so a retry can prove the
                  # plan still describes the same card.
                  "fingerprint": list(tf_inspection.fingerprint(seen))}
        try:
            tf_state.atomic_write_json(self._plan_path, record, mode=0o600)
        except OSError as error:
            raise InstallerError("launch_failed", "could not write the TF plan: %s" % error) from error

    def _write_status(self, record):
        try:
            tf_state.atomic_write_json(self._status_path, record, mode=0o600)
        except OSError:
            # A status write must never take down a launch that already started.
            pass

    def _truncate_log(self):
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("wb"):
                pass
        except OSError as error:
            raise InstallerError("launch_failed", "could not reset the TF worker log: %s" % error) from error

    def _tf_argv(self):
        worker = Path(__file__).resolve().with_name("tf_worker.py")
        return ["systemd-run", "--unit", TF_UNIT,
                "--property", "Type=exec",
                "--property", "RemainAfterExit=yes",
                "--property", "StandardOutput=append:%s" % self._log_path,
                "--property", "StandardError=append:%s" % self._log_path,
                "--", "/usr/bin/python3", str(worker), str(self._plan_path), str(self._status_path),
                str(self._log_path)]

    def _tf_show(self):
        return _parse_show(self._show_unit(TF_UNIT))

    def _show_unit(self, unit):
        argv = ["systemctl", "show", unit,
                "--property=LoadState,ActiveState,SubState,ExecMainStatus,Result", "--no-pager"]
        try:
            result = _capture(self._runner, argv)
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallerError("status_unavailable", str(error)) from error
        if result.returncode:
            raise InstallerError("status_unavailable", "systemctl could not report the TF worker unit")
        return result.stdout

    def _tf_state(self):
        return _state_of(self._tf_show())

    def _log_tail(self):
        return _tail(self._log_path)


def _tail(log_path):
    try:
        with Path(log_path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - LOG_BYTES))
            data = stream.read()
    except OSError:
        return ""
    text = tf_state.sanitize_log_text(data.decode("utf-8", errors="replace"))
    lines = text.splitlines()[-LOG_LINES:]
    text = "\n".join(lines)
    encoded = text.encode("utf-8")
    return encoded[-LOG_BYTES:].decode("utf-8", errors="ignore") if len(encoded) > LOG_BYTES else text


def _preserves(table):
    parts = table.get("partitions", []) if isinstance(table, dict) else []

    def entry(index):
        if index < len(parts):
            part = parts[index]
            return {"name": part.get("name"), "type": str(part.get("type", "")).lower(),
                    "uuid": str(part.get("uuid") or "").replace("-", "").lower()}
        return {}

    return {"android_p2": entry(1), "android_p3": entry(2)}
