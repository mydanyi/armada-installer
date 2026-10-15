"""Read-only TF-card inspection for the Armada installer.

``TfInspector`` discovers physical SD cards, proves their identity/layout, and
locates the Armada handoff marker so a later worker can plan against a stable
snapshot.  It only ever reads state: it never writes to a card, never reformats
anything, and never trusts a descriptor-supplied device path.

Every violation surfaces as ``InspectorError`` (an ``InstallerError`` with a
stable ``code``); there is no partial success.  The SD/boot/internal guards are
built into the read paths and cannot be disabled through the public API.
"""

import json
import os
import re
import subprocess
import threading
from pathlib import Path

from . import service, tf_card, tf_state

ANDROID_HANDOFF_RELATIVE = "Android/data/io.github.mydanyi.armadatf/files/armada-tf-handoff.json"
LINUX_MARKER_NAME = ".armada-tf-handoff.json"
MARKER_LIMIT = 16 * 1024
FAT_FS_TYPES = ("vfat", "exfat", "msdos", "fat")
EXT_FS_TYPES = ("ext4",)
HANDOFF_INSTRUCTION = ("Open the Smart TF Card Allocator (智能TF卡分配器) Android app and finish "
                       "the TF-card handoff, then rescan.")
MARKER_INSTRUCTION = ("This card already carries a Linux-side handoff, but its handoff record is "
                      "missing. Leave the partitions as they are, insert the card into the device "
                      "that created it and check the installer logs, then rescan.")

_LSBLK_COLUMNS = "NAME,PATH,TYPE,SIZE,PARTUUID,PKNAME"
_WHOLE_MMCBLK = re.compile(r"/dev/mmcblk\d+\Z")
_CID32 = re.compile(r"[0-9a-f]{32}\Z")


class InspectorError(service.InstallerError):
    """A read-only inspection failure with a stable machine-readable code."""


def _norm_uuid(value):
    return str(value or "").replace("-", "").lower()


def _is_cid(value):
    return isinstance(value, str) and bool(_CID32.match(value))


def _same_device(left, right):
    """True only when both paths resolve to the same block device."""
    if not left or not right:
        return False
    left, right = str(left).split("[", 1)[0], str(right).split("[", 1)[0]
    try:
        return os.path.realpath(left) == os.path.realpath(right)
    except OSError:
        return left == right


def _run_json(runner, argv):
    try:
        result = service._capture(runner, argv)
    except (OSError, subprocess.SubprocessError) as error:
        raise InspectorError("inspection_failed", str(error)) from error
    if result.returncode:
        detail = (result.stderr or "").strip() or ("%s could not inspect the card" % argv[0])
        raise InspectorError("inspection_failed", detail)
    try:
        return json.loads(result.stdout or "{}")
    except (TypeError, ValueError) as error:
        raise InspectorError("inspection_failed", "invalid %s output: %s" % (argv[0], error)) from error


def _record(device):
    """Normalise one lsblk entry; ``None`` when it carries no usable path."""
    path = device.get("path") or device.get("name")
    if not isinstance(path, str) or not path:
        return None
    return {"path": path, "name": os.path.basename(path), "type": device.get("type"),
            "size": device.get("size"),
            "partuuid": (device.get("partuuid") or "").lower() or None,
            "pkname": device.get("pkname")}


def _parent_matches(child, device):
    parent = child.get("pkname")
    return isinstance(parent, str) and bool(parent) and os.path.basename(parent) == os.path.basename(device)


def _sd_identity(sysfs, node):
    """``(bus type, CID)`` for the whole disk from sysfs; ``(None, None)`` if absent."""
    base = Path(sysfs) / "class" / "block" / node / "device"
    try:
        kind = (base / "type").read_text(encoding="utf-8").strip()
        cid = (base / "cid").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None, None
    return kind, cid


def _read_bounded(fd):
    chunks, total = [], 0
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MARKER_LIMIT:
            raise InspectorError("handoff_invalid", "the handoff descriptor is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _read_marker(mountroot, relative):
    """Read a bounded, symlink-free JSON marker inside ``mountroot``.

    The resolved target must stay inside the real mount root and the final file
    is opened ``O_NOFOLLOW``; a missing file returns ``None``.  Anything else is
    a stable ``handoff_invalid`` failure.
    """
    root = Path(mountroot)
    try:
        root_real = root.resolve()
    except OSError as error:
        raise InspectorError("handoff_invalid", str(error)) from error
    current = root
    for part in Path(relative).parts:
        current = current / part
        try:
            if current.is_symlink():
                raise InspectorError("handoff_invalid", "the handoff path must not contain symlinks")
        except OSError as error:
            raise InspectorError("handoff_invalid", str(error)) from error
    target = root / relative
    try:
        resolved = target.resolve()
    except OSError as error:
        raise InspectorError("handoff_invalid", str(error)) from error
    if resolved != root_real and root_real not in resolved.parents:
        raise InspectorError("handoff_invalid", "the handoff path escapes the mount")
    try:
        fd = os.open(str(target), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise InspectorError("handoff_invalid", str(error)) from error
    try:
        data = _read_bounded(fd)
    finally:
        os.close(fd)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise InspectorError("handoff_invalid", "the handoff descriptor is not valid JSON") from error


def fingerprint(snapshot):
    """A stable tuple over the card's durable identity, never its mount state.

    Covers the CID, capacity, GPT entry UUIDs/geometry/types, filesystem UUID
    and the normalised handoff.  The volatile mount point and library path are
    deliberately excluded so a rescanned card keeps the same fingerprint.
    """
    if not isinstance(snapshot, dict):
        return None
    table = snapshot.get("table") if isinstance(snapshot.get("table"), dict) else {}
    parts = table.get("partitions") if isinstance(table.get("partitions"), list) else []
    entries = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        entries.append((part.get("start"), part.get("size"),
                        str(part.get("type", "")).lower(),
                        _norm_uuid(part.get("uuid"))))
    geometry = (str(table.get("label", "")).lower(), table.get("sectorsize"),
                table.get("firstlba"), table.get("lastlba"), str(table.get("id", "")).lower())
    try:
        handoff = json.dumps(snapshot.get("handoff"), sort_keys=True, default=str)
    except (TypeError, ValueError):
        handoff = None
    return (snapshot.get("card_cid"), snapshot.get("size_bytes"), geometry, tuple(entries),
            snapshot.get("filesystem_uuid"), snapshot.get("filesystem_type"), handoff)


class TfInspector:
    """List physical SD cards and snapshot one for a later worker.

    ``inspect`` returns the pending/connected/blocked cards; ``snapshot`` returns
    the full read-only snapshot for one selected card, or raises
    ``InspectorError``.  Neither writes to a card.
    """

    def __init__(self, runner=subprocess.run, sysfs_dir=Path("/sys"),
                 runtime_dir=Path("/run/armada-installer-tf"),
                 checkpoint_dir=Path(tf_state.CHECKPOINT_DIR)):
        self._runner = runner
        self._sysfs = Path(sysfs_dir)
        self._runtime = Path(runtime_dir)
        self._checkpoint_dir = Path(checkpoint_dir)
        self._lock = threading.RLock()

    fingerprint = staticmethod(fingerprint)

    # -- public API ---------------------------------------------------------

    def inspect_cards(self):
        with self._lock:
            return {"cards": [card for card, _ in self._inspect_cards()]}

    def snapshot(self, device):
        with self._lock:
            for card, snapshot in self._inspect_cards():
                if card["device"] != device:
                    continue
                if card["state"] == "blocked" or snapshot is None:
                    raise InspectorError(card["reason"] or "inspection_failed", card["detail"])
                return snapshot
            raise InspectorError("unknown_device", "the selected card was not found")

    def recheck(self, device, expect):
        """Re-prove the physical card identity and GPT *without* the marker lock.

        The worker already holds the upstream automount lock while it mutates
        the reserve partition, so this re-read never mounts the marker and never
        takes ``_ReserveLock`` again (which would deadlock against the lock the
        worker itself holds).  It re-applies the SD/boot/critical guards, reads
        the live GPT and confirms the Android partitions still match the
        snapshot the plan was built from; the filesystem UUID is returned so the
        caller can verify it immediately before writing.
        """
        with self._lock:
            if not Path(device).is_block_device():
                raise InspectorError("unknown_device", "the selected card was not found")
            service._check_boot_disk(device, self._runner)
            service._check_critical_mounts(device, self._runner)
            kind, cid = _sd_identity(self._sysfs, os.path.basename(device))
            if kind != "SD" or not _is_cid(cid):
                raise InspectorError("identity_mismatch", "the card identity could not be re-proved")
            if expect.get("card_cid") and _norm_uuid(expect["card_cid"]) != cid:
                raise InspectorError("target_changed", "the card CID changed since it was prepared")
            # Re-read the whole disk's real capacity independently of the GPT;
            # a card that was swapped or resized must fail even when the CID and
            # every GPT entry still look the same.
            expected_size = expect.get("size_bytes")
            if type(expected_size) is int:
                actual_size = self._whole_disk_size(device)
                if actual_size is None:
                    raise InspectorError("inspection_failed",
                                         "the card capacity could not be re-read")
                if actual_size != expected_size:
                    raise InspectorError("target_changed",
                                         "the card capacity changed since it was prepared")
            table = self._table(device)
            parts = table.get("partitions")
            sector = table.get("sectorsize")
            if (str(table.get("label", "")).lower() != "gpt" or not isinstance(parts, list)
                    or len(parts) != 3 or type(sector) is not int):
                raise InspectorError("layout_unsupported",
                                     "the card is no longer a native three-partition GPT volume")
            reserve, meta, private = parts
            reserve_type = str(reserve.get("type", "")).lower()
            if reserve_type not in tf_card.RESERVE_TYPES:
                raise InspectorError("layout_unsupported", "the reserve partition type is unsupported")
            reserve_device = self._verify_partitions(device, reserve, meta, private)
            expected = expect.get("table") or {}
            old_parts = expected.get("partitions") if isinstance(expected, dict) else None
            if isinstance(old_parts, list) and len(old_parts) == len(parts):
                for index, (old, new) in enumerate(zip(old_parts, parts)):
                    if (old.get("start") != new.get("start") or old.get("size") != new.get("size")
                            or _norm_uuid(old.get("uuid")) != _norm_uuid(new.get("uuid"))):
                        raise InspectorError("target_changed",
                                             "the partition geometry changed since it was prepared")
                    # The reserve type legitimately flips pending -> Linux during
                    # the conversion; the Android partitions must keep their type.
                    if (index > 0 and str(old.get("type", "")).lower() != str(new.get("type", "")).lower()):
                        raise InspectorError("target_changed",
                                             "an Android partition type changed since it was prepared")
            handoff = expect.get("handoff")
            if handoff is not None:
                self._check_identity(table, handoff, cid, expect.get("size_bytes"))
            uuid, fstype = self._blkid(reserve_device)
            return {"device": device, "card_cid": cid, "reserve_device": reserve_device,
                    "table": table, "reserve_type": reserve_type,
                    "android_bytes": private["size"] * sector, "linux_bytes": reserve["size"] * sector,
                    "filesystem_uuid": uuid, "filesystem_type": fstype}

    # -- discovery ----------------------------------------------------------

    def _inspect_cards(self):
        found = []
        for device, node, size in self._discover():
            result = self._inspect_disk(device, node, size)
            if result is not None:
                found.append(result)
        return found

    def _discover(self):
        """Every whole ``/dev/mmcblk<number>`` lsblk reports, as ``(path, node, size)``."""
        data = _run_json(self._runner, ["lsblk", "--json", "--bytes", "--paths",
                                        "--output", _LSBLK_COLUMNS])
        disks = []
        for entry in data.get("blockdevices", []) if isinstance(data, dict) else []:
            seen = _record(entry) if isinstance(entry, dict) else None
            if seen is None or seen["type"] != "disk" or not _WHOLE_MMCBLK.match(seen["path"]):
                continue
            if isinstance(seen["size"], int):
                disks.append((seen["path"], seen["name"], seen["size"]))
        return disks

    def _children(self, device):
        """The selected disk's partitions keyed by their device path."""
        data = _run_json(self._runner, ["lsblk", "--json", "--bytes", "--paths",
                                        "--output", _LSBLK_COLUMNS, device])
        found = {}
        for top in data.get("blockdevices", []) if isinstance(data, dict) else []:
            for child in (top.get("children") or []) if isinstance(top, dict) else []:
                seen = _record(child) if isinstance(child, dict) else None
                if seen is not None:
                    found[seen["path"]] = seen
        return found

    def _whole_disk_size(self, device):
        """The whole disk's real byte capacity from its own lsblk row.

        Reuses the same discovery as ``inspect``; an unknown card returns
        ``None`` so the caller refuses instead of guessing.
        """
        for path, _node, size in self._discover():
            if _same_device(path, device):
                return size
        return None

    def _inspect_disk(self, device, node, size):
        kind, cid = _sd_identity(self._sysfs, node)
        if kind != "SD" or not _is_cid(cid) or not Path(device).is_block_device():
            return None
        card = {"device": device, "size_bytes": size, "card_cid": cid, "state": "blocked",
                "reason": "", "detail": "", "reserve_device": None, "android_bytes": None,
                "linux_bytes": None, "mountpoint": None, "library_path": None}
        try:
            service._check_boot_disk(device, self._runner)
            service._check_critical_mounts(device, self._runner)
        except service.InstallerError as error:
            card["reason"], card["detail"] = error.code, error.detail
            return card, None
        try:
            return self._describe_card(card, device, size, cid)
        except service.InstallerError as error:
            card["reason"], card["detail"] = error.code, error.detail
            return card, None

    # -- description --------------------------------------------------------

    def _describe_card(self, card, device, size, cid):
        table = self._table(device)
        parts = table.get("partitions")
        sector = table.get("sectorsize")
        if (str(table.get("label", "")).lower() != "gpt" or not isinstance(parts, list)
                or len(parts) != 3 or type(sector) is not int):
            raise InspectorError("layout_unsupported",
                                 "the card is not a native three-partition GPT volume")
        reserve, meta, private = parts
        for part, label in ((reserve, "reserve"), (meta, "metadata"), (private, "private")):
            if not isinstance(part, dict) or type(part.get("size")) is not int or part["size"] <= 0:
                raise InspectorError("layout_unsupported", "the %s partition has no size" % label)
        reserve_type = str(reserve.get("type", "")).lower()
        if reserve_type not in tf_card.RESERVE_TYPES:
            raise InspectorError("layout_unsupported", "the reserve partition type is unsupported")
        reserve_device = self._verify_partitions(device, reserve, meta, private)
        card["reserve_device"] = reserve_device
        card["linux_bytes"] = reserve["size"] * sector
        card["android_bytes"] = private["size"] * sector
        if reserve_type == tf_card.RESERVE_CONNECTED:
            snapshot = self._inspect_connected(card, table, reserve_device, cid, size)
            card["state"] = "recovery" if snapshot.get("recovery") else "connected"
        else:
            snapshot = self._inspect_pending(card, table, reserve_device, cid, size)
            card["state"] = "pending"
        card["reason"], card["detail"] = "", ""
        return card, snapshot

    def _verify_partitions(self, device, reserve, meta, private):
        node = reserve.get("node")
        if not isinstance(node, str) or node != device + "p1":
            raise InspectorError("layout_unsupported", "the first partition is not the reserve partition")
        children = self._children(device)
        if not children:
            raise InspectorError("inspection_failed", "could not read the card's partitions")
        for candidate, label in ((reserve, "reserve"), (meta, "metadata"), (private, "private")):
            path = candidate.get("node")
            child = children.get(path) if isinstance(path, str) else None
            if child is None or not _parent_matches(child, device):
                raise InspectorError("layout_unsupported",
                                     "the %s partition does not belong to the selected card" % label)
            if _norm_uuid(child.get("partuuid")) != _norm_uuid(candidate.get("uuid")):
                raise InspectorError("layout_unsupported",
                                     "the %s partition does not match its partuuid" % label)
        return device + "p1"

    def _table(self, device):
        data = _run_json(self._runner, ["sfdisk", "--json", device])
        table = data.get("partitiontable") if isinstance(data, dict) else None
        if not isinstance(table, dict):
            raise InspectorError("inspection_failed", "sfdisk did not report a partition table")
        return table

    # -- modes --------------------------------------------------------------

    def _inspect_pending(self, card, table, reserve_device, cid, size):
        uuid, fstype = self._blkid(reserve_device)
        if not uuid or fstype not in FAT_FS_TYPES:
            raise InspectorError("reserve_unprepared", "the reserve partition is not a FAT or exFAT volume")
        with self._reserve_lock(reserve_device):
            mountpoint, owned = self._reserve_mount(reserve_device, cid)
            try:
                raw = _read_marker(mountpoint, ANDROID_HANDOFF_RELATIVE)
            finally:
                if owned:
                    self._reserve_unmount(mountpoint)
        if raw is None:
            raise InspectorError("handoff_missing", HANDOFF_INSTRUCTION)
        handoff = self._validate_handoff(raw)
        self._check_identity(table, handoff, cid, size)
        if _norm_uuid(handoff["public_fs_uuid"]) != _norm_uuid(uuid):
            raise InspectorError("filesystem_mismatch", "the reserve filesystem UUID does not match the handoff")
        card["mountpoint"] = None if owned else str(mountpoint)
        card["library_path"] = None
        return {"mode": "format", "device": card["device"], "size_bytes": size, "card_cid": cid,
                "table": table, "reserve_device": reserve_device, "handoff": handoff,
                "filesystem_uuid": uuid, "filesystem_type": fstype,
                "android_bytes": card["android_bytes"], "linux_bytes": card["linux_bytes"],
                "mountpoint": card["mountpoint"], "library_path": card["library_path"]}

    def _inspect_connected(self, card, table, reserve_device, cid, size):
        uuid, fstype = self._blkid(reserve_device)
        if not uuid or fstype not in EXT_FS_TYPES:
            return self._recover_unformatted(card, table, reserve_device, cid, size)
        with self._reserve_lock(reserve_device):
            mountpoint, owned = self._reserve_mount(reserve_device, cid)
            try:
                raw = _read_marker(mountpoint, LINUX_MARKER_NAME)
            finally:
                if owned:
                    self._reserve_unmount(mountpoint)
        reported = None if owned else str(mountpoint)
        if raw is None:
            return self._recover_connected(card, table, reserve_device, cid, size, uuid, reported)
        handoff = self._validate_handoff(raw)
        self._check_identity(table, handoff, cid, size)
        if _norm_uuid(raw.get("linux_fs_uuid")) != _norm_uuid(uuid):
            raise InspectorError("filesystem_mismatch",
                                 "the reserve filesystem UUID does not match the recorded Linux UUID")
        card["mountpoint"] = reported
        card["library_path"] = str(Path(mountpoint) / "SteamLibrary") if reported else None
        return {"mode": "mount", "device": card["device"], "size_bytes": size, "card_cid": cid,
                "table": table, "reserve_device": reserve_device, "handoff": handoff,
                "filesystem_uuid": uuid, "filesystem_type": fstype,
                "android_bytes": card["android_bytes"], "linux_bytes": card["linux_bytes"],
                "mountpoint": card["mountpoint"], "library_path": card["library_path"]}

    def _recover_unformatted(self, card, table, reserve_device, cid, size):
        """Resume a reserve that was retyped to Linux but never formatted.

        An interrupt between the ``p1`` typecode change and ``mkfs.ext4`` leaves
        a Linux-type reserve with no ext4 filesystem.  This is only accepted as
        a resume when the durable *validated* checkpoint matches the live CID,
        capacity, handoff and Android geometry; otherwise the original
        ``reserve_unprepared`` refusal stands.  The returned snapshot is a
        format plan (with ``recovery`` set) so the caller must confirm the
        erase again, and the worker re-runs the bounded format without touching
        the Android side.
        """
        record = tf_state.read_checkpoint(self._checkpoint_dir, cid)
        if not isinstance(record, dict) or record.get("stage") != "validated":
            raise InspectorError("reserve_unprepared", "the reserve partition is not an ext4 filesystem")
        if record.get("size_bytes") != size:
            raise InspectorError("reserve_unprepared", "the reserve partition is not an ext4 filesystem")
        try:
            handoff = self._validate_handoff(record.get("handoff"))
            self._check_identity(table, handoff, cid, size)
            self._check_android_geometry(table, record.get("android_parts"))
        except InspectorError:
            raise InspectorError("reserve_unprepared", "the reserve partition is not an ext4 filesystem")
        card["state"], card["mountpoint"], card["library_path"] = "recovery", None, None
        return {"mode": "format", "recovery": True, "device": card["device"], "size_bytes": size,
                "card_cid": cid, "table": table, "reserve_device": reserve_device, "handoff": handoff,
                "filesystem_uuid": None, "filesystem_type": None,
                "android_bytes": card["android_bytes"], "linux_bytes": card["linux_bytes"],
                "mountpoint": None, "library_path": None}

    def _recover_connected(self, card, table, reserve_device, cid, size, uuid, reported):
        """Resume an ext4 reserve whose Linux marker never landed.

        A missing marker is normally ``marker_missing``.  It is only accepted
        as a resume when a durable formatted checkpoint exists for the *same*
        CID/capacity/private GUID/Android geometry and its recorded new ext4
        UUID matches the live ``blkid`` UUID.  A bare ext4 filesystem with no
        marker and no matching checkpoint stays blocked, and nothing here ever
        reformats anything.
        """
        record = tf_state.read_checkpoint(self._checkpoint_dir, cid)
        if not isinstance(record, dict) or record.get("stage") != "formatted":
            raise InspectorError("marker_missing", MARKER_INSTRUCTION)
        if record.get("size_bytes") != size:
            raise InspectorError("marker_missing", MARKER_INSTRUCTION)
        if _norm_uuid(record.get("linux_fs_uuid")) != _norm_uuid(uuid):
            raise InspectorError("marker_missing", MARKER_INSTRUCTION)
        stored_handoff = record.get("handoff")
        try:
            handoff = self._validate_handoff(stored_handoff)
        except InspectorError:
            raise InspectorError("marker_missing", MARKER_INSTRUCTION)
        try:
            self._check_identity(table, handoff, cid, size)
            self._check_android_geometry(table, record.get("android_parts"))
        except InspectorError:
            raise InspectorError("marker_missing", MARKER_INSTRUCTION)
        card["state"], card["mountpoint"] = "recovery", reported
        card["library_path"] = str(Path(reported) / "SteamLibrary") if reported else None
        return {"mode": "mount", "recovery": True, "device": card["device"], "size_bytes": size,
                "card_cid": cid, "table": table, "reserve_device": reserve_device, "handoff": handoff,
                "filesystem_uuid": uuid, "filesystem_type": "ext4",
                "android_bytes": card["android_bytes"], "linux_bytes": card["linux_bytes"],
                "mountpoint": card["mountpoint"], "library_path": card["library_path"]}

    def _check_android_geometry(self, table, recorded):
        """The live Android p2/p3 entries must equal the checkpoint's."""
        parts = table.get("partitions") if isinstance(table, dict) else None
        if not isinstance(recorded, list) or not isinstance(parts, list) or len(recorded) != 2:
            raise InspectorError("identity_mismatch", "the recorded Android geometry is unusable")
        for old, new in zip(recorded, parts[1:]):
            if (old.get("start") != new.get("start") or old.get("size") != new.get("size")
                    or str(old.get("type", "")).lower() != str(new.get("type", "")).lower()
                    or _norm_uuid(old.get("uuid")) != _norm_uuid(new.get("uuid"))):
                raise InspectorError("identity_mismatch", "the Android partitions changed")

    # -- helpers ------------------------------------------------------------

    def _validate_handoff(self, raw):
        try:
            return tf_card.validate_handoff(raw)
        except ValueError as error:
            raise InspectorError("handoff_invalid", str(error)) from error

    def _check_identity(self, table, handoff, cid, size):
        try:
            tf_card.validate_layout(table, handoff, cid, size)
        except ValueError as error:
            raise InspectorError("identity_mismatch", str(error)) from error

    def _blkid(self, device):
        """``(UUID, fstype)`` for the reserve partition only."""
        try:
            result = service._capture(self._runner, ["blkid", "-o", "export", device])
        except (OSError, subprocess.SubprocessError) as error:
            raise InspectorError("inspection_failed", str(error)) from error
        if result.returncode:
            return None, None
        values = {}
        for line in (result.stdout or "").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip()
        return values.get("UUID"), (values.get("TYPE") or "").lower()

    def _mounted_target(self, device):
        """The mount target whose source resolves to ``device``, or ``None``."""
        try:
            result = service._capture(self._runner, ["findmnt", "--json", "--list",
                                                     "--output", "SOURCE,TARGET"])
        except (OSError, subprocess.SubprocessError) as error:
            raise InspectorError("inspection_failed", str(error)) from error
        if result.returncode:
            return None
        try:
            data = json.loads(result.stdout or "{}")
        except ValueError:
            return None
        for entry in (data.get("filesystems") or []) if isinstance(data, dict) else []:
            if _same_device(entry.get("source") or "", device):
                return entry.get("target")
        return None

    def _reserve_lock(self, device):
        return service._ReserveLock(self._runtime.parent /
                                    ("jupiter-automount-%s.lock" % os.path.basename(device)))

    def _reserve_mount(self, device, cid):
        existing = self._mounted_target(device)
        if existing:
            return Path(existing), False
        base = self._runtime / "inspect" / cid
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InspectorError("inspection_failed", str(error)) from error
        result = service._capture(self._runner, ["mount", "-o", "ro,nosuid,nodev,noexec", device, str(base)])
        if result.returncode:
            detail = (result.stderr or "").strip() or "could not mount the reserve read-only"
            raise InspectorError("inspection_failed", detail)
        return base, True

    def _reserve_unmount(self, mountpoint):
        try:
            result = service._capture(self._runner, ["umount", str(mountpoint)])
        except (OSError, subprocess.SubprocessError) as error:
            raise InspectorError("inspection_failed", str(error)) from error
        if result.returncode:
            detail = (result.stderr or "").strip() or "could not unmount the reserve read-only"
            raise InspectorError("inspection_failed", detail)
