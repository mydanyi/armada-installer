// Scoped CSS for the plugin panel. Everything is rooted at `.armada-installer-ui`
// so the Decky QAM and other plugins are untouched. The rules keep the tab strip
// and its content inside the narrow QAM width, and let long device paths or
// backend details wrap instead of widening the panel. The root is a bounded
// fixed panel below the Decky title: the version header keeps its natural
// height and the tabs wrapper fills the rest, so Decky's own Tabs root gets a
// definite height and its native vertical scroll handles overflowing content.
export const installerStyles = `
  .armada-installer-ui {
    position: fixed;
    top: 56px;
    bottom: 0;
    width: 300px;
    max-width: 100%;
    min-width: 0;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    overflow: hidden;
    padding-bottom: 0;
  }

  .armada-installer-ui .armada-installer-version {
    flex: 0 0 auto;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    width: 100%;
    max-width: 100%;
    min-width: 0;
    box-sizing: border-box;
    padding: 4px 12px 0;
    font-size: 12px;
    line-height: 1.4;
  }

  .armada-installer-ui .armada-installer-tabs {
    flex: 1 1 0;
    min-height: 0;
    width: 100%;
    max-width: 100%;
    min-width: 0;
    position: relative;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
  }

  .armada-installer-ui .armada-installer-tabs > div:first-child {
    height: 100%;
    min-height: 100%;
    width: 100%;
  }

  .armada-installer-ui .armada-installer-tabs > div > div:first-child {
    background: #0D141C;
    box-shadow: none;
    backdrop-filter: none;
  }

  .armada-installer-ui .armada-installer-version-tag {
    flex: 0 0 auto;
    font-variant-numeric: tabular-nums;
  }

  .armada-installer-ui [role="tablist"] {
    display: flex;
    flex-wrap: nowrap;
    justify-content: center;
  }

  .armada-installer-ui [role="tab"] {
    flex: 0 1 auto;
    min-width: 0;
    box-sizing: border-box;
    padding-left: 6px !important;
    padding-right: 6px !important;
    display: flex !important;
    align-items: center;
    justify-content: center;
  }

  .armada-installer-ui [role="tab"] svg {
    display: block;
    margin: 0;
  }

  .armada-installer-ui .armada-installer-tab-label {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    min-width: 0;
    max-width: 100%;
  }

  .armada-installer-ui [role="tabpanel"] {
    width: 100%;
    max-width: 100%;
    min-width: 0;
    box-sizing: border-box;
    padding-left: 8px !important;
    padding-right: 8px !important;
  }

  .armada-installer-ui .armada-installer-tab-content,
  .armada-installer-ui .armada-installer-tab-content * {
    min-width: 0;
    box-sizing: border-box;
  }

  .armada-installer-ui .armada-installer-tab-content {
    width: 100%;
    max-width: 100%;
    overflow-wrap: anywhere;
    word-break: break-word;
    padding-bottom: 32px;
  }

  .armada-installer-ui .armada-installer-wrap {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
`;
