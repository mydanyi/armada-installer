import assert from "node:assert/strict";
import { test } from "node:test";
import {
  bytesToGib,
  canReviewTfCard,
  confirmSpecFor,
  isReviewableCard,
  libraryComplete,
  libraryResult,
  retryConnectAllowed,
  successBodyKey,
} from "../src/tf_model.ts";
import { tfErrorText, tfPhaseKeys, tfStateKeys } from "../src/tf_i18n.ts";
import { localeStrings, translate } from "../src/i18n.ts";
import type { TfCard, TfPlan, TfStatus } from "../src/tf_types.ts";

const idle: TfStatus = {
  state: "idle", phase: "", detail: "", log_tail: "",
  library_requested: false, library_registered: false, library_pending: false,
};
const running: TfStatus = { ...idle, state: "running", phase: "formatting" };
const failed: TfStatus = { ...idle, state: "failed" };
const succeededRegistered: TfStatus = {
  ...idle, state: "succeeded", phase: "registering", library_registered: true,
};
const succeededPending: TfStatus = {
  ...idle, state: "succeeded", phase: "registering", library_pending: true,
};

const pendingCard: TfCard = {
  device: "/dev/mmcblk0", size_bytes: 64 * 1024 ** 3, card_cid: "1b2c3d4e5f60718293a4b5c6d7e8f901",
  state: "pending", reason: "", detail: "", reserve_device: "/dev/mmcblk0p1",
  android_bytes: null, linux_bytes: null, mountpoint: null, library_path: null,
};
const connectedCard: TfCard = {
  ...pendingCard, state: "connected", mountpoint: "/run/armada-installer-tf/mnt",
  library_path: "/run/armada-installer-tf/mnt/SteamLibrary",
};
const blockedCard: TfCard = {
  ...pendingCard, state: "blocked", reason: "boot_disk_forbidden", detail: "boot source",
};
const recoveryCard: TfCard = { ...pendingCard, state: "recovery" };

function plan(overrides: Partial<TfPlan> = {}): TfPlan {
  return {
    token: "t", mode: "format", device: "/dev/mmcblk0", reserve_device: "/dev/mmcblk0p1",
    card_cid: pendingCard.card_cid, android_bytes: 32 * 1024 ** 3, linux_bytes: 32 * 1024 ** 3,
    recovery: false, preserves: { android_p2: {}, android_p3: {} }, erase_scope: "reserve_only_format",
    ...overrides,
  };
}

test("a blocked card and any running conversion cannot be reviewed", () => {
  assert.equal(isReviewableCard(pendingCard), true);
  assert.equal(isReviewableCard(connectedCard), true);
  assert.equal(isReviewableCard(recoveryCard), true);
  assert.equal(isReviewableCard(blockedCard), false);
  assert.equal(isReviewableCard(null), false);

  // A known idle status permits review; a running conversion never does.
  assert.equal(canReviewTfCard(pendingCard, idle), true);
  assert.equal(canReviewTfCard(blockedCard, idle), false);
  assert.equal(canReviewTfCard(pendingCard, running), false);
  assert.equal(canReviewTfCard(connectedCard, running), false);
  // No successful status read and an unrequested failure both block.
  assert.equal(canReviewTfCard(pendingCard, null), false);
  assert.equal(canReviewTfCard(pendingCard, null, true), false);
  assert.equal(canReviewTfCard(pendingCard, failed), false);
  // An explicit failed retry unlocks review again.
  assert.equal(canReviewTfCard(pendingCard, failed, false, true), true);
  assert.equal(canReviewTfCard(blockedCard, failed, false, true), false);
});

test("a succeeded status unlocks review only after the user returns to the card list", () => {
  // An old success must not be re-prepared automatically.
  assert.equal(canReviewTfCard(connectedCard, succeededRegistered), false);
  assert.equal(canReviewTfCard(connectedCard, succeededPending), false);
  // Only the explicit "back to cards" click (listRequested) re-opens review,
  // and it still requires a reviewable card.
  assert.equal(canReviewTfCard(connectedCard, succeededRegistered, false, false, true), true);
  assert.equal(canReviewTfCard(connectedCard, succeededPending, false, false, true), true);
  assert.equal(canReviewTfCard(blockedCard, succeededRegistered, false, false, true), false);
  // A running conversion stays blocked even if the list was requested.
  assert.equal(canReviewTfCard(connectedCard, running, false, false, true), false);
  // A status that could not be read stays blocked too.
  assert.equal(canReviewTfCard(connectedCard, null, false, false, true), false);
});

test("the mount-only retry never starts a fresh format plan directly", () => {
  // A fresh mount plan proceeds; a format plan must never enter a direct start
  // (that path has no destructive confirmation).
  assert.equal(retryConnectAllowed(plan({ mode: "mount", erase_scope: "none" })), true);
  assert.equal(retryConnectAllowed(plan({ mode: "format" })), false);
  assert.equal(retryConnectAllowed(null), false);
  assert.equal(retryConnectAllowed(undefined), false);
  // A recovery card that resolves to a mount is fine, but a recovery that
  // resolves to a format stays blocked from the direct retry path.
  assert.equal(retryConnectAllowed(plan({ mode: "mount", recovery: true })), true);
  assert.equal(retryConnectAllowed(plan({ mode: "format", recovery: true })), false);
});

test("only a format plan uses the destructive erase confirmation", () => {
  const format = confirmSpecFor(plan({ mode: "format" }));
  assert.equal(format.mode, "format");
  assert.equal(format.destructive, true);
  assert.ok(format.eraseKey);
  assert.equal(translate("en", format.eraseKey), translate("en", "tf.confirm.format.erase"));
  assert.match(translate("en", format.eraseKey), /reserve/i);

  const mount = confirmSpecFor(plan({ mode: "mount", erase_scope: "none" }));
  assert.equal(mount.mode, "mount");
  assert.equal(mount.destructive, false);
  // A mount confirmation carries no erase key at all.
  assert.equal(mount.eraseKey, null);

  // And neither language leaks erase wording into the mount confirmation.
  for (const locale of ["en", "zh"] as const) {
    const text = [
      translate(locale, mount.titleKey),
      translate(locale, mount.descriptionKey),
      translate(locale, mount.okKey),
    ].join(" ");
    assert.ok(!/erase|擦除|清除|抹除/i.test(text), `${locale} mount confirmation: ${text}`);
  }

  // A recovery that resolves to a mount plan is likewise non-destructive.
  assert.equal(confirmSpecFor(plan({ mode: "mount", recovery: true })).destructive, false);
});

test("a pending Steam library is not a completed registration", () => {
  assert.equal(libraryResult(succeededRegistered), "registered");
  assert.equal(libraryComplete(succeededRegistered), true);
  assert.equal(successBodyKey(succeededRegistered), "tf.success.registered");

  // Mounted but Steam has not picked it up yet: pending, never complete.
  assert.equal(libraryResult(succeededPending), "pending");
  assert.equal(libraryComplete(succeededPending), false);
  assert.equal(successBodyKey(succeededPending), "tf.success.pending");

  // Only a succeeded backend state counts at all.
  assert.equal(libraryResult(running), null);
  assert.equal(libraryComplete(failed), false);
});

test("sizes render as GiB and unknown values stay unknown", () => {
  assert.equal(bytesToGib(64 * 1024 ** 3), 64);
  assert.equal(bytesToGib(0), 0);
  assert.equal(bytesToGib(null), null);
  assert.equal(bytesToGib(undefined), null);
  assert.equal(bytesToGib(-1), null);
  assert.equal(bytesToGib(Number.NaN), null);
});

test("TF codes localise per key and unknown codes fall back, never the raw code", () => {
  assert.equal(tfErrorText("en", "handoff_missing"), translate("en", "error.handoff_missing"));
  assert.equal(tfErrorText("zh", "handoff_missing"), translate("zh", "error.handoff_missing"));
  assert.notEqual(tfErrorText("zh", "card_busy"), tfErrorText("en", "card_busy"));
  // An unknown code never surfaces the code itself, only the generic message.
  assert.equal(tfErrorText("en", "made_up_code"), translate("en", "error.internal_error"));
  assert.equal(tfErrorText("zh", "made_up_code"), translate("zh", "error.internal_error"));
  assert.equal(tfErrorText("en", ""), "");
});

test("every TF state, phase and error key exists in both locales with matching placeholders", () => {
  const english = localeStrings.en;
  const chinese = localeStrings.zh;
  const keys = [
    ...Object.values(tfStateKeys),
    ...Object.values(tfPhaseKeys),
    "tf.confirm.format.title", "tf.confirm.format.description", "tf.confirm.format.erase",
    "tf.confirm.format.ok", "tf.confirm.mount.title", "tf.confirm.mount.description",
    "tf.confirm.mount.ok", "tf.success.pending", "tf.success.registered",
    "tf.success.retryConnect", "tf.success.backToCards",
    "error.handoff_missing", "error.card_busy", "error.unknown_device",
  ] as const;

  const placeholders = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  for (const key of keys) {
    assert.ok(english[key].length > 0, `en ${key}`);
    assert.ok(chinese[key].length > 0, `zh ${key}`);
    assert.deepEqual(placeholders(chinese[key]), placeholders(english[key]), key);
  }
});
