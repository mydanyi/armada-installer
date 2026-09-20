export const en = {
  "app.title": "Armada Installer",
  "common.loading": "Loading…",
  "common.refresh": "Refresh",
  "common.review": "Review",
  "common.confirm": "Erase and install",
  "common.cancel": "Cancel",
  "common.details": "Details",
  "language.label": "Language",
  "details.title": "Technical details",
  "inspection.hint": "Start from the SD card. Only the internal disk is modified.",
  "inspection.targetDisk": "Target disk",
  "inspection.mode": "Mode",
  "inspection.layout": "Layout",
  "inspection.table": "Partition table",
  "inspection.power": "Power",
  "inspection.powerExternal": "Charger connected",
  "inspection.powerBattery": "Battery only ({percent}%)",
  "inspection.powerBatteryUnknown": "Battery only",
  "inspection.powerUnknown": "Unknown — connect the charger",
  "inspection.layoutFresh": "Android only",
  "inspection.layoutOccupied": "Space already allocated alongside Android",
  "inspection.layoutToosmall": "Disk too small",
  "inspection.layoutUnavailable": "Unavailable",
  "fresh.title": "Allocate space for Armada",
  "fresh.warning": "Installing factory-resets Android and erases ALL Android user data on this device.",
  "fresh.size": "Android size",
  "fresh.remainingLabel": "Armada space after install",
  "fresh.remainingValue": "about {size} GiB (includes boot partitions, approximate)",
  "fresh.remainingUnknown": "unknown",
  "occupied.title": "Replace the existing installation",
  "occupied.warning": "The partitions listed in the confirmation are erased. Android and its data are kept.",
  "occupied.androidLabel": "Android kept",
  "occupied.androidValue": "{size} GiB",
  "occupied.linuxLabel": "Armada space",
  "occupied.linuxValue": "about {size} GiB (includes boot partitions)",
  "occupied.unknown": "unknown",
  "block.batteryOnly": "Connect the charger. Installing on battery only is blocked.",
  "block.powerUnknown": "Connect the charger, then refresh to confirm the power source.",
  "confirm.title.fresh": "Erase Android data and install?",
  "confirm.title.occupied": "Erase the target partitions and install?",
  "confirm.description.fresh": "Android is factory-reset and ALL Android user data on this device is erased. This cannot be undone.",
  "confirm.description.occupied": "The target partitions listed below are erased. Android and its data are preserved.",
  "confirm.eraseScope.fresh": "Erases: all Android user data and the Armada target partitions.",
  "confirm.eraseScope.occupied": "Erases: the target partitions listed below only.",
  "confirm.planTarget": "Target disk: {device}.",
  "confirm.planAndroid": "Android size: {size} GiB.",
  "confirm.planAndroidExisting": "Android size kept: {size} GiB.",
  "confirm.planLinux": "Armada size: about {size} GiB.",
  "confirm.planPartitions": "Partitions: {names}.",
  "confirm.keepCharger": "Keep the charger attached. Do not power off or remove the SD card while installing.",
  "confirm.powerWarning": "The power source is unknown. Connect the charger before continuing.",
  "running.title": "Installing",
  "running.phase.preparing": "Preparing partitions",
  "running.phase.copying": "Writing the image",
  "running.phase.boot": "Finalizing the boot loader",
  "running.phase.working": "Working",
  "running.keepPowered": "Do not power off the device until this finishes.",
  "running.panelClose": "Closing this panel does not cancel the installation.",
  "running.pollUnavailable": "The installation status cannot be read right now. Keep the device powered on.",
  "success.title": "Installation finished",
  "success.body": "Power off, remove the SD card, then boot from the internal disk.",
  "success.powerOff": "Power off",
  "success.powerOffHint": "The device powers off only after you press this button.",
  "failure.title": "Installation failed",
  "failure.body": "The installation did not finish. In the ABL boot menu, select the SD card as the boot source, then retry the installation. To abandon the internal installation, select UNINSTALL CFW in ABL.",
  "failure.retry": "Refresh and retry",
  "failure.errorLabel": "Error",
  "error.installer_unavailable": "The installer backend is not available.",
  "error.inspection_failed": "The disk layout could not be inspected.",
  "error.toosmall": "The disk is too small for this installation.",
  "error.invalid_android_size": "That Android size is not allowed.",
  "error.battery_only": "Connect the charger. Installing on battery only is blocked.",
  "error.boot_disk_forbidden": "The boot disk cannot be used as the install target.",
  "error.boot_disk_unknown": "The boot disk could not be identified.",
  "error.confirmation_required": "The confirmation step is required before installing.",
  "error.unknown_token": "This plan has expired. Review again.",
  "error.unit_running": "Another installation is already running.",
  "error.target_changed": "The disk changed since review. Review again.",
  "error.status_unavailable": "The installation status is unavailable.",
  "error.not_succeeded": "The installation has not finished successfully.",
  "error.launch_failed": "The installer could not be started.",
  "error.poweroff_failed": "The device could not be powered off.",
  "error.internal_error": "An internal error occurred.",
  "error.unknown": "Unexpected error.",
} as const;

export type TranslationKey = keyof typeof en;

export const zh: Record<TranslationKey, string> = {
  "app.title": "Armada 安装器",
  "common.loading": "正在加载…",
  "common.refresh": "刷新",
  "common.review": "检查",
  "common.confirm": "擦除并安装",
  "common.cancel": "取消",
  "common.details": "详情",
  "language.label": "语言",
  "details.title": "技术详情",
  "inspection.hint": "请从 SD 卡启动，本操作仅修改内置磁盘。",
  "inspection.targetDisk": "目标磁盘",
  "inspection.mode": "模式",
  "inspection.layout": "分区布局",
  "inspection.table": "分区表",
  "inspection.power": "电源",
  "inspection.powerExternal": "已连接充电器",
  "inspection.powerBattery": "仅电池供电（{percent}%）",
  "inspection.powerBatteryUnknown": "仅电池供电",
  "inspection.powerUnknown": "未知 — 请先连接充电器",
  "inspection.layoutFresh": "仅有 Android",
  "inspection.layoutOccupied": "已有 Android 之外的分区",
  "inspection.layoutToosmall": "磁盘空间不足",
  "inspection.layoutUnavailable": "不可用",
  "fresh.title": "为 Armada 分配空间",
  "fresh.warning": "安装会恢复 Android 出厂设置，并清除本机全部 Android 用户数据。",
  "fresh.size": "Android 容量",
  "fresh.remainingLabel": "安装后 Armada 空间",
  "fresh.remainingValue": "约 {size} GiB（包含启动分区，为近似值）",
  "fresh.remainingUnknown": "未知",
  "occupied.title": "替换现有系统",
  "occupied.warning": "确认对话框中列出的分区将被擦除，Android 及其数据会保留。",
  "occupied.androidLabel": "保留的 Android",
  "occupied.androidValue": "{size} GiB",
  "occupied.linuxLabel": "Armada 空间",
  "occupied.linuxValue": "约 {size} GiB（包含启动分区）",
  "occupied.unknown": "未知",
  "block.batteryOnly": "请连接充电器，仅用电池时禁止安装。",
  "block.powerUnknown": "请连接充电器，然后刷新以确认供电源。",
  "confirm.title.fresh": "清除 Android 数据并安装？",
  "confirm.title.occupied": "擦除目标分区并安装？",
  "confirm.description.fresh": "Android 将恢复出厂设置，本机全部 Android 用户数据都会被清除，且无法恢复。",
  "confirm.description.occupied": "下方列出的目标分区会被擦除，Android 及其数据会保留。",
  "confirm.eraseScope.fresh": "将擦除：全部 Android 用户数据与 Armada 目标分区。",
  "confirm.eraseScope.occupied": "仅擦除：下方列出的目标分区。",
  "confirm.planTarget": "目标磁盘：{device}。",
  "confirm.planAndroid": "Android 容量：{size} GiB。",
  "confirm.planAndroidExisting": "保留的 Android 容量：{size} GiB。",
  "confirm.planLinux": "Armada 容量：约 {size} GiB。",
  "confirm.planPartitions": "分区：{names}。",
  "confirm.keepCharger": "请保持充电器连接，安装过程中请勿关机或拔出 SD 卡。",
  "confirm.powerWarning": "供电源未知，请先连接充电器再继续。",
  "running.title": "正在安装",
  "running.phase.preparing": "正在准备分区",
  "running.phase.copying": "正在写入镜像",
  "running.phase.boot": "正在完成引导程序",
  "running.phase.working": "处理中",
  "running.keepPowered": "完成前请勿关闭设备电源。",
  "running.panelClose": "关闭此面板不会取消安装。",
  "running.pollUnavailable": "暂时无法读取安装状态，请保持设备通电。",
  "success.title": "安装完成",
  "success.body": "请关机，取出 SD 卡，然后从内置磁盘启动。",
  "success.powerOff": "关机",
  "success.powerOffHint": "只有在按下此按钮后设备才会关机。",
  "failure.title": "安装失败",
  "failure.body": "安装未完成。请在 ABL 引导菜单中选择从 SD 卡启动，再重新尝试安装。若要放弃内置安装，可在 ABL 中选择 UNINSTALL CFW。",
  "failure.retry": "刷新并重试",
  "failure.errorLabel": "错误",
  "error.installer_unavailable": "安装后端不可用。",
  "error.inspection_failed": "无法检查磁盘布局。",
  "error.toosmall": "磁盘空间不足，无法完成安装。",
  "error.invalid_android_size": "该 Android 容量不被允许。",
  "error.battery_only": "请连接充电器，仅用电池时禁止安装。",
  "error.boot_disk_forbidden": "启动磁盘不能作为安装目标。",
  "error.boot_disk_unknown": "无法识别启动磁盘。",
  "error.confirmation_required": "安装前必须完成确认步骤。",
  "error.unknown_token": "该方案已失效，请重新检查。",
  "error.unit_running": "已有安装任务正在运行。",
  "error.target_changed": "自检查以来磁盘已变化，请重新检查。",
  "error.status_unavailable": "无法获取安装状态。",
  "error.not_succeeded": "安装尚未成功完成。",
  "error.launch_failed": "无法启动安装程序。",
  "error.poweroff_failed": "无法关闭设备电源。",
  "error.internal_error": "发生内部错误。",
  "error.unknown": "发生未知错误。",
};

export type Locale = "en" | "zh";

export const localeStrings: Record<Locale, Record<TranslationKey, string>> = { en, zh };

type Variables = Record<string, string | number>;

export function localeFromLanguage(language: unknown): Locale | null {
  if (typeof language !== "string") return null;
  const normalized = language.trim().toLowerCase().split("_").join("-");
  if (!normalized) return null;
  if (normalized.startsWith("zh") || normalized === "schinese" || normalized === "chinese") return "zh";
  return "en";
}

function firstLocale(values: readonly unknown[] | undefined): Locale | null {
  for (const value of values || []) {
    const locale = localeFromLanguage(value);
    if (locale) return locale;
  }
  return null;
}

export function resolveLocale({
  steamLanguage,
  deckyLocales,
  browserLanguages,
}: {
  steamLanguage?: unknown;
  deckyLocales?: readonly unknown[];
  browserLanguages?: readonly unknown[];
}): Locale {
  return localeFromLanguage(steamLanguage)
    || firstLocale(deckyLocales)
    || firstLocale(browserLanguages)
    || "en";
}

// Only Decky's own LocalizationManager is read here, so the plugin picks a
// language before any Steam sign-in. SteamClient is deliberately untouched.
type DeckyWindow = { LocalizationManager?: { m_rgLocalesToUse?: readonly unknown[] } };

export function detectLocaleFromEnvironment(): Locale {
  let deckyLocales: readonly unknown[] | undefined;
  let browserLanguages: readonly unknown[] | undefined;
  try {
    const scope = typeof window === "undefined" ? undefined : (window as unknown as DeckyWindow);
    deckyLocales = scope?.LocalizationManager?.m_rgLocalesToUse;
  } catch (error) {
  }
  try {
    browserLanguages = navigator.languages || [navigator.language];
  } catch (error) {
  }
  return resolveLocale({ deckyLocales, browserLanguages });
}

function interpolate(text: string, variables?: Variables): string {
  if (!variables) return text;
  return Object.entries(variables).reduce(
    (result, [key, value]) => result.split(`{${key}}`).join(String(value)),
    text,
  );
}

export function translate(locale: Locale, key: TranslationKey, variables?: Variables): string {
  return interpolate(localeStrings[locale][key], variables);
}

export function translateErrorCode(locale: Locale, code: string): string {
  if (!code) return "";
  const key = `error.${code}`;
  return Object.prototype.hasOwnProperty.call(localeStrings.en, key)
    ? localeStrings[locale][key as TranslationKey]
    : translate(locale, "error.internal_error");
}
