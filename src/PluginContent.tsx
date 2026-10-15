import { DropdownItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useCallback, useState } from "react";
import { Content } from "./Content";
import { TfLibraryContent } from "./TfLibraryContent";
import { detectLocaleFromEnvironment, localeFromLanguage, translate } from "./i18n";
import type { Locale, TranslationKey } from "./i18n";

type Mode = "system" | "tf";

const languageOptions = [
  { data: "zh", label: "中文" },
  { data: "en", label: "English" },
];

const modeOptions: { data: Mode; labelKey: TranslationKey }[] = [
  { data: "system", labelKey: "mode.system" },
  { data: "tf", labelKey: "mode.tf" },
];

// Owns the shared mode selector and the common language dropdown, then renders
// exactly one feature. Because only the visible feature is mounted, only its
// status poll runs; switching modes unmounts the other component and clears its
// timer while the systemd job (if any) keeps running.
export function PluginContent() {
  const [locale, setLocale] = useState<Locale>(() => detectLocaleFromEnvironment());
  const [mode, setMode] = useState<Mode>("system");
  const text = useCallback((key: TranslationKey) => translate(locale, key), [locale]);

  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <DropdownItem
            label={text("mode.label")}
            layout="below"
            childrenContainerWidth="max"
            rgOptions={modeOptions.map((option) => ({ data: option.data, label: text(option.labelKey) }))}
            selectedOption={mode}
            onChange={(option) => setMode(option.data as Mode)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label={text("language.label")}
            rgOptions={languageOptions}
            selectedOption={locale}
            onChange={(option) => setLocale(localeFromLanguage(option.data) ?? "en")}
          />
        </PanelSectionRow>
      </PanelSection>
      {mode === "tf"
        ? <TfLibraryContent locale={locale} />
        : <Content locale={locale} hideLanguageSelector />}
    </>
  );
}
