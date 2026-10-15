"""Root worker that converts one TF card's reserve partition into a Steam library.

The plugin never touches a card itself.  ``TfLibraryService.start`` writes a
fixed plan record and hands ``armada-tf-library.service`` to ``systemd-run``;
that unit runs *this* file with the running interpreter and fixed argv, so the
conversion survives a Decky panel close or a plugin reload.  The worker:

* re-proves the physical card (SD/boot/critical guards, CID, GPT, filesystem
  UUID) immediately before every write, while holding the upstream
  ``/run/jupiter-automount-<reserve>.lock`` exactly once;
* unmounts only the reserve partition and refuses to format a busy one;
* retypes only ``p1`` to the Linux-filesystem GPT type (``sgdisk --typecode=1:8300``)
  and re-reads the GPT to prove the Android ``p2``/``p3`` are untouched;
* formats only the reserve partition (``mkfs.ext4 -F -E nodiscard -L ARMADA_TF_<cid6>``);
* persists a durable checkpoint before formatting and after formatting so an
  interrupted run can resume without ever reformatting a verified ext4 reserve;
* mounts through the upstream automount helper, verifies the mount is the exact
  reserve ext4 mounted read-write, writes the Linux marker and creates the
  ``SteamLibrary/steamapps`` tree owned by UID 1000;
* asks a running Steam to add the library folder, and reports registration
  separately from the request (a CLI ``0`` is never treated as registration).

The module imports the package absolutely, adding its own parent directory to
``sys.path`` only when it is run as a direct script, so it never depends on a
relative import.
"""

import json
import os
import pwd
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

if __package__ in (None, ""):  # run as a direct script by the fixed systemd unit
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from armada_installer import service, tf_card, tf_inspection, tf_state  # noqa: E402

AUTOMOUNT_HELPER = "/usr/lib/hwsupport/steamos-automount.sh"
STEAM_BIN = "/usr/bin/steam"
STEAM_UID = 1000
MKFS_TIMEOUT = 600
HELPER_TIMEOUT = 120
STEAM_TIMEOUT = 120
MOUNT_POLLS = 40
MOUNT_POLL_DELAY = 0.5
LIBRARY_NAME = "SteamLibrary"
STEAMAPPS_NAME = "steamapps"
MARKER_NAME = tf_inspection.LINUX_MARKER_NAME

_VDF_PATH = re.compile(r'"path"\s*"([^"]*)"')
_UUID_DASH = re.compile(r"-")


class WorkerError(service.InstallerError):
    """A worker failure with a stable machine-readable code."""


def _norm(value):
    return str(value or "").replace("-", "").lower()


def _same_source(left, right):
    if not left or not right:
        return False
    left, right = str(left).split("[", 1)[0], str(right).split("[", 1)[0]
    try:
        return os.path.realpath(left) == os.path.realpath(right)
    except OSError:
        return left == right


class TfWorker:
    """Run one prepared TF-card conversion to completion.

    All external access goes through the injected ``runner`` so the tests can
    pin the exact argv list and never touch a real device, systemd or Steam.
    """

    def __init__(self, plan, status_path=None, log_path=None, runner=subprocess.run,
                 sysfs_dir=Path("/sys"), runtime_dir=service.DEFAULT_TF_RUNTIME,
                 checkpoint_dir=service.DEFAULT_TF_CHECKPOINT, helper=AUTOMOUNT_HELPER,
                 steam_bin=STEAM_BIN, steam_uid=STEAM_UID):
        self._plan = plan if isinstance(plan, dict) else {}
        self._status_path = status_path
        self._log_path = log_path
        self._runner = runner
        self._sysfs = Path(sysfs_dir)
        self._runtime = Path(runtime_dir)
        self._checkpoint = Path(checkpoint_dir)
        self._helper = helper
        self._steam_bin = steam_bin
        self._steam_uid = steam_uid
        self._status = {"state": "running", "phase": "preparing", "detail": ""}

    # -- entry point --------------------------------------------------------

    def run(self):
        try:
            self._execute()
            return 0
        except service.InstallerError as error:
            self._fail(error.code, error.detail)
            return 1
        except Exception as error:  # never leak a traceback through the unit
            self._fail("internal_error", str(error))
            return 1

    def _execute(self):
        plan = self._plan
        for key in ("device", "reserve_device", "card_cid", "mode", "handoff", "table"):
            if not plan.get(key):
                raise WorkerError("invalid_plan", "the plan record is incomplete")
        cid = str(plan["card_cid"]).lower()
        tf_card.validate_handoff(plan["handoff"])  # the descriptor must stay valid
        self._set_phase("preparing", "Validating the card %s" % plan["device"])
        inspector = tf_inspection.TfInspector(runner=self._runner, sysfs_dir=self._sysfs,
                                              runtime_dir=self._runtime,
                                              checkpoint_dir=self._checkpoint)
        lock = service._ReserveLock(self._runtime.parent /
                                    ("jupiter-automount-%s.lock" % os.path.basename(plan["reserve_device"])))
        with lock:
            # Re-prove the card identity under the same lock the automount path
            # uses, before anything at all is unmounted or written.
            got = inspector.recheck(plan["device"], plan)
            if plan["mode"] == "format":
                if not self._already_formatted(cid, plan, got):
                    # The reserve must still carry the prepared filesystem
                    # before the first write, unless a *formatted* checkpoint
                    # proves our own earlier mkfs produced a different UUID.
                    self._verify_snapshot_uuid(plan, got)
                    got = self._format(plan, got, inspector)
                else:
                    got = self._verify_formatted(plan, got)
            else:
                self._verify_snapshot_uuid(plan, got)
                got = self._verify_formatted(plan, got)
            mountpoint = self._mount(got)
            self._write_marker(mountpoint, plan, got)
            library = self._ensure_library(mountpoint)
        # Registration talks to a user session and never touches the card, so it
        # runs after the automount lock is released.
        self._register(library)
        # Keep the registration flags the worker set while marking success.
        self._update(state="succeeded", phase="registering")

    # -- format -------------------------------------------------------------

    def _already_formatted(self, cid, plan, got):
        """True when a durable formatted checkpoint matches the live reserve.

        This is the resume path: an interrupted run that already formatted the
        reserve must never call ``sgdisk``/``mkfs`` again.
        """
        record = tf_state.read_checkpoint(self._checkpoint, cid)
        if not isinstance(record, dict) or record.get("stage") != "formatted":
            return False
        if record.get("size_bytes") != plan.get("size_bytes"):
            return False
        if got.get("filesystem_type") != "ext4":
            return False
        if _norm(record.get("linux_fs_uuid")) != _norm(got.get("filesystem_uuid")):
            return False
        try:
            if record.get("handoff") != plan.get("handoff"):
                return False
        except Exception:  # pragma: no cover - defensive
            return False
        return True

    def _verify_formatted(self, plan, got):
        """Confirm an existing reserve is usable ext4 without reformatting it."""
        if got.get("filesystem_type") != "ext4" or not got.get("filesystem_uuid"):
            raise WorkerError("format_required",
                              "the reserve partition is not a known ext4 filesystem")
        return got

    def _verify_snapshot_uuid(self, plan, got):
        """Prove the live reserve filesystem still matches the prepared snapshot.

        ``recheck`` returns the live reserve filesystem UUID; comparing it with
        the prepared snapshot UUID refuses a reserve that was reformatted or
        swapped since the plan was built, before anything is written.  The one
        legitimate divergence -- a resume whose new UUID was produced by our own
        ``mkfs`` -- is proven by a formatted checkpoint before this check is
        reached, so this is never a false old-versus-new comparison.
        """
        expected = _norm(plan.get("filesystem_uuid"))
        live = _norm(got.get("filesystem_uuid"))
        if expected != live:
            raise WorkerError("target_changed",
                              "the reserve filesystem changed since it was prepared")

    def _format(self, plan, got, inspector):
        cid = str(plan["card_cid"]).lower()
        self._set_phase("formatting", "Preparing to format only the reserve partition")
        # Durable checkpoint BEFORE any format: the validated descriptor plus the
        # exact Android p2/p3 geometry, so a partial failure can recover without
        # ever re-reading (or touching) the Android side.
        tf_state.write_checkpoint(self._checkpoint, cid, self._checkpoint_payload(plan, got, "validated"))
        self._unmount_reserve(got["reserve_device"])
        # Re-prove SD/boot/GPT/CID *and* the live reserve filesystem UUID
        # immediately before the first write.
        got = inspector.recheck(plan["device"], plan)
        self._verify_snapshot_uuid(plan, got)
        self._log("Retyping the reserve partition (p1) to a Linux filesystem")
        self._checked(["sgdisk", "--typecode=1:8300", plan["device"]])
        self._checked(["partprobe", plan["device"]], optional=True)
        # Re-read the actual GPT and prove the Android p2/p3 are byte-identical,
        # and that the reserve still carries the prepared filesystem, before mkfs.
        got = inspector.recheck(plan["device"], plan)
        self._verify_snapshot_uuid(plan, got)
        self._checked(["udevadm", "settle"])
        label = "ARMADA_TF_%s" % cid[-6:]
        self._log("Formatting only %s" % got["reserve_device"])
        self._checked(["mkfs.ext4", "-F", "-E", "nodiscard", "-L", label, got["reserve_device"]],
                      timeout=MKFS_TIMEOUT)
        self._checked(["udevadm", "settle"])
        uuid, fstype = self._blkid(got["reserve_device"])
        if fstype != "ext4" or not uuid:
            raise WorkerError("format_failed", "the reserve partition is not a new ext4 filesystem")
        got = dict(got, filesystem_uuid=uuid, filesystem_type="ext4",
                   reserve_type=tf_card.RESERVE_CONNECTED)
        # Durable checkpoint BEFORE mounting so a crash while mounting still
        # resumes directly to mount without a second mkfs.
        tf_state.write_checkpoint(self._checkpoint, cid, self._checkpoint_payload(plan, got, "formatted"))
        return got

    def _checkpoint_payload(self, plan, got, stage):
        table = plan.get("table") if isinstance(plan.get("table"), dict) else {}
        parts = table.get("partitions") if isinstance(table.get("partitions"), list) else []
        android = parts[1:3] if len(parts) >= 3 else []
        return {"stage": stage, "card_cid": str(plan["card_cid"]).lower(),
                "size_bytes": plan.get("size_bytes"), "handoff": plan.get("handoff"),
                "reserve_device": got.get("reserve_device"),
                "reserve_type": got.get("reserve_type"), "android_parts": android,
                "linux_fs_uuid": got.get("filesystem_uuid")}

    # -- mount --------------------------------------------------------------

    def _mount(self, got):
        reserve = got["reserve_device"]
        self._set_phase("mounting", "Mounting %s" % reserve)
        if self._mounted_target(reserve) is None:
            self._checked([self._helper, "add", os.path.basename(reserve)], timeout=HELPER_TIMEOUT,
                          optional=True)
        target = self._await_mount(reserve)
        if target is None:
            raise WorkerError("mount_failed", "the reserve partition did not mount")
        info = self._mount_info(reserve)
        if info is None or not _same_source(info.get("source"), reserve):
            raise WorkerError("mount_failed", "the mounted source is not the reserve partition")
        if str(info.get("fstype", "")).lower() != "ext4":
            raise WorkerError("mount_failed", "the reserve partition is not mounted as ext4")
        if "rw" not in str(info.get("options", "")).split(","):
            raise WorkerError("mount_failed", "the reserve partition is not mounted read-write")
        return Path(target)

    def _unmount_reserve(self, reserve):
        target = self._mounted_target(reserve)
        if target is None:
            return
        result = self._run(["umount", str(target)], check=False)
        if result is None or result.returncode:
            raise WorkerError("card_busy", "the reserve partition is busy; nothing was written")

    def _await_mount(self, reserve):
        for attempt in range(MOUNT_POLLS):
            target = self._mounted_target(reserve)
            if target is not None:
                return target
            if attempt + 1 < MOUNT_POLLS:
                time.sleep(MOUNT_POLL_DELAY)
        return None

    def _mount_entries(self):
        result = self._run(["findmnt", "--json", "--list", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"],
                           check=False)
        if result is None or result.returncode:
            return []
        try:
            data = json.loads(result.stdout or "{}")
        except ValueError:
            return []
        entries = data.get("filesystems") if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []

    def _mounted_target(self, reserve):
        for entry in self._mount_entries():
            if isinstance(entry, dict) and _same_source(entry.get("source"), reserve):
                return entry.get("target")
        return None

    def _mount_info(self, reserve):
        for entry in self._mount_entries():
            if isinstance(entry, dict) and _same_source(entry.get("source"), reserve):
                return entry
        return None

    # -- marker and library -------------------------------------------------

    def _write_marker(self, mountroot, plan, got):
        root_real = Path(mountroot).resolve()
        marker = Path(mountroot) / MARKER_NAME
        if marker.is_symlink():
            raise WorkerError("library_unsafe", "the Linux marker is a symlink")
        if marker.exists():
            return
        resolved = marker.resolve()
        if root_real != resolved and root_real not in resolved.parents:
            raise WorkerError("library_unsafe", "the Linux marker escapes the mount")
        payload = dict(plan["handoff"])
        payload["linux_fs_uuid"] = got.get("filesystem_uuid")
        try:
            tf_state.atomic_write_json(marker, payload, mode=0o644)
        except OSError as error:
            raise WorkerError("marker_failed", "could not write the Linux marker: %s" % error) from error

    def _ensure_library(self, mountroot):
        root_real = Path(mountroot).resolve()
        library = Path(mountroot) / LIBRARY_NAME
        steamapps = library / STEAMAPPS_NAME
        for candidate in (library, steamapps):
            if candidate.is_symlink():
                raise WorkerError("library_unsafe", "the %s path is a symlink" % candidate.name)
            resolved = candidate.resolve()
            if root_real != resolved and root_real not in resolved.parents:
                raise WorkerError("library_unsafe", "the %s path escapes the mount" % candidate.name)
        try:
            account = pwd.getpwuid(self._steam_uid)
        except KeyError as error:
            raise WorkerError("library_failed", "no UID %d account on this device" % self._steam_uid) from error
        created = []
        for candidate in (library, steamapps):
            if not candidate.exists():
                try:
                    candidate.mkdir()
                except OSError as error:
                    raise WorkerError("library_failed", "could not create %s: %s" % (candidate, error)) from error
                created.append(candidate)
        for path in created:
            try:
                os.chown(path, account.pw_uid, account.pw_gid)
            except OSError as error:
                raise WorkerError("library_failed", "could not own %s: %s" % (path, error)) from error
        return library

    # -- steam registration -------------------------------------------------

    def _register(self, library):
        self._set_phase("registering", "Registering the Steam library")
        library_path = str(library)
        if not self._steam_running():
            self._update(library_requested=False, library_registered=False, library_pending=True,
                         detail="Steam is not running; the library is mounted and pending registration")
            return
        url = "steam://addlibraryfolder/" + urllib.parse.quote(library_path, safe="")
        argv = ["systemd-run", "--uid=%d" % self._steam_uid, "--collect", "--wait",
                "--setenv=XDG_RUNTIME_DIR=/run/user/%d" % self._steam_uid,
                self._steam_bin, "-ifrunning", url]
        result = self._run(argv, check=False, timeout=STEAM_TIMEOUT)
        requested = result is not None and result.returncode == 0
        # A CLI success only means Steam was asked; membership is confirmed by
        # reading the account's own libraryfolders.vdf, never by an exit code.
        registered = bool(requested and self._library_member(library_path))
        self._update(library_requested=requested, library_registered=registered,
                     library_pending=not registered,
                     detail="" if registered else "the library request could not be confirmed")

    def _steam_running(self):
        try:
            result = self._runner(["pgrep", "-x", "steam"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def _library_member(self, library_path):
        account = pwd.getpwuid(self._steam_uid)
        home = Path(account.pw_dir)
        candidates = (home / ".steam" / "steam" / "config" / "libraryfolders.vdf",
                      home / ".steam" / "steam" / "steamapps" / "libraryfolders.vdf",
                      home / ".local" / "share" / "Steam" / "config" / "libraryfolders.vdf",
                      home / ".local" / "share" / "Steam" / "steamapps" / "libraryfolders.vdf")
        wanted = os.path.normpath(library_path)
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for found in _VDF_PATH.findall(text):
                if os.path.normpath(found) == wanted:
                    return True
        return False

    # -- plumbing -----------------------------------------------------------

    def _checked(self, argv, timeout=30, optional=False):
        result = self._run(argv, check=False, timeout=timeout)
        if result is None:  # command missing
            if optional:
                return None
            raise WorkerError("worker_failed", "%s is not available" % argv[0])
        if result.returncode:
            if optional:
                return result
            detail = (result.stderr or "").strip() or ("%s failed" % argv[0])
            raise WorkerError("worker_failed", detail)
        return result

    def _run(self, argv, check=False, timeout=30):
        try:
            return self._runner(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            if check:
                raise WorkerError("worker_failed", str(error)) from error
            return None

    def _blkid(self, device):
        result = self._run(["blkid", "-o", "export", device], check=False)
        if result is None or result.returncode:
            return None, None
        values = {}
        for line in (result.stdout or "").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip()
        return values.get("UUID"), (values.get("TYPE") or "").lower()

    def _log(self, message):
        if not self._log_path:
            return
        text = tf_state.sanitize_log_text(str(message)) + "\n"
        try:
            os.makedirs(os.path.dirname(os.fspath(self._log_path)) or ".", mode=0o700, exist_ok=True)
            with open(os.fspath(self._log_path), "a", encoding="utf-8") as stream:
                stream.write(text)
        except OSError:
            pass

    def _set_phase(self, phase, detail=""):
        self._log("%s: %s" % (phase, detail) if detail else phase)
        self._write_status({"state": "running", "phase": phase, "detail": detail})

    def _update(self, **fields):
        self._status.update(fields)
        self._write_status(self._status)

    def _write_status(self, record):
        self._status = dict(record)
        if not self._status_path:
            return
        try:
            tf_state.atomic_write_json(self._status_path, self._status, mode=0o600)
        except OSError:
            pass

    def _fail(self, code, detail):
        self._log("failed: %s (%s)" % (code, detail))
        record = {"state": "failed", "phase": self._status.get("phase") or "preparing",
                  "detail": "%s: %s" % (code, detail) if detail else code}
        for key in ("library_requested", "library_registered", "library_pending"):
            if key in self._status:
                record[key] = self._status[key]
        self._write_status(record)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        sys.stderr.write("usage: tf_worker.py <plan.json> <status.json> [log]\n")
        return 2
    plan = tf_state.read_json(argv[0])
    status_path, log_path = argv[1], (argv[2] if len(argv) > 2 else None)
    if not isinstance(plan, dict):
        tf_state.atomic_write_json(status_path, {"state": "failed", "phase": "preparing",
                                                 "detail": "the plan record is missing or invalid"})
        return 1
    return TfWorker(plan, status_path=status_path, log_path=log_path).run()


if __name__ == "__main__":
    sys.exit(main())
