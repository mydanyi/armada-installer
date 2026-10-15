// TF-specific presentation maps. The strings themselves live in the single
// ./i18n dictionary (so the shared completeness check keeps covering them);
// this module maps backend codes to those keys and re-exports the localiser so
// the TF component never renders a raw machine code.

// Native Node 22 --experimental-strip-types needs the explicit .ts extension on
// runtime relative imports; the bundler/tsc emit rewrites it via
// rewriteRelativeImportExtensions (see tsconfig.json). Type-only imports stay
// extensionless.
import { translate, translateErrorCode } from "./i18n.ts";
import type { Locale, TranslationKey } from "./i18n";
import type { TfCardState, TfPhase } from "./tf_types";

export const tfStateKeys: Record<TfCardState, TranslationKey> = {
  pending: "tf.state.pending",
  connected: "tf.state.connected",
  recovery: "tf.state.recovery",
  blocked: "tf.state.blocked",
};

export const tfPhaseKeys: Record<TfPhase, TranslationKey> = {
  preparing: "tf.phase.preparing",
  formatting: "tf.phase.formatting",
  mounting: "tf.phase.mounting",
  registering: "tf.phase.registering",
};

export function tfText(locale: Locale, key: TranslationKey, variables?: Record<string, string | number>): string {
  return translate(locale, key, variables);
}

// Unknown or missing codes fall back to the localised generic error, never the
// raw code, so a technical identifier never reaches the main user flow.
export function tfErrorText(locale: Locale, code: string): string {
  return translateErrorCode(locale, code);
}
