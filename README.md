# Armada Installer

**English** | [简体中文](README.zh-CN.md)

Decky plugin for [ArmadaOS](https://github.com/armada-os/armada) that installs
Armada onto the internal disk from the SD-card installer environment. It works
without a Steam account, a game library or a sign-in, and it never writes to a
disk the user did not confirm.

This plugin is an installation component for ArmadaOS. For the operating
system, device support, images and the main project documentation, see the
[ArmadaOS repository](https://github.com/armada-os/armada).

## Requirements

- [ArmadaOS](https://github.com/armada-os/armada) and its SD-card installer
  environment.
- A supported ARM64 handheld with Decky Loader available before sign-in.
- Enough internal storage for the selected layout.

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

## Support and community

For ArmadaOS images, device support and system issues, start with the
[ArmadaOS project](https://github.com/armada-os/armada). For plugin-specific
installation or UI issues, use [Issues](https://github.com/mydanyi/armada-installer/issues)
in this repository.

Special thanks to **深圳市退格科技有限公司** for supporting the development,
testing and maintenance of ArmadaOS plugins.

- Sponsorship: [Afdian](https://afdian.com/a/meetmiku)
- QQ fan group: **487945399**
- QQ casual chat group: **477426414**
