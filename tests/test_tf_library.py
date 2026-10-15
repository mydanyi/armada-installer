"""Public TF-library facade and preparation-plan tests.

``service.TfLibraryService`` is a thin public facade over
``armada_installer.tf_inspection.TfInspector``: it keeps the plugin-facing
``inspect``/``prepare`` contract and the single-use plan token while every card
read and validation is delegated to the one inspector implementation.  These
tests pin that facade contract; the read-only inspection guards themselves are
covered end to end in ``test_tf_inspection``.
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

from armada_installer import service, tf_card, tf_inspection, tf_state  # noqa: E402

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
    """Answers only the read-only probes the inspector issues; refuses shell/unknowns.

    The systemctl/systemd-run probes are answered only so the facade's
    ``start``/``status`` paths can be exercised; nothing here ever runs a real
    unit or a real installer.
    """

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
        self.unit_show = "LoadState=not-found\nActiveState=inactive\nSubState=dead\n"
        self.launch_code = 0
        self.cleanup_code = 0

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
                return self._done(argv, 0, "\n".join(self.ancestors_by_source.get(source, self.ancestors)) + "\n")
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
        if command in ("mount", "umount"):
            return self._done(argv, 0)
        if command == "systemctl":
            action = argv[1] if len(argv) > 1 else ""
            if action in ("reset-failed", "stop"):
                if self.cleanup_code:
                    return self._done(argv, self.cleanup_code, "", "cleanup failed")
                return self._done(argv, 0)
            return self._done(argv, 0, self.unit_show, "")
        if command == "systemd-run":
            return self._done(argv, self.launch_code, "", "launch failed" if self.launch_code else "")
        raise AssertionError("unexpected command: {}".format(command))

    def ran(self, *commands):
        return [call for call in self.calls if call[0] in commands]

    def launched(self):
        return [call for call in self.calls if call[0] == "systemd-run"]


class TfFacadeTestCase(unittest.TestCase):
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
        self._patch(service.os, "stat", self._stat)
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

    def _service(self):
        return service.TfLibraryService(runner=self.runner, sysfs_dir=self.sysfs, runtime_dir=self.runtime)

    def _write_marker(self, root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def _existing_mount(self, source=RESERVE):
        self.runner.mounts = [{"source": source, "target": str(self.mount_dir)}]
        return self.mount_dir


class FacadeTests(TfFacadeTestCase):
    def test_inspect_lists_the_card_through_the_facade(self):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        card = self._service().inspect()["cards"][0]
        self.assertEqual((card["state"], card["card_cid"], card["reserve_device"]),
                         ("pending", CID, RESERVE))

    def test_prepare_format_plan_matches_the_public_contract(self):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        svc = self._service()
        plan = svc.prepare(DISK)
        self.assertEqual((plan["mode"], plan["erase_scope"]), ("format", "reserve_only_format"))
        self.assertTrue(plan["token"])
        self.assertEqual(plan["device"], DISK)
        self.assertEqual(plan["reserve_device"], RESERVE)
        self.assertEqual(plan["card_cid"], CID)
        self.assertEqual(plan["linux_bytes"], 256 * MIB)
        # Android bytes cover the private partition only, never the 16 MiB metadata.
        parts = native_parts(tf_card.RESERVE_PENDING)
        self.assertEqual(plan["android_bytes"], parts[2]["size"] * SECTOR)
        self.assertEqual(plan["preserves"]["android_p2"]["uuid"], META_PARTUUID.replace("-", ""))
        self.assertEqual(plan["preserves"]["android_p3"]["name"], "android-private")
        self.assertEqual(self.runner.ran("mount", "umount", "sgdisk", "mkfs.ext4", "wipefs"), [])

    def test_prepare_connected_plan_is_mount_only(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = TfRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        marker = dict(handoff(public_partition_guid=None))
        marker["linux_fs_uuid"] = LINUX_FS_UUID
        self._write_marker(self._existing_mount(), tf_inspection.LINUX_MARKER_NAME, marker)
        plan = self._service().prepare(DISK)
        self.assertEqual((plan["mode"], plan["erase_scope"]), ("mount", "none"))
        self.assertEqual(plan["device"], DISK)
        self.assertEqual(plan["reserve_device"], RESERVE)
        self.assertEqual(self.runner.ran("mkfs.ext4", "sgdisk"), [])

    def test_prepare_blocked_card_propagates_the_inspector_reason(self):
        self.runner.ancestors = ["179:0"]  # the card backs the running root
        with self.assertRaises(service.InstallerError) as caught:
            self._service().prepare(DISK)
        self.assertEqual(caught.exception.code, "boot_disk_forbidden")
        self.assertEqual(self.runner.ran("mount", "sgdisk"), [])

    def test_prepare_unknown_device_is_refused(self):
        with self.assertRaises(service.InstallerError) as caught:
            self._service().prepare("/dev/mmcblk9")
        self.assertEqual(caught.exception.code, "unknown_device")

    def test_identity_change_before_prepare_is_refused(self):
        svc = self._service()
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        self.assertEqual(svc.inspect()["cards"][0]["state"], "pending")
        self._write_marker(self.mount_dir, tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff(card_cid=CID_OTHER))
        with self.assertRaises(service.InstallerError) as caught:
            svc.prepare(DISK)
        self.assertEqual(caught.exception.code, "identity_mismatch")


class LibraryStartTests(TfFacadeTestCase):
    def _prepared(self, svc):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        return svc.prepare(DISK)

    def test_start_requires_confirmation_and_one_use_token(self):
        svc = self._service()
        plan = self._prepared(svc)
        for value in (False, 1, None):
            with self.subTest(confirmed=value):
                with self.assertRaises(service.InstallerError) as caught:
                    svc.start(plan["token"], confirmed=value)
                self.assertEqual(caught.exception.code, "confirmation_required")
        with self.assertRaises(service.InstallerError) as caught:
            svc.start("0" * 32, confirmed=True)
        self.assertEqual(caught.exception.code, "unknown_token")
        self.assertEqual(self.runner.launched(), [])
        svc.start(plan["token"], confirmed=True)
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "unknown_token")
        self.assertEqual(len(self.runner.launched()), 1)

    def test_start_launches_the_fixed_worker_unit(self):
        svc = self._service()
        plan = self._prepared(svc)
        result = svc.start(plan["token"], confirmed=True)
        self.assertEqual((result["state"], result["phase"]), ("running", "preparing"))
        argv = self.runner.launched()[0]
        self.assertEqual(argv[argv.index("--unit") + 1], "armada-tf-library.service")
        tail = argv[argv.index("--") + 1:]
        # The worker must run under the fixed system interpreter, never the
        # interpreter that happens to be running this facade.
        self.assertEqual(tail[0], "/usr/bin/python3")
        self.assertTrue(tail[1].endswith("tf_worker.py"))
        self.assertEqual(tail[2], str(svc._plan_path))
        self.assertEqual(tail[3], str(svc._status_path))
        # The plan and status records are written 0600 before the launch.
        self.assertEqual(oct(svc._plan_path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(svc._status_path.stat().st_mode & 0o777), "0o600")
        # No client-supplied command is ever passed to the fixed unit.
        self.assertNotIn("/usr/libexec/armada/armada-installer", argv)

    def test_start_rejects_a_running_conversion(self):
        svc = self._service()
        plan = self._prepared(svc)
        self.runner.unit_show = "LoadState=loaded\nActiveState=active\nSubState=running\n"
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "unit_running")
        self.assertEqual(self.runner.launched(), [])

    def test_start_rejects_a_stale_card(self):
        svc = self._service()
        plan = self._prepared(svc)
        # The disk GUID changes after preparation: the fingerprint differs even
        # though the handoff and layout still validate.
        self.runner.table = dict(table())
        self.runner.table["id"] = "deadbeef-0000-0000-0000-000000000000"
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "target_changed")
        self.assertEqual(self.runner.launched(), [])


class LibraryStatusTests(TfFacadeTestCase):
    def test_status_is_idle_without_a_unit_or_record(self):
        status = self._service().status()
        self.assertEqual((status["state"], status["phase"]), ("idle", ""))

    def test_status_reflects_the_worker_record_while_running(self):
        svc = self._service()
        self.runner.unit_show = "LoadState=loaded\nActiveState=active\nSubState=running\n"
        tf_state.atomic_write_json(svc._status_path,
                                   {"state": "running", "phase": "formatting", "detail": "x"})
        status = svc.status()
        self.assertEqual((status["state"], status["phase"], status["detail"]), ("running", "formatting", "x"))

    def test_systemd_failure_overrides_a_stale_success(self):
        svc = self._service()
        self.runner.unit_show = ("LoadState=loaded\nActiveState=failed\nSubState=failed\n"
                                 "Result=exit-code\nExecMainStatus=1\n")
        tf_state.atomic_write_json(svc._status_path,
                                   {"state": "succeeded", "phase": "registering", "detail": ""})
        self.assertEqual(svc.status()["state"], "failed")

    def test_status_sanitises_and_bounds_the_log(self):
        svc = self._service()
        self.runner.unit_show = ("LoadState=loaded\nActiveState=failed\nSubState=failed\n"
                                 "Result=exit-code\nExecMainStatus=1\n")
        svc._log_path.parent.mkdir(parents=True, exist_ok=True)
        svc._log_path.write_bytes(("\n".join("y" * 500 for _ in range(200))
                                   + "\nbad\x08\x1bprogress\n").encode("utf-8"))
        status = svc.status()
        self.assertNotIn("\x08", status["log_tail"])
        self.assertNotIn("\x1b", status["log_tail"])
        self.assertLessEqual(len(status["log_tail"].encode("utf-8")), 65536)
        self.assertLessEqual(len(status["log_tail"].splitlines()), 40)


class LibraryRetryTests(TfFacadeTestCase):
    """A retry after a stale unit must clear it first, and must never clear a
    running one.  The worker interpreter is fixed regardless of how the facade
    itself was started."""

    FAILED = ("LoadState=loaded\nActiveState=failed\nSubState=failed\n"
              "Result=exit-code\nExecMainStatus=1\n")
    SUCCEEDED = ("LoadState=loaded\nActiveState=active\nSubState=exited\n"
                 "Result=success\nExecMainStatus=0\n")
    RUNNING = "LoadState=loaded\nActiveState=active\nSubState=running\n"

    def _prepared(self, svc):
        self._write_marker(self._existing_mount(), tf_inspection.ANDROID_HANDOFF_RELATIVE, handoff())
        return svc.prepare(DISK)

    @staticmethod
    def _index(calls, *prefix):
        return [i for i, call in enumerate(calls) if tuple(call[:len(prefix)]) == prefix]

    def test_start_uses_the_fixed_system_python_when_frozen(self):
        svc = self._service()
        plan = self._prepared(svc)
        loader = "/var/home/armada/homebrew/services/PluginLoader"
        with patch.object(sys, "executable", loader), patch.object(sys, "frozen", True, create=True):
            svc.start(plan["token"], confirmed=True)
        argv = self.runner.launched()[0]
        tail = argv[argv.index("--") + 1:]
        self.assertEqual(tail[0], "/usr/bin/python3")
        self.assertTrue(tail[1].endswith("tf_worker.py"))

    def test_start_clears_a_failed_unit_before_the_launch(self):
        svc = self._service()
        plan = self._prepared(svc)
        self.runner.unit_show = self.FAILED
        svc.start(plan["token"], confirmed=True)
        calls = self.runner.calls
        resets = self._index(calls, "systemctl", "reset-failed")
        launches = self._index(calls, "systemd-run")
        self.assertEqual([calls[i] for i in resets],
                         [["systemctl", "reset-failed", service.TF_UNIT]])
        self.assertTrue(launches)
        self.assertLess(resets[0], launches[0])
        self.assertEqual(self._index(calls, "systemctl", "stop"), [])

    def test_start_stops_a_succeeded_unit_before_the_launch(self):
        svc = self._service()
        plan = self._prepared(svc)
        self.runner.unit_show = self.SUCCEEDED
        svc.start(plan["token"], confirmed=True)
        calls = self.runner.calls
        stops = self._index(calls, "systemctl", "stop")
        launches = self._index(calls, "systemd-run")
        self.assertEqual([calls[i] for i in stops], [["systemctl", "stop", service.TF_UNIT]])
        self.assertTrue(launches)
        self.assertLess(stops[0], launches[0])
        self.assertEqual(self._index(calls, "systemctl", "reset-failed"), [])

    def test_running_unit_is_never_cleared(self):
        svc = self._service()
        plan = self._prepared(svc)
        self.runner.unit_show = self.RUNNING
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "unit_running")
        self.assertEqual(self._index(self.runner.calls, "systemctl", "reset-failed"), [])
        self.assertEqual(self._index(self.runner.calls, "systemctl", "stop"), [])
        self.assertEqual(self.runner.launched(), [])

    def test_cleanup_failure_aborts_before_launch_and_preserves_state(self):
        svc = self._service()
        plan = self._prepared(svc)
        self.runner.unit_show = self.FAILED
        self.runner.cleanup_code = 1
        svc._status_path.parent.mkdir(parents=True, exist_ok=True)
        tf_state.atomic_write_json(svc._status_path,
                                   {"state": "failed", "phase": "registering", "detail": "old"})
        svc._log_path.write_bytes(b"old-log\n")
        old_status = svc._status_path.read_text(encoding="utf-8")
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "launch_failed")
        self.assertEqual(self.runner.launched(), [])
        # Cleanup runs before the plan/status are written, so a failed cleanup
        # leaves the previous plan/status/log untouched.
        self.assertFalse(svc._plan_path.exists())
        self.assertEqual(svc._status_path.read_text(encoding="utf-8"), old_status)
        self.assertEqual(svc._log_path.read_bytes(), b"old-log\n")


if __name__ == "__main__":
    unittest.main()
