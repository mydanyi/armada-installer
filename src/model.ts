import type { TranslationKey } from "./i18n";
import type { Inspection, InspectionInfo, Layout, Plan, PowerState, Status } from "./types";

export type PowerGate = "ok" | "battery" | "unknown";

// The backend refuses battery-only installs. A known battery-only reading
// blocks; an unknown reading only warns, because some devices expose no sysfs
// power supply and the backend reports that as null.
export function powerGate(power: PowerState): PowerGate {
  if (power.external_power === true) return "ok";
  if (power.external_power === false) return "battery";
  return "unknown";
}

export function sizeBounds(info: InspectionInfo): { min: number; max: number } {
  // Inverted bounds are reported as-is: the slider helper stays usable, but
  // isSizeValid refuses such a range so it can never enable an install.
  const max = typeof info.android_max_gib === "number" ? info.android_max_gib : 0;
  const min = typeof info.android_min_gib === "number" ? info.android_min_gib : 0;
  return { min, max };
}

// Slider start value: min(max(32, min), max).
export function defaultSizeFor(info: InspectionInfo): number {
  const { min, max } = sizeBounds(info);
  return Math.min(Math.max(32, min), max);
}

export function clampSize(value: number, info: InspectionInfo): number {
  const { min, max } = sizeBounds(info);
  if (!Number.isFinite(value)) return defaultSizeFor(info);
  return Math.min(Math.max(Math.round(value), min), max);
}

export function isSizeValid(value: number, info: InspectionInfo): boolean {
  const { min, max } = sizeBounds(info);
  return Number.isInteger(value) && Number.isInteger(min) && Number.isInteger(max)
    && max > 0 && min <= max && value >= min && value <= max;
}

// Armada space that stays usable after the chosen Android size is applied.
// A fresh detect reports the current Android capacity and no Linux figure; an
// occupied detect reports the Linux figure and keeps it unchanged.
export function remainingLinuxGib(info: InspectionInfo, androidGib: number): number | null {
  if (info.mode === "occupied") {
    return typeof info.linux_gib === "number" ? info.linux_gib : null;
  }
  if (info.mode === "fresh") {
    const current = info.android_current_gib;
    if (typeof current !== "number") return null;
    return Math.max(0, current - androidGib);
  }
  return null;
}

export function isInstallableLayout(layout: Layout): layout is "fresh" | "occupied" {
  return layout === "fresh" || layout === "occupied";
}

// A fresh install factory-resets Android; replacing Armada keeps its data.
export function erasesAndroidData(layout: Layout): boolean {
  return layout === "fresh";
}

export function installBlockCode(inspection: Inspection): string | null {
  if (!inspection.can_install) return inspection.reason || "installer_unavailable";
  if (!isInstallableLayout(inspection.layout)) return "installer_unavailable";
  return null;
}

// Review needs a known task status: only a known idle unit, or a retry the
// user explicitly requested after a real failure, may proceed. A null or
// unreadable status, a running/succeeded unit, and known battery-only power all
// block. Unknown power only warns, so it stays reviewable.
export function canReviewInstall(
  inspection: Inspection,
  status: Status | null = null,
  statusError = false,
  retryRequested = false,
): boolean {
  if (!inspection.can_install) return false;
  if (!isInstallableLayout(inspection.layout)) return false;
  if (powerGate(inspection.power) === "battery") return false;
  if (statusError || status === null) return false;
  if (status.state === "idle") return true;
  if (status.state === "failed" && retryRequested) return true;
  return false;
}

// Facts the final confirmation must show, taken from the returned plan rather
// than the stale inspection. partition_names is one raw CLI string.
export function confirmationFacts(plan: Plan): {
  layout: "fresh" | "occupied";
  device: string;
  androidGib: number;
  linuxGib: number | null;
  partitions: string | null;
  powerUnknown: boolean;
  erasesAndroid: boolean;
} {
  const layout: "fresh" | "occupied" = plan.mode === "occupied" ? "occupied" : "fresh";
  return {
    layout,
    device: plan.device,
    androidGib: plan.android_gib,
    linuxGib: typeof plan.linux_gib === "number" ? plan.linux_gib : null,
    partitions: typeof plan.partition_names === "string" && plan.partition_names.length > 0
      ? plan.partition_names
      : null,
    powerUnknown: powerGate(plan.power) !== "ok",
    erasesAndroid: erasesAndroidData(layout),
  };
}

export function confirmationSpec(layout: "fresh" | "occupied"): {
  titleKey: TranslationKey;
  descriptionKey: TranslationKey;
  eraseKey: TranslationKey;
} {
  if (layout === "occupied") {
    return {
      titleKey: "confirm.title.occupied",
      descriptionKey: "confirm.description.occupied",
      eraseKey: "confirm.eraseScope.occupied",
    };
  }
  return {
    titleKey: "confirm.title.fresh",
    descriptionKey: "confirm.description.fresh",
    eraseKey: "confirm.eraseScope.fresh",
  };
}
