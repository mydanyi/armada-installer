"""Contract tests for armada_installer.service.

Inspection and preparation are read-only; only a confirmed, single-use start
may hand the fixed install command to systemd.  Every test uses an injected
runner, so no real installer, partition tool, or systemd unit is ever run.

Error codes asserted: inspection_failed, toosmall, invalid_android_size, battery_only,
boot_disk_forbidden, boot_disk_unknown, confirmation_required, unknown_token, unit_running,
target_changed, launch_failed, status_unavailable, not_succeeded, poweroff_failed.
"""

import os, pathlib, subprocess, sys, tempfile, unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "py_modules"))

from armada_installer import service

INSTALLER = "/usr/libexec/armada/armada-installer"
UNIT = "armada-installer-plugin.service"
DEVICE, FINGERPRINT = "/dev/mmcblk0", "f" * 64
BOUNDS = {"fresh": {"android_min_gib": 8, "android_max_gib": 100, "android_current_gib": 128},
          "occupied": {"android_gib": 64, "linux_gib": 32, "partition_names": "/dev/mmcblk0p4 (SYSTEM)"},
          "toosmall": {"needed_gib": 104, "android_current_gib": 64}}


def unit_show(load="loaded", active="inactive", result="", code="", sub=""):
    sub = sub or {"active": "exited", "activating": "start", "reloading": "start",
                  "deactivating": "stop", "failed": "failed", "not-found": "dead"}.get(active, "dead")
    text = "LoadState={}\nActiveState={}\nSubState={}\n".format(load, active, sub)
    return text + ("Result={}\nExecMainStatus={}\n".format(result, code) if code != "" else "")


def detect(mode="fresh", **extra):
    return {"mode": mode, "device": DEVICE, "table_fingerprint": FINGERPRINT, **BOUNDS[mode], **extra}


class FakeRunner:
    """subprocess.run stand-in: answers systemctl and refuses stray installs."""

    def __init__(self, show=None, allow_launch=False, launch_code=0):
        self.calls, self.show = [], unit_show("not-found") if show is None else show
        self.allow_launch, self.launch_code = allow_launch, launch_code

    def __call__(self, argv, **kwargs):
        if not isinstance(argv, list) or not argv:
            raise AssertionError("blocking calls must pass an argv list: {!r}".format(argv))
        if kwargs.get("shell"):
            raise AssertionError("shell execution is never allowed")
        self.calls.append(list(argv))
        if argv[0] == "systemd-run" and not self.allow_launch:
            raise AssertionError("unexpected launch: {}".format(" ".join(argv)))
        if argv[0] not in ("systemctl", "systemd-run"):
            raise AssertionError("unexpected command: {}".format(argv[0]))
        if argv[0] == "systemd-run":
            return subprocess.CompletedProcess(argv, self.launch_code, "", "launch failed")
        return subprocess.CompletedProcess(argv, 0, self.show, "")

    def launched(self):
        return [call for call in self.calls if call[0] == "systemd-run"]


class UnloadRunner(FakeRunner):
    """FakeRunner whose reset-failed unloads the unit and then refuses it.

    Mirrors a failed transient unit: clearing the failure unloads it, after
    which any systemctl command naming the missing unit must fail.
    """

    def __init__(self, reset_code=0, **kwargs):
        super().__init__(**kwargs)
        self.reset_code, self.unloaded = reset_code, False

    def __call__(self, argv, **kwargs):
        if self.unloaded and argv[0] == "systemctl":
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 1, "", "Unit {} not loaded.".format(UNIT))
        result = super().__call__(argv, **kwargs)
        if argv[0] == "systemctl" and argv[1:3] == ["reset-failed", UNIT]:
            if self.reset_code == 0:
                self.unloaded = True
            return subprocess.CompletedProcess(argv, self.reset_code, "", "reset failed")
        return result


class BootRunner:
    """Answers only the boot-disk probes with fabricated output."""

    def __init__(self, source="/dev/sda2", ancestors=("8:0",), lsblk_code=0):
        self.calls, self.source, self.ancestors, self.lsblk_code = [], source, list(ancestors), lsblk_code

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] == "findmnt":
            return subprocess.CompletedProcess(argv, 0, self.source + "\n", "")
        if argv[0] == "lsblk":
            return subprocess.CompletedProcess(argv, self.lsblk_code,
                                               "\n".join(self.ancestors) + "\n", "lsblk failed")
        raise AssertionError("unexpected command: {}".format(argv[0]))


def properties(argv):
    found = {}
    for index, token in enumerate(argv):
        if token in ("--property", "-p") and index + 1 < len(argv):
            token = "--property=" + argv[index + 1]
        if token.startswith(("--property=", "-p=")):
            key, _, value = token.split("=", 1)[1].partition("=")
            found[key] = value
    return found


class ServiceTestCase(unittest.TestCase):
    def service(self, show=None, allow_launch=False, plan=None, detect_error=None, power=None,
                boot_error=None, patch_power=True, runner=None):
        self.runner = runner if runner is not None else FakeRunner(show=show, allow_launch=allow_launch)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = pathlib.Path(self.tmp.name) / "armada-installer-plugin.log"
        patches = [(patch.object(service, "_detect", return_value=plan or detect()) if detect_error is None
                    else patch.object(service, "_detect", side_effect=detect_error)),
                   (patch.object(service, "_check_boot_disk", return_value=None) if boot_error is None
                    else patch.object(service, "_check_boot_disk", side_effect=boot_error))]
        if patch_power:
            patches.insert(0, patch.object(service, "_power",
                                           return_value=power or {"external_power": True, "battery_percent": 80}))
        for started in patches:
            started.start()
            self.addCleanup(started.stop)
        return service.InstallerService(runner=self.runner, log_path=self.log_path,
                                        power_dir=pathlib.Path(self.tmp.name) / "power")


class InspectionTests(ServiceTestCase):
    def test_inspect_and_prepare_are_readonly(self):
        svc = self.service()
        seen = svc.inspect()
        self.assertEqual((seen["layout"], seen["can_install"], seen["power"]["external_power"]), ("fresh", True, True))
        plan = svc.prepare(android_gib=32)
        self.assertEqual((plan["device"], plan["table_fingerprint"], plan["mode"], plan["android_gib"]),
                         (DEVICE, FINGERPRINT, "fresh", 32))
        self.assertTrue(plan["token"])
        self.assertEqual(self.runner.launched(), [])
        self.assertEqual([call for call in self.runner.calls if INSTALLER in call], [])

    def test_plan_reports_power_and_fresh_capacity(self):
        svc = self.service()
        plan = svc.prepare(android_gib=32)
        self.assertEqual(plan["power"], {"external_power": True, "battery_percent": 80})
        self.assertEqual((plan["android_gib"], plan["android_current_gib"], plan["linux_gib"]),
                         (32, 128, 96))
        self.assertEqual(sorted(plan),
                         ["android_current_gib", "android_gib", "device", "linux_gib",
                          "mode", "partition_names", "power", "table_fingerprint", "token"])

    def test_inspection_failures_fail_closed(self):
        cases = (({"detect_error": service.InstallerError("inspection_failed", "no device")}, "inspection_failed"),
                 ({"plan": detect("toosmall")}, "toosmall"))
        for kwargs, reason in cases:
            with self.subTest(reason=reason):
                svc = self.service(**kwargs)
                seen = svc.inspect()
                self.assertFalse(seen["can_install"])
                self.assertEqual(seen["reason"], reason)
                with self.assertRaises(service.InstallerError) as caught:
                    svc.prepare(android_gib=32)
                self.assertEqual(caught.exception.code, reason)
                self.assertEqual(self.runner.launched(), [])

    def test_malformed_detect_cannot_generate_argv(self):
        for field in ("device", "table_fingerprint"):
            for bad in ("", None, 7):
                with self.subTest(field=field, bad=bad):
                    plan = detect()
                    plan[field] = bad
                    svc = self.service(plan=plan)
                    seen = svc.inspect()
                    self.assertFalse(seen["can_install"])
                    self.assertEqual(seen["reason"], "inspection_failed")
                    with self.assertRaises(service.InstallerError) as caught:
                        svc.prepare(android_gib=32)
                    self.assertEqual(caught.exception.code, "inspection_failed")
                    self.assertEqual(self.runner.launched(), [])

    def test_sizes_must_be_plain_integers_within_bounds(self):
        for plan, value in ((detect("fresh", android_min_gib=1), True), (detect(), "32"), (detect(), 32.0),
                            (detect(), 7), (detect(), 101), (detect(), None)):
            with self.subTest(value=value):
                svc = self.service(plan=plan)
                with self.assertRaises(service.InstallerError) as caught:
                    svc.prepare(android_gib=value)
                self.assertEqual(caught.exception.code, "invalid_android_size")
                self.assertEqual(self.runner.launched(), [])

    def test_battery_only_and_boot_disk_are_rejected(self):
        for kwargs in ({"power": {"external_power": False, "battery_percent": 80}},
                       {"boot_error": service.InstallerError("boot_disk_forbidden", DEVICE)}):
            with self.subTest(case=sorted(kwargs)[0]):
                svc = self.service(**kwargs)
                with self.assertRaises(service.InstallerError) as caught:
                    svc.prepare(android_gib=32)
                self.assertIn(caught.exception.code, ("battery_only", "boot_disk_forbidden"))
                self.assertEqual(self.runner.launched(), [])

    def test_prepare_denied_while_unit_runs(self):
        svc = self.service()
        self.runner.show = unit_show(active="activating")
        with self.assertRaises(service.InstallerError) as caught:
            svc.prepare(android_gib=32)
        self.assertEqual(caught.exception.code, "unit_running")
        self.assertEqual(self.runner.launched(), [])

    def test_missing_power_directory_is_unknown(self):
        svc = self.service(patch_power=False)
        seen = svc.inspect()
        self.assertEqual(seen["power"], {"external_power": None, "battery_percent": None})
        self.assertTrue(seen["can_install"])


class HelperTests(unittest.TestCase):
    def test_power_unknown_when_directory_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "not-there"
            self.assertEqual(service._power(missing), {"external_power": None, "battery_percent": None})

    def test_power_summarises_readable_supplies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            battery, mains = root / "BAT0", root / "AC0"
            battery.mkdir()
            (battery / "type").write_text("Battery\n", encoding="utf-8")
            (battery / "capacity").write_text("77\n", encoding="utf-8")
            mains.mkdir()
            (mains / "type").write_text("Mains\n", encoding="utf-8")
            (mains / "online").write_text("1\n", encoding="utf-8")
            self.assertEqual(service._power(root), {"external_power": True, "battery_percent": 77})


class BootDiskTests(unittest.TestCase):
    def check(self, source="/dev/sda2", ancestors=("8:0",), lsblk_code=0, rdev=(8, 0)):
        stat = SimpleNamespace(st_rdev=os.makedev(*rdev))
        with patch.object(pathlib.Path, "is_mount", return_value=False), \
                patch.object(pathlib.Path, "is_block_device", return_value=True), \
                patch.object(service.os, "stat", return_value=stat):
            return service._check_boot_disk(DEVICE, BootRunner(source, ancestors, lsblk_code))

    def test_running_boot_disk_is_rejected(self):
        with self.assertRaises(service.InstallerError) as caught:
            self.check(ancestors=("8:0",), rdev=(8, 0))
        self.assertEqual(caught.exception.code, "boot_disk_forbidden")

    def test_other_internal_disk_is_allowed(self):
        self.assertIsNone(self.check(ancestors=("8:0",), rdev=(8, 16)))

    def test_unreadable_ancestry_is_rejected(self):
        with self.assertRaises(service.InstallerError) as caught:
            self.check(lsblk_code=1)
        self.assertEqual(caught.exception.code, "boot_disk_unknown")


class StartTests(ServiceTestCase):
    def test_occupied_prepare_preserves_android(self):
        svc = self.service(plan=detect("occupied"), allow_launch=True)
        plan = svc.prepare()
        self.assertEqual((plan["android_gib"], plan["android_current_gib"], plan["linux_gib"]), (64, 64, 32))
        svc.start(plan["token"], confirmed=True)
        tail = self.runner.launched()[0]
        self.assertEqual(plan["mode"], "occupied")
        self.assertNotIn("--userdata-gib", tail)
        self.assertEqual(tail[tail.index("--expect-table") + 1], FINGERPRINT)

    def test_start_requires_confirmation_and_one_use_token(self):
        svc = self.service(allow_launch=True)
        plan = svc.prepare(android_gib=32)
        for value in (False, 1):
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

    def test_launch_failure_consumes_the_token(self):
        svc = self.service(allow_launch=True)
        self.runner.launch_code = 1
        plan = svc.prepare(android_gib=32)
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "launch_failed")
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "unknown_token")
        self.assertEqual(len(self.runner.launched()), 1)

    def test_start_rejects_changed_target(self):
        for field, value in (("table_fingerprint", "0" * 64), ("device", "/dev/nvme0n1")):
            with self.subTest(field=field):
                svc = self.service(allow_launch=True)
                plan = svc.prepare(android_gib=32)
                with patch.object(service, "_detect", return_value=detect("fresh", **{field: value})):
                    with self.assertRaises(service.InstallerError) as caught:
                        svc.start(plan["token"], confirmed=True)
                self.assertEqual(caught.exception.code, "target_changed")
                self.assertEqual(self.runner.launched(), [])

    def test_start_rejects_launch_while_unit_runs(self):
        svc = self.service(allow_launch=True)
        plan = svc.prepare(android_gib=32)
        self.runner.show = unit_show(active="activating")
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "unit_running")
        self.assertEqual(len(self.runner.launched()), 0)

    def test_consumed_token_is_unknown_even_while_running(self):
        svc = self.service(allow_launch=True)
        plan = svc.prepare(android_gib=32)
        svc.start(plan["token"], confirmed=True)
        self.runner.show = unit_show(active="activating")
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "unknown_token")
        self.assertEqual(len(self.runner.launched()), 1)

    def test_retry_from_failed_resets_instead_of_stopping(self):
        # A failed transient unit is cleared with one reset-failed; stopping it first
        # would unload the unit and make the retry fail.
        runner = UnloadRunner(show=unit_show(active="failed", result="exit-code", code="1"),
                              allow_launch=True)
        svc = self.service(runner=runner)
        plan = svc.prepare(android_gib=32)
        result = svc.start(plan["token"], confirmed=True)
        self.assertEqual(result, {"state": "running", "phase": "working", "log_tail": ""})
        self.assertEqual([call for call in runner.calls if call[:3] == ["systemctl", "reset-failed", UNIT]],
                         [["systemctl", "reset-failed", UNIT]])
        self.assertEqual([call for call in runner.calls if call[:3] == ["systemctl", "stop", UNIT]], [])
        self.assertEqual(len(runner.launched()), 1)

    def test_failed_reset_error_prevents_launch(self):
        runner = UnloadRunner(show=unit_show(active="failed", result="exit-code", code="1"),
                              allow_launch=True, reset_code=1)
        svc = self.service(runner=runner)
        plan = svc.prepare(android_gib=32)
        with self.assertRaises(service.InstallerError) as caught:
            svc.start(plan["token"], confirmed=True)
        self.assertEqual(caught.exception.code, "launch_failed")
        self.assertEqual([call for call in runner.calls if call[:3] == ["systemctl", "stop", UNIT]], [])
        self.assertEqual(runner.launched(), [])

    def test_fresh_launch_uses_fixed_unit_and_argv(self):
        svc = self.service(allow_launch=True)
        result = svc.start(svc.prepare(android_gib=32)["token"], confirmed=True)
        self.assertEqual(result, {"state": "running", "phase": "working", "log_tail": ""})
        argv = self.runner.launched()[0]
        props = properties(argv)
        self.assertIn(UNIT, argv)
        self.assertEqual((props.get("Type"), props.get("RemainAfterExit")), ("exec", "yes"))
        self.assertEqual((props.get("StandardOutput"), props.get("StandardError")),
                         ("append:{}".format(self.log_path), "append:{}".format(self.log_path)))
        tail = argv[argv.index(INSTALLER):]
        self.assertEqual(tail[:4], [INSTALLER, "--device", DEVICE, "install"])
        self.assertIn("--assume-yes", tail)
        self.assertEqual((tail[tail.index("--expect-table") + 1], tail[tail.index("--userdata-gib") + 1]),
                         (FINGERPRINT, "32"))


class StatusTests(ServiceTestCase):
    def test_status_maps_systemd_state(self):
        cases = ((unit_show("not-found"), "idle"),
                 (unit_show(active="activating"), "running"),
                 (unit_show(active="active", sub="running", result="success", code="0"), "running"),
                 (unit_show(active="active", result="success", code="0"), "succeeded"),
                 (unit_show(active="active", result="exit-code", code="1"), "failed"),
                 (unit_show(active="failed", result="exit-code", code="1"), "failed"),
                 (unit_show(active="inactive", result="exit-code", code="1"), "failed"),
                 (unit_show(load="not-found", active="inactive"), "idle"),
                 ("LoadState=not-found\nActiveState=active", "idle"))
        for output, expected in cases:
            with self.subTest(output=output):
                self.assertEqual(self.service(show=output).status()["state"], expected)

    def test_ambiguous_status_is_unavailable(self):
        with self.assertRaises(service.InstallerError) as caught:
            self.service(show="").status()
        self.assertEqual(caught.exception.code, "status_unavailable")

    def test_status_tail_is_bounded_and_reports_no_percentages(self):
        svc = self.service(show=unit_show(active="failed", result="exit-code", code="1"))
        self.log_path.write_bytes(("\n".join("y" * 500 for _ in range(200)) + "\n").encode("utf-8"))
        status = svc.status()
        self.assertGreater(len(status["log_tail"]), 0)
        self.assertLessEqual(len(status["log_tail"].encode("utf-8")), 65536)
        self.assertLessEqual(len(status["log_tail"].splitlines()), 40)
        self.assertNotIn("percent", status)

    def test_poweroff_requires_a_successful_install(self):
        cases = ((unit_show("not-found"), "not_succeeded"),
                 (unit_show(active="activating"), "not_succeeded"),
                 (unit_show(active="active", sub="running", result="success", code="0"), "not_succeeded"),
                 (unit_show(active="failed", result="exit-code", code="1"), "not_succeeded"),
                 ("", "status_unavailable"))
        for output, code in cases:
            with self.subTest(output=output):
                svc = self.service(show=output)
                with self.assertRaises(service.InstallerError) as caught:
                    svc.poweroff()
                self.assertEqual(caught.exception.code, code)
                self.assertEqual([call for call in self.runner.calls if call[:2] == ["systemctl", "poweroff"]], [])
        svc = self.service(show=unit_show(active="active", result="success", code="0"))
        svc.poweroff()
        self.assertTrue([call for call in self.runner.calls if call[:2] == ["systemctl", "poweroff"]])


if __name__ == "__main__":
    unittest.main()
