export type Result<T> = { ok: true; data: T } | { ok: false; error: string; detail: string };

export type Layout = "fresh" | "occupied" | "toosmall" | "unavailable";

export interface InspectionInfo {
  mode: string;
  device: string;
  table_fingerprint: string;
  android_min_gib?: number;
  android_max_gib?: number;
  android_current_gib?: number;
  android_gib?: number;
  linux_gib?: number;
  partition_names?: string;
}

export interface PowerState {
  // null means the backend could not determine the power source.
  external_power: boolean | null;
  battery_percent: number | null;
}

export interface Inspection {
  layout: Layout;
  info: InspectionInfo;
  can_install: boolean;
  reason: string;
  detail?: string;
  power: PowerState;
}

export interface Plan {
  token: string;
  device: string;
  table_fingerprint: string;
  mode: string;
  android_gib: number;
  android_current_gib?: number;
  linux_gib?: number;
  partition_names?: string;
  power: PowerState;
}

export type InstallState = "idle" | "running" | "succeeded" | "failed";

export type InstallPhase = "preparing" | "copying" | "boot" | "working";

export interface Status {
  state: InstallState;
  phase: InstallPhase;
  log_tail: string;
}
