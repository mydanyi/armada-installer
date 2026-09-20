import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  ProgressBarItem,
  SliderField,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { getInspection, getStatus, powerOff, prepareInstall, startInstall } from "./backend";
import { detectLocaleFromEnvironment, localeFromLanguage, translate, translateErrorCode } from "./i18n";
import type { Locale, TranslationKey } from "./i18n";
import {
  canReviewInstall,
  clampSize,
  confirmationFacts,
  confirmationSpec,
  defaultSizeFor,
  erasesAndroidData,
  installBlockCode,
  isInstallableLayout,
  isSizeValid,
  powerGate,
  remainingLinuxGib,
  sizeBounds,
} from "./model";
import type { Inspection, InstallPhase, Layout, Plan, PowerState, Status } from "./types";

const POLL_INTERVAL_MS = 1500;

const languageOptions = [
  { data: "zh", label: "中文" },
  { data: "en", label: "English" },
];

const layoutKeys: Record<Layout, TranslationKey> = {
  fresh: "inspection.layoutFresh",
  occupied: "inspection.layoutOccupied",
  toosmall: "inspection.layoutToosmall",
  unavailable: "inspection.layoutUnavailable",
};

const phaseKeys: Record<InstallPhase, TranslationKey> = {
  preparing: "running.phase.preparing",
  copying: "running.phase.copying",
  boot: "running.phase.boot",
  working: "running.phase.working",
};

type TextFn = (key: TranslationKey, variables?: Record<string, string | number>) => string;

type ErrorInfo = { code: string; detail: string };

function ConfirmInstallModal({
  plan,
  text,
  onConfirm,
  onClosed,
  closeModal,
}: {
  plan: Plan;
  text: TextFn;
  onConfirm: () => void;
  onClosed: () => void;
  closeModal?: () => void;
}) {
  const facts = confirmationFacts(plan);
  const spec = confirmationSpec(facts.layout);
  const dismiss = () => {
    closeModal?.();
    onClosed();
  };
  const lines: string[] = [
    text(spec.descriptionKey),
    text(spec.eraseKey),
    text("confirm.planTarget", { device: facts.device }),
    text(facts.layout === "occupied" ? "confirm.planAndroidExisting" : "confirm.planAndroid",
      { size: facts.androidGib }),
  ];
  if (facts.linuxGib !== null) lines.push(text("confirm.planLinux", { size: facts.linuxGib }));
  if (facts.partitions !== null) {
    lines.push(text("confirm.planPartitions", { names: facts.partitions }));
  }
  if (facts.powerUnknown) lines.push(text("confirm.powerWarning"));
  lines.push(text("confirm.keepCharger"));
  return (
    <ConfirmModal
      strTitle={text(spec.titleKey)}
      strDescription={lines.join("\n")}
      strOKButtonText={text("common.confirm")}
      strCancelButtonText={text("common.cancel")}
      bDestructiveWarning
      bOKDisabled={false}
      onOK={() => { onConfirm(); dismiss(); }}
      onCancel={dismiss}
    />
  );
}

export function Content() {
  const [locale, setLocale] = useState<Locale>(() => detectLocaleFromEnvironment());
  const text = useCallback<TextFn>((key, variables) => translate(locale, key, variables), [locale]);

  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [inspectionError, setInspectionError] = useState<ErrorInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [actionError, setActionError] = useState<ErrorInfo | null>(null);
  const [androidGib, setAndroidGib] = useState(32);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [statusError, setStatusError] = useState(true);
  const [retryRequested, setRetryRequested] = useState(false);
  const [pollError, setPollError] = useState<ErrorInfo | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [session, setSession] = useState(0);

  const mounted = useRef(true);
  const actionLock = useRef(false);
  const sessionRef = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadInspection = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getInspection();
      if (!mounted.current) return;
      if (!result.ok) {
        setInspectionError({ code: result.error, detail: result.detail });
        setInspection(null);
        return;
      }
      const next = result.data;
      setInspectionError(null);
      setInspection(next);
      setAndroidGib(next.layout === "fresh" ? defaultSizeFor(next.info) : 32);
    } catch (error) {
      if (!mounted.current) return;
      setInspectionError({ code: "inspection_failed", detail: String(error) });
      setInspection(null);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadInspection();
  }, [loadInspection]);

  const openConfirm = useCallback((next: Plan) => {
    showModal(
      <ConfirmInstallModal
        plan={next}
        text={text}
        onConfirm={() => { void confirmInstall(next); }}
        onClosed={() => { setModalOpen(false); }}
      />,
    );
  }, [text]);

  const confirmInstall = useCallback(async (active: Plan) => {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setActionError(null);
    setPollError(null);
    const nextSession = sessionRef.current + 1;
    sessionRef.current = nextSession;
    setSession(nextSession);
    try {
      const result = await startInstall(active.token, true);
      if (!mounted.current) return;
      if (!result.ok) {
        setActionError({ code: result.error, detail: result.detail });
        return;
      }
      setStatusError(false);
      setRetryRequested(false);
      setStatus(result.data);
    } catch (error) {
      if (mounted.current) setActionError({ code: "internal_error", detail: String(error) });
    } finally {
      actionLock.current = false;
      if (mounted.current) setBusy(false);
    }
  }, []);

  const review = useCallback(async () => {
    if (actionLock.current || busy || modalOpen) return;
    if (!inspection || !isInstallableLayout(inspection.layout)) return;
    // The handler keeps the same guard as the button; a disabled button alone
    // must never be the only thing standing between the user and an install.
    if (!canReviewInstall(inspection, status, statusError, retryRequested)) return;
    const layout = inspection.layout;
    const requested = layout === "fresh" ? clampSize(androidGib, inspection.info) : null;
    if (requested !== null && !isSizeValid(requested, inspection.info)) {
      setActionError({ code: "invalid_android_size", detail: "" });
      return;
    }
    actionLock.current = true;
    setBusy(true);
    setActionError(null);
    try {
      const result = await prepareInstall(requested);
      if (!mounted.current) return;
      if (!result.ok) {
        setActionError({ code: result.error, detail: result.detail });
        return;
      }
      const next = result.data;
      setPlan(next);
      setModalOpen(true);
      openConfirm(next);
    } catch (error) {
      if (mounted.current) setActionError({ code: "internal_error", detail: String(error) });
    } finally {
      actionLock.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [androidGib, busy, inspection, modalOpen, openConfirm, retryRequested, status, statusError]);

  const doPowerOff = useCallback(async () => {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    try {
      const result = await powerOff();
      if (!mounted.current) return;
      if (!result.ok) setActionError({ code: result.error, detail: result.detail });
    } catch (error) {
      if (mounted.current) setActionError({ code: "internal_error", detail: String(error) });
    } finally {
      actionLock.current = false;
      if (mounted.current) setBusy(false);
    }
  }, []);

  const reset = useCallback(() => {
    // Keep the real backend failure state: the flag lets review proceed only
    // while the status is genuinely failed and the user asked to retry.
    setRetryRequested(true);
    setPlan(null);
    setPollError(null);
    setActionError(null);
    setShowDetails(false);
    void loadInspection();
  }, [loadInspection]);

  const running = status?.state === "running";

  // A single outstanding poll, scheduled recursively so it survives errors and
  // runs for the whole mount: it also restores a running/succeeded/failed state
  // the first time the panel opens. Results from a superseded session are
  // dropped so a late poll cannot overwrite a newer start.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const schedule = () => {
      if (cancelled) return;
      timer = window.setTimeout(() => { void poll(); }, POLL_INTERVAL_MS);
    };
    const poll = async () => {
      if (cancelled) return;
      // Hold while an action is in flight; its result is authoritative.
      if (actionLock.current) {
        schedule();
        return;
      }
      const generation = sessionRef.current;
      try {
        const result = await getStatus();
        if (cancelled || generation !== sessionRef.current || actionLock.current) return;
        if (!result.ok) {
          setPollError({ code: result.error, detail: result.detail });
          setStatusError(true);
          return;
        }
        setPollError(null);
        setStatusError(false);
        setStatus(result.data);
      } catch (error) {
        if (!cancelled && generation === sessionRef.current) {
          setPollError({ code: "status_unavailable", detail: String(error) });
          setStatusError(true);
        }
      } finally {
        schedule();
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [session]);

  const powerText = (power: PowerState) => {
    const gate = powerGate(power);
    if (gate === "ok") return text("inspection.powerExternal");
    if (gate === "battery") {
      return typeof power.battery_percent === "number"
        ? text("inspection.powerBattery", { percent: power.battery_percent })
        : text("inspection.powerBatteryUnknown");
    }
    return text("inspection.powerUnknown");
  };

  const detailsBlock = (details: (string | undefined)[], log?: string) => {
    const body = [...details, log].filter((part): part is string => !!part && part.length > 0).join("\n");
    if (!body) return null;
    return (
      <>
        <PanelSectionRow>
          <ToggleField label={text("common.details")} checked={showDetails} onChange={setShowDetails} />
        </PanelSectionRow>
        {showDetails ? (
          <PanelSectionRow>
            <Field
              label={text("details.title")}
              description={<span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{body}</span>}
            />
          </PanelSectionRow>
        ) : null}
      </>
    );
  };

  const infoRows = (current: Inspection) => (
    <>
      <PanelSectionRow>
        <Field label={text("inspection.targetDisk")} description={current.info.device} />
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label={text("inspection.layout")} description={text(layoutKeys[current.layout])} />
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label={text("inspection.power")} description={powerText(current.power)} />
      </PanelSectionRow>
    </>
  );

  let body: ReactNode = null;

  if (running && status) {
    const phase = phaseKeys[status.phase] ?? "running.phase.working";
    body = (
      <>
        <PanelSectionRow>
          <Field label={text("running.title")} description={text(phase)} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ProgressBarItem label={text(phase)} indeterminate />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field description={text("running.keepPowered")} />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field description={text("running.panelClose")} />
        </PanelSectionRow>
        {pollError ? (
          <PanelSectionRow>
            <Field description={text("running.pollUnavailable")} />
          </PanelSectionRow>
        ) : null}
        {plan ? (
          <PanelSectionRow>
            <Field label={text("inspection.targetDisk")} description={plan.device} />
          </PanelSectionRow>
        ) : null}
        {detailsBlock([pollError?.detail], status.log_tail)}
      </>
    );
  } else if (status?.state === "succeeded") {
    body = (
      <>
        <PanelSectionRow>
          <Field label={text("success.title")} description={text("success.body")} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem onClick={() => { void doPowerOff(); }} disabled={busy}>
            {text("success.powerOff")}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field description={text("success.powerOffHint")} />
        </PanelSectionRow>
        {actionError ? (
          <PanelSectionRow>
            <Field label={text("failure.errorLabel")} description={translateErrorCode(locale, actionError.code)} />
          </PanelSectionRow>
        ) : null}
        {detailsBlock([actionError?.detail])}
      </>
    );
  } else if (status?.state === "failed" && !retryRequested) {
    body = (
      <>
        <PanelSectionRow>
          <Field label={text("failure.title")} description={text("failure.body")} />
        </PanelSectionRow>
        {actionError ? (
          <PanelSectionRow>
            <Field label={text("failure.errorLabel")} description={translateErrorCode(locale, actionError.code)} />
          </PanelSectionRow>
        ) : null}
        <PanelSectionRow>
          <ButtonItem onClick={reset} disabled={busy}>{text("failure.retry")}</ButtonItem>
        </PanelSectionRow>
        {detailsBlock([actionError?.detail, pollError?.detail], status.log_tail)}
      </>
    );
  } else if (loading && !inspection) {
    body = (
      <PanelSectionRow>
        <Field label={text("common.loading")} />
      </PanelSectionRow>
    );
  } else if (!inspection) {
    body = (
      <>
        <PanelSectionRow>
          <Field
            label={text("app.title")}
            description={translateErrorCode(locale, inspectionError?.code ?? "inspection_failed")}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem onClick={() => { void loadInspection(); }} disabled={busy}>
            {text("common.refresh")}
          </ButtonItem>
        </PanelSectionRow>
        {detailsBlock([inspectionError?.detail])}
      </>
    );
  } else {
    const layout = isInstallableLayout(inspection.layout) ? inspection.layout : null;
    const bounds = inspection.layout === "fresh" ? sizeBounds(inspection.info) : null;
    const remaining = layout ? remainingLinuxGib(inspection.info, androidGib) : null;
    const sizeOk = inspection.layout !== "fresh" || isSizeValid(androidGib, inspection.info);
    const canReview = canReviewInstall(inspection, status, statusError, retryRequested) && sizeOk;
    const blockCode = installBlockCode(inspection);
    const gate = powerGate(inspection.power);
    const needsRefresh = gate !== "ok" || statusError || status === null;
    body = (
      <>
        <PanelSectionRow>
          <Field description={text("inspection.hint")} />
        </PanelSectionRow>
        {infoRows(inspection)}
        {layout ? (
          <PanelSectionRow>
            <Field
              label={text(layout === "fresh" ? "fresh.title" : "occupied.title")}
              description={text(erasesAndroidData(layout) ? "fresh.warning" : "occupied.warning")}
            />
          </PanelSectionRow>
        ) : null}
        {inspection.layout === "fresh" && bounds ? (
          <>
            <PanelSectionRow>
              <SliderField
                label={text("fresh.size")}
                value={androidGib}
                min={bounds.min}
                max={bounds.max}
                step={1}
                showValue
                disabled={busy}
                onChange={setAndroidGib}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <Field
                label={text("fresh.remainingLabel")}
                description={remaining === null
                  ? text("fresh.remainingUnknown")
                  : text("fresh.remainingValue", { size: remaining })}
              />
            </PanelSectionRow>
          </>
        ) : null}
        {inspection.layout === "occupied" ? (
          <>
            <PanelSectionRow>
              <Field
                label={text("occupied.androidLabel")}
                description={typeof inspection.info.android_gib === "number"
                  ? text("occupied.androidValue", { size: inspection.info.android_gib })
                  : text("occupied.unknown")}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <Field
                label={text("occupied.linuxLabel")}
                description={typeof inspection.info.linux_gib === "number"
                  ? text("occupied.linuxValue", { size: inspection.info.linux_gib })
                  : text("occupied.unknown")}
              />
            </PanelSectionRow>
          </>
        ) : null}
        {!canReview ? (
          <PanelSectionRow>
            <Field
              description={blockCode
                ? translateErrorCode(locale, blockCode)
                : gate === "battery"
                  ? text("block.batteryOnly")
                  : gate === "unknown"
                    ? text("block.powerUnknown")
                    : text("error.status_unavailable")}
            />
          </PanelSectionRow>
        ) : null}
        <PanelSectionRow>
          {layout ? (
            <ButtonItem onClick={() => { void review(); }} disabled={!canReview || busy || modalOpen}>
              {text("common.review")}
            </ButtonItem>
          ) : (
            <ButtonItem onClick={() => { void loadInspection(); }} disabled={busy}>
              {text("common.refresh")}
            </ButtonItem>
          )}
        </PanelSectionRow>
        {layout && needsRefresh ? (
          <PanelSectionRow>
            <ButtonItem onClick={() => { void loadInspection(); }} disabled={busy}>
              {text("common.refresh")}
            </ButtonItem>
          </PanelSectionRow>
        ) : null}
        {actionError ? (
          <PanelSectionRow>
            <Field label={text("failure.errorLabel")} description={translateErrorCode(locale, actionError.code)} />
          </PanelSectionRow>
        ) : null}
        {detailsBlock([inspectionError?.detail, actionError?.detail, inspection.detail])}
      </>
    );
  }

  return (
    <PanelSection title={text("app.title")}>
      <PanelSectionRow>
        <DropdownItem
          label={text("language.label")}
          rgOptions={languageOptions}
          selectedOption={locale}
          onChange={(option) => setLocale(localeFromLanguage(option.data) ?? "en")}
        />
      </PanelSectionRow>
      {body}
    </PanelSection>
  );
}
