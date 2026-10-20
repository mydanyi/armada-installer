import { DropdownItem, Field, PanelSection, PanelSectionRow, Tabs } from "@decky/ui";
import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { version as pluginVersion } from "../package.json";
import { author as pluginAuthor } from "../plugin.json";
import { Content } from "./Content";
import { TfLibraryContent } from "./TfLibraryContent";
import { detectLocaleFromEnvironment, localeFromLanguage, translate } from "./i18n";
import type { Locale, TranslationKey } from "./i18n";
import { installerStyles } from "./styles";

type TabId = "system" | "tf" | "settings";

const languageOptions = [
  { data: "zh", label: "中文" },
  { data: "en", label: "English" },
];

const tabIds: TabId[] = ["system", "tf", "settings"];

const tabLabelKeys: Record<TabId, TranslationKey> = {
  system: "tab.system",
  tf: "tab.tf",
  settings: "tab.settings",
};

function DiskIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" />
    </svg>
  );
}

function StorageCardIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M5 8h5V3" />
      <path d="M9 14v4M12 14v4M15 14v4" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
    </svg>
  );
}

// Icon-only tab targets keep the accessible name from the localized label, so
// the tab strip stays narrow without losing a readable title or tooltip.
function TabIcon({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="armada-installer-tab-label" role="img" aria-label={label} title={label}>
      {children}
    </span>
  );
}

// Owns the shared locale, the icon tab strip and the settings tab. Exactly one
// feature is mounted at a time, so switching tabs unmounts the other one and
// clears its status poll timer while the systemd job keeps running.
export function PluginContent() {
  const [locale, setLocale] = useState<Locale>(() => detectLocaleFromEnvironment());
  const [tab, setTab] = useState<TabId>("system");
  const text = useCallback((key: TranslationKey) => translate(locale, key), [locale]);

  const tabContent = (content: ReactNode) => (
    <div className="armada-installer-tab-content">{content}</div>
  );

  const tabs = [
    {
      id: "system",
      title: <TabIcon label={text(tabLabelKeys.system)}><DiskIcon /></TabIcon>,
      content: tabContent(tab === "system" ? <Content locale={locale} hideLanguageSelector /> : null),
    },
    {
      id: "tf",
      title: <TabIcon label={text(tabLabelKeys.tf)}><StorageCardIcon /></TabIcon>,
      content: tabContent(tab === "tf" ? <TfLibraryContent locale={locale} /> : null),
    },
    {
      id: "settings",
      title: <TabIcon label={text(tabLabelKeys.settings)}><GearIcon /></TabIcon>,
      content: tabContent(
        <PanelSection title={text("settings.title")}>
          <PanelSectionRow>
            <DropdownItem
              label={text("language.label")}
              layout="below"
              childrenContainerWidth="max"
              rgOptions={languageOptions}
              selectedOption={locale}
              onChange={(option) => setLocale(localeFromLanguage(option.data) ?? "en")}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label={text("author.label")} description={pluginAuthor} />
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label={text("version.label")} description={`v${pluginVersion}`} />
          </PanelSectionRow>
        </PanelSection>,
      ),
    },
  ];

  const activeTab = tabIds.includes(tab) ? tab : "system";

  return (
    <div className="armada-installer-ui">
      <style>{installerStyles}</style>
      <div className="armada-installer-version" title={text("version.label")}>
        <span>{text("app.title")}</span>
        <span className="armada-installer-version-tag">{`v${pluginVersion}`}</span>
      </div>
      <div className="armada-installer-tabs">
        <Tabs
          activeTab={activeTab}
          onShowTab={(nextTab: string) => {
            if (tabIds.includes(nextTab as TabId)) setTab(nextTab as TabId);
          }}
          tabs={tabs}
        />
      </div>
    </div>
  );
}
