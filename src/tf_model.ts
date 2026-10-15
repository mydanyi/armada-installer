// Pure decision helpers for the TF-card library UI. The component calls these
// so the review/confirmation policy is testable without rendering React.

import type { TranslationKey } from "./i18n";
import type { TfCard, TfPlan, TfStatus } from "./tf_types";

const GIB = 1024 ** 3;

// Bytes -> GiB rounded to one decimal, or null when the backend omitted it.
export function bytesToGib(bytes: number | null | undefined): number | null {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return null;
  return Math.round((bytes / GIB) * 10) / 10;
}

// A card can only be acted on when the backend described it (never blocked).
export function isReviewableCard(card: TfCard | null | undefined): boolean {
  return !!card && (card.state === "pending" || card.state === "connected" || card.state === "recovery");
}

// Review prepares a fresh plan (read-only snapshot). It needs a usable card and
// a known idle status; a running conversion or a status that could not be read
// must never be re-prepared. A failure the user explicitly retried may proceed,
// and a backend-confirmed success may proceed only after the user explicitly
// returned to the card list (never automatically).
export function canReviewTfCard(
  card: TfCard | null,
  status: TfStatus | null = null,
  statusError = false,
  retryRequested = false,
  listRequested = false,
): boolean {
  if (!isReviewableCard(card)) return false;
  if (statusError || status === null) return false;
  if (status.state === "idle") return true;
  if (status.state === "succeeded" && listRequested) return true;
  if (status.state === "failed" && retryRequested) return true;
  return false;
}

// The mount-only retry ("Steam has not picked the library up yet") may start
// directly only when the freshly prepared plan is still a mount. A card that
// changed underneath us re-prepares as a format, which would erase the Linux
// reserve partition without the destructive confirmation — that must never run
// directly from this click.
export function retryConnectAllowed(plan: TfPlan | null | undefined): boolean {
  return !!plan && plan.mode === "mount";
}

export interface TfConfirmSpec {
  mode: "format" | "mount";
  // Only a format plan may use destructive wording. A mount plan preserves the
  // already-formatted data and must never read as an erase.
  destructive: boolean;
  titleKey: TranslationKey;
  descriptionKey: TranslationKey;
  eraseKey: TranslationKey | null;
  okKey: TranslationKey;
}

// The confirmation is chosen by the plan's mode, never by the card label, so a
// recovery card that resolves to a mount plan also cannot show erase wording.
export function confirmSpecFor(plan: TfPlan): TfConfirmSpec {
  if (plan.mode === "format") {
    return {
      mode: "format",
      destructive: true,
      titleKey: "tf.confirm.format.title",
      descriptionKey: "tf.confirm.format.description",
      eraseKey: "tf.confirm.format.erase",
      okKey: "tf.confirm.format.ok",
    };
  }
  return {
    mode: "mount",
    destructive: false,
    titleKey: "tf.confirm.mount.title",
    descriptionKey: "tf.confirm.mount.description",
    eraseKey: null,
    okKey: "tf.confirm.mount.ok",
  };
}

// A mounted partition is not a finished Steam registration until the backend
// reports library_registered. library_pending means "mounted, waiting for
// Steam" and must never be shown as success-complete.
export function libraryResult(status: TfStatus | null): "registered" | "pending" | null {
  if (!status || status.state !== "succeeded") return null;
  return status.library_registered ? "registered" : "pending";
}

export function libraryComplete(status: TfStatus | null): boolean {
  return libraryResult(status) === "registered";
}

// The success screen message key: registered vs waiting for Steam.
export function successBodyKey(status: TfStatus | null): TranslationKey {
  return libraryResult(status) === "registered" ? "tf.success.registered" : "tf.success.pending";
}
