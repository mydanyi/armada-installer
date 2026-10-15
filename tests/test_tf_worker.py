"""Behaviour tests for the root ``armada_installer.tf_worker``.

Everything the worker would do to a real card is injected through the runner, so
the exact argv list is pinned and no device, systemd unit, udev or Steam process
is ever touched.  The tests cover the destructive-path contract: only ``p1`` is
retyped, only the reserve partition is formatted (never the whole card or the
Android side), no zap/wipe command is ever issued, a failure before the write
halts, a formatted checkpoint resumes without a second ``mkfs``, connected cards
are never formatted, and an absent Steam leaves the card mounted and pending.
"""

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

from armada_installer import service, tf_card, tf_state, tf_worker  # noqa: E402

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
NEW_FS_UUID = "ffeeddcc-bbaa-9988-7766-554433221100"
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


def table(reserve_type=tf_card.RESERVE_PENDING):
    return {"label": "gpt", "sectorsize": SECTOR, "firstlba": FIRST_LBA, "lastlba": LAST_LBA,
            "id": DISK_GUID, "device": DISK, "partitions": native_parts(reserve_type)}


def handoff(**overrides):
    base = {"magic": "armada-tf-handoff", "schema_version": 1, "card_cid": CID,
            "disk_bytes": DISK_BYTES, "linux_ratio": 50, "public_fs_uuid": RESERVE_FS_UUID,
            "public_partition_guid": RESERVE_PARTUUID, "private_partition_guid": PRIVATE_PARTUUID,
            "private_fs_uuid": PRIVATE_FS_UUID}
    base.update(overrides)
    return base


class WorkerRunner:
    """A card/Steam model that changes state only in response to worker actions."""

    def __init__(self):
        self.calls = []
        self.table = table()
        self.children = self._children(tf_card.RESERVE_PENDING)
        self.disk_size = DISK_BYTES
        self.blkid_values = {"UUID": RESERVE_FS_UUID, "TYPE": "vfat"}
        self.mounted = False
        self.mount_target = None
        self.steam_running = False
        self.ancestors = ["259:0"]
        self.ancestors_by_source = {}
        self.inverse_rc = 0
        self.sgdisk_rc = 0
        self.mkfs_rc = 0
        self.steam_rc = 0
        self.vdf_path = None
        self.default_mount = None

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
        if command == "pgrep":
            return self._done(argv, 0 if self.steam_running else 1, "")
        if command == "lsblk":
            if "--inverse" in argv:
                if self.inverse_rc:
                    return self._done(argv, 1, "", "lsblk failed")
                return self._done(argv, 0, "\n".join(
                    self.ancestors_by_source.get(argv[-1], self.ancestors)) + "\n")
            if DISK in argv:
                top = {"name": DISK, "path": DISK, "type": "disk", "size": self.disk_size,
                       "partuuid": None, "pkname": None, "children": self.children}
                return self._done(argv, 0, json.dumps({"blockdevices": [top]}))
            return self._done(argv, 0, json.dumps({"blockdevices": [
                {"name": DISK, "path": DISK, "type": "disk", "size": self.disk_size,
                 "partuuid": None, "pkname": None}]}))
        if command == "sfdisk":
            return self._done(argv, 0, json.dumps({"partitiontable": self.table}))
        if command == "blkid":
            return self._done(argv, 0, "".join("%s=%s\n" % item for item in self.blkid_values.items()))
        if command == "findmnt":
            if "--json" in argv:
                entries = ([{"source": RESERVE, "target": str(self.mount_target), "fstype": "ext4",
                             "options": "rw,relatime"}] if self.mounted else [])
                return self._done(argv, 0, json.dumps({"filesystems": entries}))
            return self._done(argv, 0, "/dev/nvme0n1p2\n")
        if command == "umount":
            self.mounted, self.mount_target = False, None
            return self._done(argv, 0)
        if command == tf_worker.AUTOMOUNT_HELPER:
            self.mounted, self.mount_target = True, self.mount_target or self.default_mount
            return self._done(argv, 0)
        if command == "sgdisk":
            if self.sgdisk_rc == 0 and "--typecode=1:8300" in argv:
                # A native retype really does change the live GPT, so the model
                # mutates its table too; the guards still have to hold.
                self.table["partitions"][0]["type"] = tf_card.RESERVE_CONNECTED
            return self._done(argv, self.sgdisk_rc, "", "sgdisk failed" if self.sgdisk_rc else "")
        if command == "partprobe":
            return self._done(argv, 0)
        if command == "udevadm":
            return self._done(argv, 0)
        if command == "mkfs.ext4":
            if self.mkfs_rc:
                return self._done(argv, self.mkfs_rc, "", "mkfs failed")
            self.blkid_values = {"UUID": NEW_FS_UUID, "TYPE": "ext4"}
            return self._done(argv, 0)
        if command == "systemd-run":
            if self.steam_rc == 0 and self.vdf_path is not None:
                self._write_vdf()
            return self._done(argv, self.steam_rc, "", "steam request failed" if self.steam_rc else "")
        raise AssertionError("unexpected command: {}".format(command))

    def _write_vdf(self):
        path = self.vdf_path / "libraryfolders.vdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        library = self.default_mount / "SteamLibrary"
        path.write_text('"libraryfolders"\n{\n  "0"\n  {\n    "path" "%s"\n  }\n}\n' % library,
                        encoding="utf-8")

    def ran(self, *commands):
        return [call for call in self.calls if call[0] in commands]


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.sysfs = self.root / "sys"
        self.runtime = self.root / "run" / "armada-installer-tf"
        self.checkpoint = self.root / "var" / "tf"
        self.mount_dir = self.root / "mnt"
        self.mount_dir.mkdir()
        self.status_path = self.runtime / tf_state.STATUS_NAME
        self.log_path = self.runtime / tf_state.LOG_NAME
        self.home = self.root / "home"
        self.home.mkdir()
        self._write_sysfs("SD", CID)
        self.runner = WorkerRunner()
        self.runner.default_mount = self.mount_dir
        self.runner.vdf_path = self.home / ".local" / "share" / "Steam" / "steamapps"
        self._patch(pathlib.Path, "is_block_device", lambda self: True)
        self._patch(pathlib.Path, "is_mount", lambda self: False)
        self._patch(service.os, "stat", self._stat)
        self._patch(tf_worker.os, "chown", lambda *a, **k: None)
        self._patch(tf_worker.pwd, "getpwuid", self._getpwuid)

    def _patch(self, obj, name, value):
        started = patch.object(obj, name, value)
        started.start()
        self.addCleanup(started.stop)

    def _stat(self, path, *args, **kwargs):
        if str(path) == DISK:
            return SimpleNamespace(st_rdev=os.makedev(*DISK_MAJMIN))
        return _REAL_STAT(path, *args, **kwargs)

    def _getpwuid(self, uid):
        return SimpleNamespace(pw_uid=uid, pw_gid=uid, pw_dir=str(self.home))

    def _write_sysfs(self, kind, cid):
        device = self.sysfs / "class" / "block" / "mmcblk0" / "device"
        device.mkdir(parents=True, exist_ok=True)
        (device / "type").write_text(kind + "\n", encoding="utf-8")
        (device / "cid").write_text(cid + "\n", encoding="utf-8")

    def _plan(self, mode="format"):
        reserve_type = tf_card.RESERVE_PENDING if mode == "format" else tf_card.RESERVE_CONNECTED
        filesystem_uuid = RESERVE_FS_UUID if mode == "format" else LINUX_FS_UUID
        return {"device": DISK, "reserve_device": RESERVE, "card_cid": CID, "size_bytes": DISK_BYTES,
                "mode": mode, "handoff": handoff(), "table": table(reserve_type),
                "filesystem_uuid": filesystem_uuid,
                "android_bytes": native_parts(reserve_type)[2]["size"] * SECTOR,
                "linux_bytes": native_parts(reserve_type)[0]["size"] * SECTOR}

    def _worker(self, plan, runner=None):
        return tf_worker.TfWorker(plan, status_path=self.status_path, log_path=self.log_path,
                                  runner=runner or self.runner, sysfs_dir=self.sysfs,
                                  runtime_dir=self.runtime, checkpoint_dir=self.checkpoint)

    def _status(self):
        return tf_state.read_json(self.status_path) or {}


class BusyRunner(WorkerRunner):
    """A runner whose reserve is mounted and refuses to unmount."""

    def __call__(self, argv, **kwargs):
        if argv and argv[0] == "umount":
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 1, "", "target is busy")
        return super().__call__(argv, **kwargs)


class FormatTests(WorkerTestCase):
    def test_format_retypes_only_p1_and_formats_only_the_reserve(self):
        self.runner.mounted, self.runner.mount_target = True, self.mount_dir
        code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 0)
        grans = self.runner.ran("sgdisk")
        self.assertEqual(grans, [["sgdisk", "--typecode=1:8300", DISK]])
        mkfs = self.runner.ran("mkfs.ext4")
        self.assertEqual(len(mkfs), 1)
        self.assertEqual(mkfs[0][-1], RESERVE)
        self.assertNotIn(DISK, mkfs[0])
        self.assertIn("nodiscard", mkfs[0])
        self.assertIn("ARMADA_TF_%s" % CID[-6:], mkfs[0])
        self.assertEqual(self.runner.ran("umount")[0][-1], str(self.mount_dir))
        # The upstream helper mounted the reserve exactly once.
        self.assertEqual(self.runner.ran(tf_worker.AUTOMOUNT_HELPER)[0][1:],
                         ["add", os.path.basename(RESERVE)])
        self.assertEqual(self._status()["state"], "succeeded")

    def test_no_zap_or_wipe_commands_are_ever_issued(self):
        self._worker(self._plan("format")).run()
        executables = {call[0] for call in self.runner.calls}
        for forbidden in ("wipefs", "blkdiscard", "dd"):
            self.assertNotIn(forbidden, executables)
        for call in self.runner.calls:
            if call[0] == "sgdisk":
                for option in call[1:]:
                    self.assertNotIn(option, ("-Z", "--zap", "--zap-all"))

    def test_a_sgdisk_failure_halts_before_mkfs(self):
        self.runner.sgdisk_rc = 1
        code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("mkfs.ext4"), [])
        self.assertEqual(self._status()["state"], "failed")

    def test_the_validated_checkpoint_is_written_before_any_format(self):
        self._worker(self._plan("format")).run()
        record = tf_state.read_checkpoint(self.checkpoint, CID)
        self.assertEqual(record["stage"], "formatted")
        self.assertEqual(record["size_bytes"], DISK_BYTES)
        self.assertEqual(record["android_parts"][0]["uuid"], META_PARTUUID)
        self.assertEqual(record["android_parts"][1]["uuid"], PRIVATE_PARTUUID)
        self.assertEqual(record["linux_fs_uuid"], NEW_FS_UUID)


class ResumeTests(WorkerTestCase):
    def _preformatted(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": NEW_FS_UUID, "TYPE": "ext4"}
        plan = self._plan("format")
        plan["table"] = table(tf_card.RESERVE_CONNECTED)
        tf_state.write_checkpoint(self.checkpoint, CID, {
            "stage": "formatted", "card_cid": CID, "size_bytes": DISK_BYTES, "handoff": handoff(),
            "reserve_device": RESERVE, "reserve_type": tf_card.RESERVE_CONNECTED,
            "android_parts": native_parts(tf_card.RESERVE_CONNECTED)[1:], "linux_fs_uuid": NEW_FS_UUID})
        return plan

    def test_a_formatted_checkpoint_resumes_without_a_second_mkfs(self):
        code = self._worker(self._preformatted()).run()
        self.assertEqual(code, 0)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4"), [])
        self.assertEqual(self.runner.ran(tf_worker.AUTOMOUNT_HELPER)[0][1:], ["add", "mmcblk0p1"])
        self.assertEqual(self._status()["state"], "succeeded")

    def test_a_validated_checkpoint_resumes_the_unformatted_reserve(self):
        runner = WorkerRunner()
        runner.default_mount = self.mount_dir
        runner.vdf_path = self.home / ".local" / "share" / "Steam" / "steamapps"
        runner.table = table(tf_card.RESERVE_CONNECTED)
        runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        runner.blkid_values = {}  # retyped to Linux but not yet formatted
        plan = self._plan("format")
        plan["filesystem_uuid"] = None
        plan["recovery"] = True
        plan["table"] = table(tf_card.RESERVE_CONNECTED)
        tf_state.write_checkpoint(self.checkpoint, CID, {
            "stage": "validated", "card_cid": CID, "size_bytes": DISK_BYTES, "handoff": handoff(),
            "reserve_device": RESERVE, "reserve_type": tf_card.RESERVE_CONNECTED,
            "android_parts": native_parts(tf_card.RESERVE_CONNECTED)[1:], "linux_fs_uuid": None})
        code = self._worker(plan, runner=runner).run()
        self.assertEqual(code, 0)
        self.assertEqual(len(runner.ran("mkfs.ext4")), 1)
        self.assertEqual(self._status()["state"], "succeeded")


class RefusalTests(WorkerTestCase):
    def test_a_stale_card_cid_is_refused_at_the_worker(self):
        plan = self._plan("format")
        plan["card_cid"] = CID_OTHER
        code = self._worker(plan).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4"), [])
        self.assertEqual(self._status()["state"], "failed")

    def test_a_boot_backed_card_is_refused_at_the_worker(self):
        self.runner.ancestors = ["179:0"]
        code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4"), [])

    def test_a_busy_reserve_is_refused_before_mkfs(self):
        runner = BusyRunner()
        runner.default_mount = self.mount_dir
        runner.mounted, runner.mount_target = True, self.mount_dir
        code = self._worker(self._plan("format"), runner=runner).run()
        self.assertEqual(code, 1)
        self.assertEqual(runner.ran("mkfs.ext4"), [])

    def _extra_runner(self):
        runner = WorkerRunner()
        runner.default_mount = self.mount_dir
        runner.vdf_path = self.home / ".local" / "share" / "Steam" / "steamapps"
        return runner

    def test_a_changed_whole_capacity_is_refused_before_any_write(self):
        runner = self._extra_runner()
        # Same CID and same GPT, but the whole card now reports fewer bytes.
        runner.disk_size = DISK_BYTES - 4096
        code = self._worker(self._plan("format"), runner=runner).run()
        self.assertEqual(code, 1)
        self.assertEqual(runner.ran("umount", "sgdisk", "mkfs.ext4"), [])
        self.assertIn("target_changed", self._status()["detail"])

    def test_a_changed_reserve_filesystem_uuid_is_refused_before_any_write(self):
        runner = self._extra_runner()
        # The reserve now carries a different filesystem than the prepared one.
        runner.blkid_values = {"UUID": "1234-5678", "TYPE": "vfat"}
        code = self._worker(self._plan("format"), runner=runner).run()
        self.assertEqual(code, 1)
        self.assertEqual(runner.ran("umount", "sgdisk", "mkfs.ext4"), [])
        self.assertIn("target_changed", self._status()["detail"])


class ConnectedTests(WorkerTestCase):
    def test_a_connected_card_is_mounted_but_never_formatted(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        code = self._worker(self._plan("mount")).run()
        self.assertEqual(code, 0)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4", "partprobe"), [])
        self.assertEqual(self.runner.ran(tf_worker.AUTOMOUNT_HELPER)[0][1:], ["add", "mmcblk0p1"])
        self.assertTrue((self.mount_dir / tf_worker.MARKER_NAME).is_file())
        self.assertTrue((self.mount_dir / "SteamLibrary" / "steamapps").is_dir())


class SteamTests(WorkerTestCase):
    def _mount_plan(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        return self._plan("mount")

    def test_an_absent_steam_leaves_the_card_mounted_and_pending(self):
        self.runner.steam_running = False
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertEqual(status["state"], "succeeded")
        self.assertFalse(status["library_requested"])
        self.assertFalse(status["library_registered"])
        self.assertTrue(status["library_pending"])
        self.assertEqual(self.runner.ran("systemd-run"), [])

    def test_a_cli_success_without_membership_is_not_a_registration(self):
        self.runner.steam_running = True
        self.runner.vdf_path = None  # no VDF will be written, so membership is unconfirmed
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertTrue(status["library_requested"])
        self.assertFalse(status["library_registered"])
        self.assertTrue(status["library_pending"])
        argv = self.runner.ran("systemd-run")[0]
        self.assertIn("--uid=1000", argv)
        self.assertIn("--collect", argv)
        self.assertIn("--setenv=XDG_RUNTIME_DIR=/run/user/1000", argv)
        self.assertTrue(argv[-1].startswith("steam://addlibraryfolder/"))

    def test_a_confirmed_membership_reports_registered(self):
        self.runner.steam_running = True
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertTrue(status["library_requested"])
        self.assertTrue(status["library_registered"])
        self.assertFalse(status["library_pending"])


if __name__ == "__main__":
    unittest.main()
