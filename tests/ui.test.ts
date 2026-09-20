import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canReviewInstall,
  clampSize,
  confirmationFacts,
  confirmationSpec,
  defaultSizeFor,
  erasesAndroidData,
  installBlockCode,
  isSizeValid,
  powerGate,
  remainingLinuxGib,
  sizeBounds,
} from "../src/model.ts";
import {
  detectLocaleFromEnvironment,
  localeFromLanguage,
  localeStrings,
  resolveLocale,
  translate,
  translateErrorCode,
} from "../src/i18n.ts";

const external = { external_power: true, battery_percent: 100 };
const battery = { external_power: false, battery_percent: 42 };
const unknownPower = { external_power: null, battery_percent: null };

const idleStatus = { state: "idle", phase: "working", log_tail: "" } as const;
const runningStatus = { state: "running", phase: "working", log_tail: "" } as const;
const succeededStatus = { state: "succeeded", phase: "working", log_tail: "" } as const;
const failedStatus = { state: "failed", phase: "working", log_tail: "" } as const;

// The real detect shapes: fresh carries bounds + the current Android capacity
// and no linux_gib; occupied carries android_gib + linux_gib (no "linux" mode).
const freshInfo = {
  mode: "fresh",
  device: "/dev/mmcblk0",
  table_fingerprint: "abc123",
  android_min_gib: 8,
  android_max_gib: 100,
  android_current_gib: 128,
};

function inspection(overrides = {}) {
  return {
    layout: "fresh",
    info: freshInfo,
    can_install: true,
    reason: "",
    power: external,
    ...overrides,
  };
}

test("a fresh install erases Android, replacing Armada does not", () => {
  assert.equal(erasesAndroidData("fresh"), true);
  assert.equal(erasesAndroidData("occupied"), false);
  assert.equal(erasesAndroidData("toosmall"), false);

  const fresh = confirmationSpec("fresh");
  const occupied = confirmationSpec("occupied");
  assert.notEqual(fresh.titleKey, occupied.titleKey);
  assert.notEqual(fresh.descriptionKey, occupied.descriptionKey);
  assert.notEqual(fresh.eraseKey, occupied.eraseKey);
  assert.match(translate("en", fresh.eraseKey), /Android user data/);
  assert.match(translate("en", occupied.eraseKey), /target partitions/);
});

test("the Android size slider is bounded and starts at min(max(32, min), max)", () => {
  const info = { android_min_gib: 16, android_max_gib: 512 };
  assert.deepEqual(sizeBounds(info), { min: 16, max: 512 });
  assert.equal(defaultSizeFor(info), 32);
  assert.equal(defaultSizeFor({ android_min_gib: 64, android_max_gib: 512 }), 64);
  assert.equal(defaultSizeFor({ android_min_gib: 16, android_max_gib: 24 }), 24);
  assert.equal(defaultSizeFor({ android_max_gib: 512 }), 32);

  assert.equal(clampSize(5, info), 16);
  assert.equal(clampSize(4096, info), 512);
  assert.equal(clampSize(64, info), 64);
  assert.equal(clampSize(Number.NaN, info), 32);

  assert.equal(isSizeValid(16, info), true);
  assert.equal(isSizeValid(512, info), true);
  assert.equal(isSizeValid(15, info), false);
  assert.equal(isSizeValid(513, info), false);
  assert.equal(isSizeValid(Number.NaN, info), false);
  assert.equal(isSizeValid(32.5, info), false);
  assert.equal(isSizeValid(0, { android_max_gib: 0 }), false);

  // Inverted bounds are not coerced into a valid range.
  const inverted = { android_min_gib: 512, android_max_gib: 16 };
  assert.deepEqual(sizeBounds(inverted), { min: 512, max: 16 });
  assert.equal(isSizeValid(512, inverted), false);
  assert.equal(isSizeValid(16, inverted), false);
});

test("remaining space follows the chosen size using the real detect shapes", () => {
  // fresh: no linux_gib; remaining is the current Android capacity minus the choice.
  assert.equal(remainingLinuxGib({ mode: "fresh", android_current_gib: 128 }, 64), 64);
  assert.equal(remainingLinuxGib({ mode: "fresh", android_current_gib: 128 }, 200), 0);
  assert.equal(remainingLinuxGib({ mode: "fresh" }, 64), null);
  // occupied: android_gib + linux_gib, and the selection never moves it.
  assert.equal(remainingLinuxGib({ mode: "occupied", android_gib: 64, linux_gib: 32 }, 64), 32);
  assert.equal(remainingLinuxGib({ mode: "occupied", android_gib: 64 }, 64), null);
  // neither a fresh nor occupied layout reports a number.
  assert.equal(remainingLinuxGib({ mode: "toosmall" }, 64), null);
});

test("only a known idle status or an explicit failed retry unlocks review", () => {
  assert.equal(powerGate(external), "ok");
  assert.equal(powerGate(battery), "battery");
  assert.equal(powerGate(unknownPower), "unknown");

  // No successful status read yet: null and status errors both block.
  assert.equal(canReviewInstall(inspection()), false);
  assert.equal(canReviewInstall(inspection(), null), false);
  assert.equal(canReviewInstall(inspection(), idleStatus, true), false);

  // Known idle permits review; running, succeeded and an unrequested failure do not.
  assert.equal(canReviewInstall(inspection(), idleStatus), true);
  assert.equal(canReviewInstall(inspection(), runningStatus), false);
  assert.equal(canReviewInstall(inspection(), succeededStatus), false);
  assert.equal(canReviewInstall(inspection(), failedStatus), false);
  // A failure the user explicitly retried permits review, idle still does too.
  assert.equal(canReviewInstall(inspection(), failedStatus, false, true), true);
  assert.equal(canReviewInstall(inspection(), idleStatus, false, true), true);

  // Known battery-only blocks under any status; occupied is the same.
  assert.equal(canReviewInstall(inspection({ power: battery }), idleStatus), false);
  assert.equal(canReviewInstall(inspection({ layout: "occupied", power: battery }), idleStatus), false);

  // Unknown power only warns; it never permanently blocks review.
  assert.equal(canReviewInstall(inspection({ power: unknownPower }), idleStatus), true);
  assert.equal(canReviewInstall(inspection({ layout: "occupied", power: unknownPower }), idleStatus), true);
  assert.equal(canReviewInstall(inspection({ layout: "occupied" }), idleStatus), true);
  assert.match(translate("en", "confirm.powerWarning"), /charger/i);
  assert.notEqual(translate("zh", "confirm.powerWarning"), translate("en", "confirm.powerWarning"));

  // installBlockCode stays about the layout/backend, not power or status.
  assert.equal(installBlockCode(inspection({ power: battery })), null);
  assert.equal(installBlockCode(inspection({ power: unknownPower })), null);
  assert.equal(installBlockCode(inspection({ layout: "occupied" })), null);
});

test("non-installable layouts expose no install control", () => {
  const toosmall = inspection({ layout: "toosmall", can_install: false, reason: "toosmall" });
  const forbidden = inspection({ layout: "unavailable", can_install: false, reason: "boot_disk_forbidden" });
  const unknownDisk = inspection({ layout: "unavailable", can_install: false, reason: "boot_disk_unknown" });
  for (const blocked of [toosmall, forbidden, unknownDisk]) {
    assert.equal(canReviewInstall(blocked, idleStatus), false);
  }
  assert.equal(installBlockCode(toosmall), "toosmall");
  assert.equal(installBlockCode(forbidden), "boot_disk_forbidden");
  assert.equal(installBlockCode(unknownDisk), "boot_disk_unknown");
  assert.equal(installBlockCode(inspection({ can_install: false, reason: "" })), "installer_unavailable");
});

test("the confirmation reads the returned plan, not the stale inspection", () => {
  const plan = {
    token: "token",
    device: "/dev/mmcblk0",
    table_fingerprint: "abc123",
    mode: "fresh",
    android_gib: 32,
    android_current_gib: 128,
    linux_gib: 96,
    partition_names: "/dev/mmcblk0p4 (SYSTEM)",
    power: unknownPower,
  };
  const facts = confirmationFacts(plan);
  assert.equal(facts.layout, "fresh");
  assert.equal(facts.device, "/dev/mmcblk0");
  assert.equal(facts.androidGib, 32);
  assert.equal(facts.linuxGib, 96);
  // partition_names is one raw CLI string, never an array joined with commas.
  assert.equal(facts.partitions, "/dev/mmcblk0p4 (SYSTEM)");
  assert.equal(facts.powerUnknown, true);
  assert.equal(facts.erasesAndroid, true);

  const occupied = confirmationFacts({
    ...plan, mode: "occupied", android_gib: 64, linux_gib: 32, partition_names: "",
  });
  assert.equal(occupied.layout, "occupied");
  assert.equal(occupied.partitions, null);
  assert.equal(occupied.erasesAndroid, false);
});

test("language detection falls back to English outside the Chinese locales", () => {
  assert.equal(localeFromLanguage("zh_CN"), "zh");
  assert.equal(localeFromLanguage("zh-CN"), "zh");
  assert.equal(localeFromLanguage("zh-Hans"), "zh");
  assert.equal(localeFromLanguage("zh-SG"), "zh");
  assert.equal(localeFromLanguage("schinese"), "zh");
  assert.equal(localeFromLanguage("en-US"), "en");
  assert.equal(localeFromLanguage("pt-BR"), "en");
  assert.equal(localeFromLanguage(undefined), null);
  assert.equal(localeFromLanguage(null), null);
  assert.equal(localeFromLanguage("   "), null);

  assert.equal(resolveLocale({}), "en");
  assert.equal(resolveLocale({ steamLanguage: "schinese" }), "zh");
  assert.equal(resolveLocale({ steamLanguage: "english" }), "en");
  assert.equal(resolveLocale({ steamLanguage: "schinese", browserLanguages: ["en-US"] }), "zh");
  assert.equal(resolveLocale({ deckyLocales: ["zh-CN"] }), "zh");
  assert.equal(resolveLocale({ browserLanguages: ["zh-CN"] }), "zh");
  assert.equal(resolveLocale({ browserLanguages: ["fr-FR"] }), "en");
});

test("the environment can be read without window or Steam globals", () => {
  const locale = detectLocaleFromEnvironment();
  assert.ok(locale === "en" || locale === "zh", locale);
});

test("both dictionaries stay complete and keep the same placeholders", () => {
  const english = localeStrings.en;
  const chinese = localeStrings.zh;
  assert.deepEqual(Object.keys(chinese), Object.keys(english));

  const placeholders = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
  for (const key of Object.keys(english) as (keyof typeof english)[]) {
    assert.ok(english[key].length > 0, key);
    assert.ok(chinese[key].length > 0, key);
    assert.deepEqual(placeholders(chinese[key]), placeholders(english[key]), key);
  }
});

test("Chinese strings interpolate exactly like the English ones", () => {
  const english = translate("en", "fresh.remainingValue", { size: 64 });
  const chinese = translate("zh", "fresh.remainingValue", { size: 64 });
  assert.ok(english.includes("64"), english);
  assert.ok(chinese.includes("64"), chinese);
  assert.ok(!english.includes("{size}") && !chinese.includes("{size}"));
  assert.notEqual(english, chinese);

  const target = translate("zh", "confirm.planTarget", { device: "/dev/mmcblk0" });
  assert.ok(target.includes("/dev/mmcblk0"), target);
  assert.notEqual(target, translate("en", "confirm.planTarget", { device: "/dev/mmcblk0" }));
});

test("known error codes localize and unknown codes map to a generic error", () => {
  assert.equal(translateErrorCode("en", "battery_only"), translate("en", "error.battery_only"));
  assert.equal(translateErrorCode("zh", "battery_only"), translate("zh", "error.battery_only"));
  assert.notEqual(translateErrorCode("zh", "battery_only"), translateErrorCode("en", "battery_only"));
  assert.equal(translateErrorCode("zh", "made_up_code"), translate("zh", "error.internal_error"));
  assert.equal(translateErrorCode("en", "made_up_code"), translate("en", "error.internal_error"));
  assert.equal(translateErrorCode("en", ""), "");
});
