# Armada Installer

**English** | [简体中文](README.zh-CN.md)

**Download the latest plugin ZIP:**
[armada-installer releases](https://github.com/mydanyi/armada-installer/releases/latest)

Decky plugin for [ArmadaOS](https://github.com/armada-os/armada) that installs
Armada onto the internal disk from the SD-card installer environment, and that
can also create an Armada game library in the Linux reserve partition of a TF
card while keeping the Android storage on that card. Both modes work without a Steam account, a game
library or a sign-in, and neither writes to a disk the user did not confirm.

This plugin is an installation component for ArmadaOS. For the operating
system, device support, images and the main project documentation, see the
[ArmadaOS repository](https://github.com/armada-os/armada).

## Requirements

- [ArmadaOS](https://github.com/armada-os/armada) and its SD-card installer
  environment.
- A supported ARM64 handheld with Decky Loader available before sign-in.
- Enough internal storage for the selected layout.

## What the panel does

- Reads the disk only when you press **Detect disk** and reports the target, the
  readable layout (`fresh` / `occupied` / `toosmall` / `unavailable`), the
  capacities and the power source. Nothing is inspected on load, and the panel
  first asks you to connect the charger.
- The system install, TF card game library and settings are separate icon tabs
  at the top of the panel, with the plugin version shown next to the title on
  every tab. The system install tab is selected first.
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
- A fresh allocation requires a confirmed charger and a successful explicit
  **Detect disk** first. On battery, or when the power source is unknown, the
  size slider stays hidden; the existing occupied-layout installation keeps its
  usual behaviour. Detecting the same disk and partition table again keeps the
  size you already chose.

The language follows the Steam/Decky locale (`zh_CN`, `zh-CN`, `zh-Hans`,
`zh-SG`, `schinese` → Chinese, otherwise English) and can also be switched by
hand with the language selector in the Settings tab, which works before
sign-in.

## TF card game library

Besides installing Armada on the internal disk, the panel can turn a TF card
into an Armada game library. The two features are separate icon tabs at the top
of the panel; **System installation** is the default tab, and the TF card game
library is the second one. The third tab lists language, author and version, in that order.

1. **On Android first.** Use the
   [Smart TF Card Allocator (智能TF卡分配器)](https://github.com/mydanyi/smart-tf-allocator)
   app ([APK download](https://github.com/mydanyi/smart-tf-allocator/releases/latest))
   to allocate the TF card. The Android app allocates and initializes the Android
   storage; the Linux plugin only formats and mounts the Linux reserve partition
   as ext4 and connects it to the Steam library, so the Android storage is kept.
   The app lays out the Android metadata/private partitions and the Linux reserve
   partition; a fresh allocation erases all existing data on the card. The app
   can run through Shizuku for a rootless workflow — check the app for its
   supported Android/Shizuku version.
2. **Then the Armada Installer entry.** Insert the card into the handheld, open
   the Armada Installer panel, choose **TF card game library**, rescan and select
   the card.
3. **Linux formats only the reserve partition.** The panel formats **only** the
   Linux reserve partition (p1) to ext4 and connects it to Steam. The Android
   metadata and private partitions (p2/p3) and their data are preserved.

- A card that already holds an Armada library is mounted and reconnected to
  Steam without formatting, and an existing ext4 card is never automatically
  reformatted.
- If a conversion leaves the reserve partition retyped but not yet formatted,
  the panel asks for the same erase confirmation again and resumes the format.
- Before formatting, the panel shows exactly what is erased: only the Linux
  reserve partition, with the card, p1 and the Android/Linux sizes, while p2/p3
  are preserved. Nothing is written on load, rescan, card selection or review.
- While working, the panel shows an indeterminate progress bar with the real
  stage and says that closing the panel does not cancel the job. It never claims
  a percentage.
- On success it distinguishes **connected to the Steam library** from **mounted,
  waiting for Steam**, with a retry button that never formats.

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
| `get_tf_inspection` | – | `{ cards }` |
| `prepare_tf_library` | `device: string` | `Plan` |
| `start_tf_library` | `token: string, confirmed: boolean` | `Status` |
| `get_tf_status` | – | `Status` |

## Development

From `decky/armada-installer`:

```sh
npm ci
npm test         # node --experimental-strip-types --test tests/ui.test.ts
npm run typecheck
npm run build    # rollup -c, emits dist/
```

`src/model.ts` holds the shared decision helpers (size bounds, remaining space,
power gate, block codes, confirmation content, allocation visibility and size
retention) and `src/i18n.ts` holds both locale dictionaries.
`src/PluginContent.tsx` holds the icon tab strip and the settings tab, while
`src/TfLibraryContent.tsx` with `src/tf_model.ts`, `src/tf_types.ts`,
`src/tf_backend.ts` and `src/tf_i18n.ts` holds the TF card game library.

## Packaging

The image build compiles this plugin in the `decky-build` stage and installs the
resulting `dist/` with the other bundled plugins.

## License

Armada Installer is licensed under the [GNU General Public License v3.0 or
later](LICENSE). ArmadaOS and its bundled components may carry separate
licenses; see the [ArmadaOS license information](https://github.com/armada-os/armada/blob/main/LICENSE.md).

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
