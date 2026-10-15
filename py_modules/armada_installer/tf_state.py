"""Durable plan/status/checkpoint files and log sanitisation for the TF worker.

Everything here is pure standard library and never touches a device.  The
helpers exist so the plugin service, the read-only inspector and the root
worker all agree on one on-disk contract:

* the transient plan/status files live under a *fixed* ``/run`` path and are
  written atomically with ``0600`` permissions, so a panel close or a crash can
  never observe a half-written record;
* the durable checkpoint lives under ``/var/lib/armada-installer/tf`` so it
  survives a power loss or a plugin reload and lets a retry resume without
  reformatting the Android side (or an already formatted ext4 reserve);
* log text is stripped of ASCII control characters (except ``\\n``/``\\t``) so
  the Decky UI never renders terminal boxes.
"""

import json
import os
import re
import tempfile

# Fixed, root-owned locations.  Overridable only for tests.
RUNTIME_DIR = "/run/armada-installer-tf"
CHECKPOINT_DIR = "/var/lib/armada-installer/tf"

PLAN_NAME = "plan.json"
STATUS_NAME = "status.json"
LOG_NAME = "worker.log"

STATES = ("idle", "running", "succeeded", "failed")
PHASES = ("preparing", "formatting", "mounting", "registering")
# Strip every C0/C1 control character except newline and tab.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_CID = re.compile(r"[0-9a-f]{32}\Z")


def safe_cid(cid):
    """A filesystem-safe name for a card CID; only a real CID is accepted."""
    text = str(cid or "").lower()
    if not _CID.match(text):
        raise ValueError("a checkpoint name needs a 32-character card CID")
    return text


def sanitize_log_text(text):
    """Drop terminal control characters, keeping only ``\\n`` and ``\\t``."""
    return _CONTROL.sub("", text if isinstance(text, str) else str(text))


def atomic_write_json(path, payload, mode=0o600):
    """Write ``payload`` as JSON atomically with an explicit ``mode``.

    The data is written to a temporary file in the same directory, flushed and
    fsynced, then ``os.replace``d over the destination so a reader never sees a
    partial file.  The parent directory is created ``0700`` if missing.
    """
    path = os.fspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def read_json(path):
    """Return the parsed JSON object at ``path`` or ``None`` when unusable."""
    try:
        with open(os.fspath(path), "rb") as stream:
            raw = stream.read()
    except OSError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def checkpoint_path(checkpoint_dir, cid):
    return os.path.join(os.fspath(checkpoint_dir), safe_cid(cid) + ".json")


def read_checkpoint(checkpoint_dir, cid):
    """The durable checkpoint for one card, or ``None`` when absent/unknown."""
    try:
        path = checkpoint_path(checkpoint_dir, cid)
    except ValueError:
        return None
    return read_json(path)


def write_checkpoint(checkpoint_dir, cid, payload):
    """Atomically persist ``payload`` for one card with ``0600`` permissions."""
    atomic_write_json(checkpoint_path(checkpoint_dir, cid), payload)


def clear_checkpoint(checkpoint_dir, cid):
    try:
        os.unlink(checkpoint_path(checkpoint_dir, cid))
    except (OSError, ValueError):
        pass
