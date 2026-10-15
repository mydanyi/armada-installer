import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  ProgressBarItem,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { getTfInspection, getTfStatus, prepareTfLibrary, startTfLibrary } from "./tf_backend";
import { tfErrorText, tfPhaseKeys, tfStateKeys, tfText } from "./tf_i18n";
import type { Locale, TranslationKey } from "./i18n";
import {
  bytesToGib,
  canReviewTfCard,
  confirmSpecFor,
  libraryResult,
  retryConnectAllowed,
  successBodyKey,
} from "./tf_model";
import type { TfCard, TfPhase, TfPlan, TfStatus } from "./tf_types";

const POLL_INTERVAL_MS = 1500;

type TextFn = (key: TranslationKey, variables?: Record<string, string | number>) => string;

type ErrorInfo = { code: string; detail: string };

// The destructive confirmation is driven entirely by the returned plan: only a
// format plan reads as an erase. A mount plan (including a recovery that
// resolves to mount) keeps the already-formatted data and never shows erasing.
function TfConfirmModal({
  plan,
  text,
  onConfirm,
  onClosed,
  closeModal,
}: {
  plan: TfPlan;
  text: TextFn;
  onConfirm: () => void;
  onClosed: () => void;
  closeModal?: () => void;
}) {
  const spec = confirmSpecFor(plan);
  const dismiss = () => {
    closeModal?.();
    onClosed();
  };
  const lines: string[] = [text(spec.descriptionKey)];
  if (spec.eraseKey) lines.push(text(spec.eraseKey));
  lines.push(text("tf.confirm.target", { device: plan.device }));
  lines.push(text("tf.confirm.reserve", { device: plan.reserve_device }));
  const linux = bytesToGib(plan.linux_bytes);
  const android = bytesToGib(plan.android_bytes);
  if (linux !== null) lines.push(text("tf.confirm.linux", { size: linux }));
  if (android !== null) lines.push(text("tf.confirm.android", { size: android }));
  lines.push(text("tf.confirm.keepCard"));
  return (
    <ConfirmModal
      strTitle={text(spec.titleKey)}
      strDescription={lines.join("\n")}
      strOKButtonText={text(spec.okKey)}
      strCancelButtonText={text("common.cancel")}
      bDestructiveWarning={spec.destructive}
      bOKDisabled={false}
      onOK={() => { onConfirm(); dismiss(); }}
      onCancel={dismiss}
    />
  );
}

export function TfLibraryContent({ locale }: { locale: Locale }) {
  const text = useCallback<TextFn>((key, variables) => tfText(locale, key, variables), [locale]);

  const [cards, setCards] = useState<TfCard[]>([]);
  const [cardsError, setCardsError] = useState<ErrorInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [actionError, setActionError] = useState<ErrorInfo | null>(null);
  const [plan, setPlan] = useState<TfPlan | null>(null);
  const [status, setStatus] = useState<TfStatus | null>(null);
  const [statusError, setStatusError] = useState(true);
  const [retryRequested, setRetryRequested] = useState(false);
  // Set only by an explicit "Back to cards" click after a succeeded status, so
  // an old success can be managed without auto-formatting or auto-mounting.
  const [listRequested, setListRequested] = useState(false);
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

  const loadCards = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getTfInspection();
      if (!mounted.current) return;
      if (!result.ok) {
        setCardsError({ code: result.error, detail: result.detail });
        setCards([]);
        return;
      }
      const next = result.data.cards;
      setCardsError(null);
      setCards(next);
      setSelectedDevice((current) =>
        next.some((card) => card.device === current) ? current : (next[0]?.device ?? null));
    } catch (error) {
      if (!mounted.current) return;
      setCardsError({ code: "inspection_failed", detail: String(error) });
      setCards([]);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCards();
  }, [loadCards]);

  const selected = cards.find((card) => card.device === selectedDevice) ?? null;

  const openConfirm = useCallback((next: TfPlan) => {
    showModal(
      <TfConfirmModal
        plan={next}
        text={text}
        onConfirm={() => { void confirmPlan(next); }}
        onClosed={() => { setModalOpen(false); }}
      />,
    );
  }, [text]);

  const confirmPlan = useCallback(async (active: TfPlan) => {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setActionError(null);
    setPollError(null);
    setListRequested(false);
    const nextSession = sessionRef.current + 1;
    sessionRef.current = nextSession;
    setSession(nextSession);
    try {
      const result = await startTfLibrary(active.token, true);
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
    if (!selected) return;
    if (!canReviewTfCard(selected, status, statusError, retryRequested, listRequested)) return;
    // The device path comes from a card the backend just inspected; a forged or
    // stale value is rejected by prepare, and start still needs the plan token.
    actionLock.current = true;
    setBusy(true);
    setActionError(null);
    try {
      const result = await prepareTfLibrary(selected.device);
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
  }, [busy, listRequested, modalOpen, openConfirm, retryRequested, selected, status, statusError]);

  // Retry connecting after a mount that Steam has not picked up yet: prepare a
  // fresh mount plan and start it on this authorized click, never formatting.
  const retryConnect = useCallback(async () => {
    if (actionLock.current || busy) return;
    if (!selected) return;
    actionLock.current = true;
    setBusy(true);
    setActionError(null);
    setPollError(null);
    try {
      const prepared = await prepareTfLibrary(selected.device);
      if (!mounted.current) return;
      if (!prepared.ok) {
        setActionError({ code: prepared.error, detail: prepared.detail });
        return;
      }
      // The mount-only retry may only start a fresh mount directly. If the card
      // changed and re-prepared as a format, never run it from this click: that
      // would erase the Linux reserve partition without the destructive confirm.
      if (!retryConnectAllowed(prepared.data)) {
        setActionError({ code: "target_changed", detail: "" });
        void loadCards();
        return;
      }
      setListRequested(false);
      const nextSession = sessionRef.current + 1;
      sessionRef.current = nextSession;
      setSession(nextSession);
      const started = await startTfLibrary(prepared.data.token, true);
      if (!mounted.current) return;
      if (!started.ok) {
        setActionError({ code: started.error, detail: started.detail });
        return;
      }
      setStatusError(false);
      setRetryRequested(false);
      setStatus(started.data);
    } catch (error) {
      if (mounted.current) setActionError({ code: "internal_error", detail: String(error) });
    } finally {
      actionLock.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [busy, loadCards, selected]);

  const reset = useCallback(() => {
    // Keep the real backend failure state: review proceeds only while the
    // status is genuinely failed and the user asked to retry.
    setRetryRequested(true);
    setListRequested(false);
    setPlan(null);
    setPollError(null);
    setActionError(null);
    setShowDetails(false);
    void loadCards();
  }, [loadCards]);

  // Explicit user action after a success: return to the real card list so the
  // card can be managed while the backend's actual succeeded status is kept.
  // Nothing is formatted or mounted automatically.
  const backToCards = useCallback(() => {
    setListRequested(true);
    void loadCards();
  }, [loadCards]);

  const running = status?.state === "running";

  // A single recursive poll that survives errors and runs for the whole mount,
  // so re-opening the panel restores a running/succeeded/failed conversion. The
  // systemd job keeps running independently; a superseded session is dropped.
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
        const result = await getTfStatus();
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

  const sizeText = (bytes: number | null | undefined) => {
    const gib = bytesToGib(bytes);
    return gib === null ? text("tf.sizeUnknown") : text("tf.sizeGiB", { size: gib });
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

  const errorRow = (error: ErrorInfo | null) => (error ? (
    <PanelSectionRow>
      <Field label={text("failure.errorLabel")} description={tfErrorText(locale, error.code)} />
    </PanelSectionRow>
  ) : null);

  let body: ReactNode = null;

  if (running && status) {
    const phaseKey = tfPhaseKeys[status.phase as TfPhase] ?? "tf.phase.preparing";
    body = (
      <>
        <PanelSectionRow>
          <Field label={text("tf.running.title")} description={text(phaseKey)} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ProgressBarItem label={text(phaseKey)} indeterminate />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field description={text("tf.running.keepPowered")} />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field description={text("tf.running.panelClose")} />
        </PanelSectionRow>
        {pollError ? (
          <PanelSectionRow>
            <Field description={text("tf.running.pollUnavailable")} />
          </PanelSectionRow>
        ) : null}
        {plan ? (
          <PanelSectionRow>
            <Field label={text("tf.card")} description={plan.device} />
          </PanelSectionRow>
        ) : null}
        {detailsBlock([pollError?.detail, status.detail], status.log_tail)}
      </>
    );
  } else if (status?.state === "succeeded" && !listRequested) {
    const result = libraryResult(status);
    body = (
      <>
        <PanelSectionRow>
          <Field label={text("tf.success.title")} description={text(successBodyKey(status))} />
        </PanelSectionRow>
        {result === "pending" ? (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              childrenContainerWidth="max"
              onClick={() => { void retryConnect(); }}
              disabled={busy}
            >
              {text("tf.success.retryConnect")}
            </ButtonItem>
          </PanelSectionRow>
        ) : null}
        <PanelSectionRow>
          <ButtonItem layout="below" childrenContainerWidth="max" onClick={backToCards} disabled={busy}>
            {text("tf.success.backToCards")}
          </ButtonItem>
        </PanelSectionRow>
        {errorRow(actionError)}
        {detailsBlock([actionError?.detail, status.detail], status.log_tail)}
      </>
    );
  } else if (status?.state === "failed" && !retryRequested) {
    body = (
      <>
        <PanelSectionRow>
          <Field label={text("tf.failure.title")} description={text("tf.failure.body")} />
        </PanelSectionRow>
        {errorRow(actionError)}
        <PanelSectionRow>
          <ButtonItem layout="below" childrenContainerWidth="max" onClick={reset} disabled={busy}>{text("failure.retry")}</ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" childrenContainerWidth="max" onClick={() => { void loadCards(); }} disabled={busy}>{text("common.refresh")}</ButtonItem>
        </PanelSectionRow>
        {detailsBlock([actionError?.detail, pollError?.detail, status.detail], status.log_tail)}
      </>
    );
  } else if (loading && cards.length === 0) {
    body = (
      <PanelSectionRow>
        <Field label={text("common.loading")} />
      </PanelSectionRow>
    );
  } else if (cards.length === 0) {
    body = (
      <>
        <PanelSectionRow>
          <Field label={text("tf.none.title")} description={text("tf.none.body")} />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field description={text("tf.none.sdBoot")} />
        </PanelSectionRow>
        {cardsError ? (
          <PanelSectionRow>
            <Field label={text("failure.errorLabel")} description={tfErrorText(locale, cardsError.code)} />
          </PanelSectionRow>
        ) : null}
        <PanelSectionRow>
          <ButtonItem layout="below" childrenContainerWidth="max" onClick={() => { void loadCards(); }} disabled={busy}>
            {text("tf.rescan")}
          </ButtonItem>
        </PanelSectionRow>
        {detailsBlock([cardsError?.detail])}
      </>
    );
  } else {
    const canReview = canReviewTfCard(selected, status, statusError, retryRequested, listRequested);
    const stateKey = selected ? tfStateKeys[selected.state] : "tf.state.blocked";
    const blocked = selected?.state === "blocked";
    const actionLabel = selected?.state === "connected" ? text("tf.mount") : text("common.review");
    body = (
      <>
        <PanelSectionRow>
          <Field description={text("tf.hint")} />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label={text("tf.card")}
            layout="below"
            childrenContainerWidth="max"
            rgOptions={cards.map((card) => ({
              data: card.device,
              label: `${card.device} · ${text(tfStateKeys[card.state])}`,
            }))}
            selectedOption={selectedDevice ?? undefined}
            onChange={(option) => setSelectedDevice(option.data as string)}
          />
        </PanelSectionRow>
        {selected ? (
          <>
            <PanelSectionRow>
              <Field label={text("tf.card")} description={selected.device} />
            </PanelSectionRow>
            <PanelSectionRow>
              <Field label={text("tf.stateLabel")} description={text(stateKey)} />
            </PanelSectionRow>
            <PanelSectionRow>
              <Field label={text("tf.androidLabel")} description={sizeText(selected.android_bytes)} />
            </PanelSectionRow>
            <PanelSectionRow>
              <Field label={text("tf.linuxLabel")} description={sizeText(selected.linux_bytes)} />
            </PanelSectionRow>
            {blocked ? (
              <>
                <PanelSectionRow>
                  <Field label={text("failure.errorLabel")} description={tfErrorText(locale, selected.reason)} />
                </PanelSectionRow>
                {selected.reason === "boot_disk_forbidden" || selected.reason === "boot_disk_unknown" ? (
                  <PanelSectionRow>
                    <Field description={text("tf.none.sdBoot")} />
                  </PanelSectionRow>
                ) : null}
              </>
            ) : null}
          </>
        ) : null}
        {!blocked && !canReview ? (
          <PanelSectionRow>
            <Field description={tfErrorText(locale, "status_unavailable")} />
          </PanelSectionRow>
        ) : null}
        <PanelSectionRow>
          {blocked ? (
            <ButtonItem layout="below" childrenContainerWidth="max" onClick={() => { void loadCards(); }} disabled={busy}>{text("tf.rescan")}</ButtonItem>
          ) : (
            <ButtonItem
              layout="below"
              childrenContainerWidth="max"
              onClick={() => { void review(); }}
              disabled={!canReview || busy || modalOpen}
            >
              {actionLabel}
            </ButtonItem>
          )}
        </PanelSectionRow>
        {!blocked ? (
          <PanelSectionRow>
            <ButtonItem layout="below" childrenContainerWidth="max" onClick={() => { void loadCards(); }} disabled={busy}>{text("tf.rescan")}</ButtonItem>
          </PanelSectionRow>
        ) : null}
        {errorRow(actionError)}
        {detailsBlock([cardsError?.detail, actionError?.detail, selected?.detail], status?.log_tail)}
      </>
    );
  }

  return (
    <PanelSection title={text("tf.title")}>
      {body}
    </PanelSection>
  );
}
