"""Read-only inspection tests for ``armada_installer.tf_inspection``.

The production guards are exercised end to end: discovery, the sysfs SD/CID
identity, the boot/critical-mount refusals, the GPT/partuuid identity, the
marker bounds and the filesystem-UUID match all stay in the path.  Only the
runner and the physical ``stat``/``is_block_device`` probes are injected, so the
real code still decides and the public API never gets a bypass flag.
"""

import fcntl
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "py_modules"))

from armada_installer import tf_card, tf_inspection, tf_state  # noqa: E402

MIB = 1024 * 1024
DISK, RESERVE, META, PRIVATE = "/dev/mmcblk0", "/dev/mmcblk0p1", "/dev/mmcblk0p2", "/dev/mmcblk0p3"
CID = "1b2c3d4e5f60718293a4b5c6d7e8f901"
CID_OTHER = "0011223344556677889900aabbccddee"
DISK_BYTES = 512 * MIB
SECTOR = 512
FIRST_LBA, LAST_LBA = 2048, DISK_BYTES // SECTOR - 34
DISK_GUID = "01234567-89ab-cdef-0123-456789abcdef"
RESERVE_PARTUUID = "11111111-2222-3333-4444-555555555555"
META_PARTUUID = "22222222-3333-4444-5555-666666666666"
PRIVATE_PARTUUID = "99999999-8888-7777-6666-555555555555"
RESERVE_FS_UUID = "1A2B-3C4D"
PRIVATE_FS_UUID = "abcdef01-2345-6789-abcd-ef0123456789"
LINUX_FS_UUID = "00112233-4455-6677-8899-aabbccddeeff"
DISK_MAJMIN = (179, 0)
_REAL_STAT = os.stat


def native_parts(reserve_type):
    reserve = 256 * MIB // SECTOR
    meta_start = FIRST_LBA + reserve
    private_start = meta_start + 16 * MIB // SECTOR
    private = LAST_LBA - private_start + 1
    return [
        {"node": RESERVE, "start": FIRST_LBA, "size": reserve, "type": reserve_type,
         "uuid": RESERVE_PARTUUID, "name": "linux-reserve"},
        {"node": META, "start": meta_start, "size": 16 * MIB // SECTOR, "type": tf_card.ANDROID_META,
         "uuid": META_PARTUUID, "name": "android-meta"},
        {"node": PRIVATE, "start": private_start, "size": private, "type": tf_card.ANDROID_PRIVATE,
         "uuid": PRIVATE_PARTUUID, "name": "android-private"},
    ]


def table(reserve_type=tf_card.RESERVE_PENDING, parts=None):
    return {"label": "gpt", "sectorsize": SECTOR, "firstlba": FIRST_LBA, "lastlba": LAST_LBA,
            "id": DISK_GUID, "device": DISK,
            "partitions": parts if parts is not None else native_parts(reserve_type)}


def handoff(**overrides):
    base = {"magic": "armada-tf-handoff", "schema_version": 1, "card_cid": CID,
            "disk_bytes": DISK_BYTES, "linux_ratio": 50, "public_fs_uuid": RESERVE_FS_UUID,
            "public_partition_guid": RESERVE_PARTUUID, "private_partition_guid": PRIVATE_PARTUUID,
            "private_fs_uuid": PRIVATE_FS_UUID}
    base.update(overrides)
    return base


class TfRunner:
    """Answers only the read-only probes; refuses shell and unknown commands."""

    def __init__(self):
        self.calls = []
        self.disks = [{"name": DISK, "path": DISK, "type": "disk", "size": DISK_BYTES,
                       "partuuid": None, "pkname": None}]
        self.table = table()
        self.children = self._children(tf_card.RESERVE_PENDING)
        self.blkid_values = {"UUID": RESERVE_FS_UUID, "TYPE": "vfat"}
        self.mounts = []
        self.critical = {}
        self.boot_source = "/dev/nvme0n1p2"
        self.ancestors = ["259:0"]
        self.ancestors_by_source = {}
        self.inverse_rc = 0
        self.umount_rc = 0

    @staticmethod
    def _children(reserve_type):
        return [{"name": part["node"].rsplit("/", 1)[-1], "path": part["node"], "type": "part",
                 "size": part["size"] * SECTOR, "partuuid": part["uuid"], "pkname": "mmcblk0"}
                for part in native_parts(reserve_type)]

    def _done(self, argv, code=0, out="", err=""):
        return subprocess.CompletedProcess(argv, code, out, err)

    def __call__(self, argv, **kwargs):
        if not isinstance(argv, list) or not argv:
            raise AssertionError("blocking calls must pass an argv list: {!r}".format(argv))
        if kwargs.get("shell"):
            raise AssertionError("shell execution is never allowed")
        self.calls.append(list(argv))
        command = argv[0]
        if command == "lsblk":
            if "--inverse" in argv:
                if self.inverse_rc:
                    return self._done(argv, 1, "", "lsblk failed")
                source = argv[-1]
                lines = self.ancestors_by_source.get(source, self.ancestors)
                return self._done(argv, 0, "\n".join(lines) + "\n")
            if DISK in argv:
                top = {"name": DISK, "path": DISK, "type": "disk", "size": DISK_BYTES,
                       "partuuid": None, "pkname": None, "children": self.children}
                return self._done(argv, 0, json.dumps({"blockdevices": [top]}))
            return self._done(argv, 0, json.dumps({"blockdevices": self.disks}))
        if command == "sfdisk":
            return self._done(argv, 0, json.dumps({"partitiontable": self.table}))
        if command == "blkid":
            return self._done(argv, 0, "".join("%s=%s\n" % item for item in self.blkid_values.items()))
        if command == "findmnt":
            if "--json" in argv:
                return self._done(argv, 0, json.dumps({"filesystems": self.mounts}))
            target = argv[argv.index("--target") + 1] if "--target" in argv else ""
            return self._done(argv, 0, str(self.critical.get(target, self.boot_source)) + "\n")
        if command == "mount":
            return self._done(argv, 0)
        if command == "umount":
            return self._done(argv, self.umount_rc, "", "umount failed" if self.umount_rc else "")
        raise AssertionError("unexpected command: {}".format(command))

    def ran(self, *commands):
        return [call for call in self.calls if call[0] in commands]


class InspectionTestCase(unittest.TestCase):
    reserve_type = tf_card.RESERVE_PENDING

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.sysfs = self.root / "sys"
        self.runtime = self.root / "run" / "armada-installer-tf"
        self.mount_dir = self.root / "mnt"
        self.mount_dir.mkdir()
        self._write_sysfs("SD", CID)
        self.runner = TfRunner()
        self.runner.table = table(self.reserve_type)
        self.runner.children = TfRunner._children(self.reserve_type)
        self._patch(pathlib.Path, "is_block_device", lambda self: True)
        self._patch(tf_inspection.service.os, "stat", self._stat)
        self._set_is_mount(())

    def _patch(self, obj, name, value):
        started = patch.object(obj, name, value)
        started.start()
        self.addCleanup(started.stop)

    def _stat(self, path, *args, **kwargs):
        if str(path) == DISK:
            return SimpleNamespace(st_rdev=os.makedev(*DISK_MAJMIN))
        return _REAL_STAT(path, *args, **kwargs)

    def _set_is_mount(self, targets):
        targets = set(targets)
        self._patch(pathlib.Path, "is_mount", lambda self: str(self) in targets)

    def _write_sysfs(self, kind, cid):
        device = self.sysfs / "class" / "block" / "mmcblk0" / "device"
        device.mkdir(parents=True, exist_ok=True)
        (device / "type").write_text(kind + "\n", encoding="utf-8")
        (device / "cid").write_text(cid + "\n", encoding="utf-8")

    def _inspector(self):
        return tf_inspection.TfInspector(runner=self.runner, sysfs_dir=self.sysfs,
                                         runtime_dir=self.runtime)

    def _write_marker(self, root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def _existing_mount(self, source=RESERVE):
        self.runner.mounts = [{"source": source, "target": str(self.mount_dir)}]
        return self.mount_dir


class DiscoveryTests(InspectionTestCase):
    def test_physical_sd_card_is_snapshotted(self):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["card_cid"], card["reserve_device"]),
                         ("pending", CID, RESERVE))
        parts = native_parts(tf_card.RESERVE_PENDING)
        self.assertEqual(card["linux_bytes"], parts[0]["size"] * SECTOR)
        self.assertEqual(card["android_bytes"], parts[2]["size"] * SECTOR)

    def test_non_sd_and_internal_disks_are_not_offered(self):
        self._write_sysfs("MMC", CID)
        self.assertEqual(self._inspector().inspect_cards()["cards"], [])
        self._write_sysfs("SD", CID)
        self.runner.disks = [{"name": "/dev/nvme0n1", "path": "/dev/nvme0n1", "type": "disk",
                              "size": DISK_BYTES, "partuuid": None, "pkname": None}]
        self.assertEqual(self._inspector().inspect_cards()["cards"], [])

    def test_sd_without_cid_is_not_offered(self):
        self._write_sysfs("SD", "not-a-cid")
        self.assertEqual(self._inspector().inspect_cards()["cards"], [])

    def test_foreign_partition_uuid_is_refused(self):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        self.runner.children[1]["partuuid"] = "00000000-0000-0000-0000-000000000000"
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "layout_unsupported"))


class RefusalTests(InspectionTestCase):
    def test_boot_disk_is_refused(self):
        self.runner.ancestors = ["179:0"]  # the card backs the running root
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "boot_disk_forbidden"))
        with self.assertRaises(tf_inspection.InspectorError) as caught:
            self._inspector().snapshot(DISK)
        self.assertEqual(caught.exception.code, "boot_disk_forbidden")
        self.assertEqual(self.runner.ran("mount", "sfdisk"), [])

    def test_critical_mount_ancestry_is_refused(self):
        self.runner.critical = {"/var": PRIVATE}
        self.runner.ancestors_by_source = {PRIVATE: ["179:0"]}
        self._set_is_mount(("/var",))
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "boot_disk_forbidden"))

    def test_unknown_critical_mount_fails_closed(self):
        self.runner.critical = {"/boot": ""}
        self._set_is_mount(("/boot",))
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "boot_disk_unknown"))

    def test_unknown_boot_ancestry_fails_closed(self):
        self.runner.inverse_rc = 1
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "boot_disk_unknown"))


class MarkerTests(InspectionTestCase):
    def test_missing_marker_is_blocked_with_instruction(self):
        self._existing_mount()
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "handoff_missing"))
        self.assertIn("Android app", card["detail"])

    def test_malformed_marker_is_blocked(self):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE, "{not json")
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "handoff_invalid"))

    def test_symlinked_marker_is_blocked(self):
        mount = self._existing_mount()
        target = self.mount_dir / tf_inspection.ANDROID_HANDOFF_RELATIVE
        target.parent.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(handoff()), encoding="utf-8")
        target.symlink_to(outside)
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "handoff_invalid"))

    def test_oversized_marker_is_blocked(self):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE,
                           " " * (tf_inspection.MARKER_LIMIT + 1))
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "handoff_invalid"))

    def test_filesystem_uuid_mismatch_is_blocked(self):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE,
                           handoff(public_fs_uuid="0A0B-0C0D"))
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "filesystem_mismatch"))

    def test_card_cid_mismatch_is_blocked(self):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE,
                           handoff(card_cid=CID_OTHER))
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "identity_mismatch"))

    def test_missing_connected_marker_is_blocked(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = TfRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        self._existing_mount()
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "marker_missing"))
        detail = card["detail"].lower()
        self.assertNotIn("revert", detail)
        self.assertNotIn("uninstall", detail)
        self.assertIn("logs", detail)


class SnapshotTests(InspectionTestCase):
    def test_pending_snapshot_is_readonly_and_unmounts(self):
        self._write_marker(self.runtime / "inspect" / CID, tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        snapshot = self._inspector().snapshot(DISK)
        self.assertEqual((snapshot["mode"], snapshot["filesystem_type"]), ("format", "vfat"))
        self.assertEqual(snapshot["filesystem_uuid"], RESERVE_FS_UUID)
        self.assertIsNone(snapshot["mountpoint"])
        self.assertIsNone(snapshot["library_path"])
        mounts = self.runner.ran("mount")
        self.assertEqual(mounts[0][1:4], ["-o", "ro,nosuid,nodev,noexec", RESERVE])
        self.assertEqual(self.runner.ran("umount")[0][-1], str(self.runtime / "inspect" / CID))
        self.assertEqual(self.runner.ran("mkfs.ext4", "sgdisk", "wipefs"), [])

    def test_connected_snapshot_uses_mount_mode(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = TfRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        marker = dict(handoff(public_partition_guid=None))
        marker["linux_fs_uuid"] = LINUX_FS_UUID
        self._write_marker(self._existing_mount(), tf_inspection.LINUX_MARKER_NAME, marker)
        snapshot = self._inspector().snapshot(DISK)
        self.assertEqual((snapshot["mode"], snapshot["filesystem_type"]), ("mount", "ext4"))
        self.assertEqual(snapshot["mountpoint"], str(self.mount_dir))
        self.assertEqual(snapshot["library_path"], str(self.mount_dir / "SteamLibrary"))
        self.assertEqual(self.runner.ran("mkfs.ext4", "sgdisk"), [])

    def test_connected_linux_uuid_mismatch_is_blocked(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = TfRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        marker = dict(handoff(public_partition_guid=None))
        marker["linux_fs_uuid"] = "deadbeef-0000-0000-0000-000000000000"
        self._write_marker(self._existing_mount(), tf_inspection.LINUX_MARKER_NAME, marker)
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "filesystem_mismatch"))

    def test_unknown_device_is_refused(self):
        with self.assertRaises(tf_inspection.InspectorError) as caught:
            self._inspector().snapshot("/dev/mmcblk9")
        self.assertEqual(caught.exception.code, "unknown_device")

    def test_failed_unmount_is_refused(self):
        self.runner.umount_rc = 1
        self._write_marker(self.runtime / "inspect" / CID, tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "inspection_failed"))

    def test_busy_reserve_lock_fails_fast(self):
        lock_path = self.runtime.parent / "jupiter-automount-mmcblk0p1.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        held = lock_path.open("a+")
        self.addCleanup(held.close)
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "card_busy"))

    def test_fingerprint_ignores_the_volatile_mountpoint(self):
        self._write_marker(self.runtime / "inspect" / CID, tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        snapshot = self._inspector().snapshot(DISK)
        moved = dict(snapshot, mountpoint="/somewhere/else", library_path="/somewhere/else/SteamLibrary")
        self.assertEqual(tf_inspection.fingerprint(snapshot), tf_inspection.fingerprint(moved))
        changed = dict(snapshot, card_cid=CID_OTHER)
        self.assertNotEqual(tf_inspection.fingerprint(snapshot), tf_inspection.fingerprint(changed))

    def test_construction_rejects_bypass_keywords(self):
        with self.assertRaises(TypeError):
            tf_inspection.TfInspector(self.runner, sysfs_dir=self.sysfs, runtime_dir=self.runtime,
                                      unsafe=True)

    def test_a_validated_checkpoint_resumes_a_retyped_reserve(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = TfRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": "DEAD-BEEF", "TYPE": "vfat"}
        checkpoint_dir = self.root / "checkpoints"
        tf_state.write_checkpoint(checkpoint_dir, CID, {
            "stage": "validated", "card_cid": CID, "size_bytes": DISK_BYTES, "handoff": handoff(),
            "reserve_device": RESERVE, "reserve_type": tf_card.RESERVE_CONNECTED,
            "android_parts": native_parts(tf_card.RESERVE_CONNECTED)[1:], "linux_fs_uuid": None})
        inspector = tf_inspection.TfInspector(runner=self.runner, sysfs_dir=self.sysfs,
                                              runtime_dir=self.runtime, checkpoint_dir=checkpoint_dir)
        card = inspector.inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("recovery", ""))
        snapshot = inspector.snapshot(DISK)
        self.assertEqual((snapshot["mode"], snapshot["recovery"]), ("format", True))
        self.assertEqual(self.runner.ran("mount", "sgdisk", "mkfs.ext4"), [])

    def test_an_unrecoverable_connected_reserve_stays_blocked(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = TfRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": "DEAD-BEEF", "TYPE": "vfat"}
        card = self._inspector().inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("blocked", "reserve_unprepared"))


if __name__ == "__main__":
    unittest.main()
