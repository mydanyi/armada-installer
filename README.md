# Armada Installer

Decky plugin that installs Armada onto the internal disk from the SD-card
installer environment. It works without a Steam account, a game library or a
sign-in, and it never writes to a disk the user did not confirm.

## What the panel does

- Reads the disk and reports the target, the readable layout
  (`fresh` / `occupied` / `toosmall` / `unavailable`), the capacities and the
  power source.
- **Fresh layout:** pick the Android size with a slider and see the Armada space
  that remains, including the boot partitions. Installing factory-resets Android
  and erases **all** Android user data.
- **Occupied layout:** no slider and no resizing. Every target partition shown is
  erased; Android and its data are kept.
- Review fetches a plan, then the confirmation dialog restates the target,
  capacities and erase/preserve scope from that plan. Cancel (and the B button)
  change nothing. Only the explicit destructive confirm starts the install.
- While installing the panel polls the backend every 1.5 s, shows an
  indeterminate progress bar with the current stage, and says that closing the
  panel does not cancel the job. There is no cancel action.
- On success it explains how to power off, remove the SD card and boot the
  internal disk; powering off always requires pressing the button.
- On failure it tells the user to select the SD card as the boot source in ABL
  and rerun the installer to retry, and notes that **UNINSTALL CFW** in ABL is
  only for abandoning the internal installation, not part of retrying.
- Install is blocked on battery only. An unknown power source does not block:
  review and the confirmation dialog warn to connect the charger.

The language follows the Steam/Decky locale (`zh_CN`, `zh-CN`, `zh-Hans`,
`zh-SG`, `schinese` → Chinese, otherwise English) and can be switched by hand
with the language dropdown, which works before sign-in.

## Backend contract

The UI calls these `@decky/api` methods; every call returns
`{ ok: true, data }` or `{ ok: false, error, detail }`.

| Method | Arguments | Returns |
| --- | --- | --- |
| `get_inspection` | – | `Inspection` |
| `prepare_install` | `android_gib: number \| null` | `Plan` |
| `start_install` | `token: string, confirmed: boolean` | `Status` |
| `get_status` | – | `Status` |
| `power_off` | – | – |

## Development

From `decky/armada-installer`:

```sh
npm ci
npm test         # node --experimental-strip-types --test tests/ui.test.ts
npm run typecheck
npm run build    # rollup -c, emits dist/
```

`src/model.ts` holds the shared decision helpers (size bounds, remaining space,
power gate, block codes, confirmation content) and `src/i18n.ts` holds both
locale dictionaries.

## Packaging

The image build compiles this plugin in the `decky-build` stage and installs the
resulting `dist/` with the other bundled plugins.
