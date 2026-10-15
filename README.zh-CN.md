# Armada Installer

[English](README.md) | **简体中文**

Armada Installer 是一个面向 [ArmadaOS](https://github.com/armada-os/armada)
的 Decky 插件，用于从 SD 卡安装环境把 Armada 安装到掌机内部存储空间。
插件不要求登录 Steam 账号，也不要求已有游戏库；只有用户明确确认后，才会写入选定的磁盘。

Armada Installer 是 ArmadaOS 的安装组件，不是独立的系统发行版。系统、设备支持、镜像和主项目文档请参考
[ArmadaOS 主项目](https://github.com/armada-os/armada)。

## 使用条件

- 已准备好 [ArmadaOS](https://github.com/armada-os/armada) 及其 SD 卡安装环境。
- 支持 ARM64 的掌机，并且在登录 Steam 前可以打开 Decky Loader。
- 内部存储空间满足所选安装布局的要求。

## 插件功能

- 读取内部磁盘并显示目标磁盘、分区布局（`fresh` / `occupied` / `toosmall` / `unavailable`）、容量和供电状态。
- **全新布局（Fresh）：** 使用滑块选择 Android 保留空间，同时显示包含启动分区在内的 Armada 可用空间。安装会恢复出厂 Android，并清除全部 Android 用户数据。
- **已有布局（Occupied）：** 不提供调整滑块；会清除目标分区，但保留 Android 及其用户数据。
- 查看安装方案后，确认窗口会再次列出目标磁盘、容量以及清除／保留范围。取消操作和手柄 B 键都不会写入磁盘，只有明确确认后才会开始安装。
- 安装过程中每 1.5 秒读取一次状态，显示当前阶段和进度提示。关闭插件窗口不会取消安装，界面不提供取消操作。
- 安装成功后提示关机、拔出 SD 卡并从内部存储启动；关机仍需用户按下电源键。
- 安装失败后提示在 ABL 中选择 SD 卡作为启动源并重新运行安装器；ABL 中的 **UNINSTALL CFW** 只用于放弃内部安装，不是重试步骤。
- 仅在电池供电时阻止安装。无法识别供电状态时仍可查看方案，但会提醒连接充电器。

插件语言跟随 Steam／Decky 区域设置（`zh_CN`、`zh-CN`、`zh-Hans`、`zh-SG`、`schinese` 显示中文，其余显示英文），也可以在登录前通过语言下拉菜单手动切换。

## TF 卡游戏库

除了把 Armada 安装到内部存储，插件还可以把 TF 卡转换为 Armada 游戏库。两种模式通过面板顶部的模式选择器切换，默认是**系统安装**，第二项是 **TF卡游戏库**。

1. **先在 Android 上操作。** 使用「智能TF卡分配器」应用完成 TF 卡分配。它会划分 Android 元数据／私有分区和 Linux 保留分区；全新分配会清除卡上已有的全部数据。该应用可通过 Shizuku 以免 root 方式运行，具体支持的 Android／Shizuku 版本请以应用说明为准。
2. **再使用 Armada Installer 入口。** 把卡插入掌机，打开 Armada Installer 面板，选择 **TF卡游戏库**，重新扫描并选择该卡。
3. **Linux 仅格式化保留分区。** 面板**只**把 Linux 保留分区（p1）格式化为 ext4 并接入 Steam；Android 的元数据与私有分区（p2/p3）及其数据会保留。

- 已经承载 Armada 游戏库的卡只会挂载并重新接入 Steam，不会格式化；已有的 ext4 卡也不会被自动重新格式化。
- 如果转换在中途中断，保留分区只改了类型还没格式化，面板会再次要求相同的擦除确认，然后继续格式化。
- 格式化前，面板会明确列出擦除范围：仅 Linux 保留分区，并显示 TF 卡、p1 以及 Android／Linux 容量，同时说明 p2/p3 保留。加载、重新扫描、选择卡片和查看方案都不会写入任何数据。
- 处理过程中面板显示不确定进度条和真实阶段，并提示关闭面板不会取消任务，不会声称百分比。
- 成功后区分**已接入 Steam 游戏库**与**已挂载、等待 Steam**，并提供永不格式化的重试接入按钮。

## 后端接口

界面通过 `@decky/api` 调用以下方法，每个方法都会返回
`{ ok: true, data }` 或 `{ ok: false, error, detail }`。

| 方法 | 参数 | 返回值 |
| --- | --- | --- |
| `get_inspection` | 无 | `Inspection` |
| `prepare_install` | `android_gib: number \| null` | `Plan` |
| `start_install` | `token: string, confirmed: boolean` | `Status` |
| `get_status` | 无 | `Status` |
| `power_off` | 无 | 无 |
| `get_tf_inspection` | 无 | `{ cards }` |
| `prepare_tf_library` | `device: string` | `Plan` |
| `start_tf_library` | `token: string, confirmed: boolean` | `Status` |
| `get_tf_status` | 无 | `Status` |

## 开发

在 `decky/armada-installer` 目录执行：

```sh
npm ci
npm test         # node --experimental-strip-types --test tests/ui.test.ts
npm run typecheck
npm run build    # rollup -c，输出 dist/
```

`src/model.ts` 保存尺寸范围、剩余空间、供电限制、阻止原因和确认内容等决策逻辑；`src/i18n.ts` 保存中英文文案。`src/PluginContent.tsx` 保存模式选择器，`src/TfLibraryContent.tsx` 连同 `src/tf_model.ts`、`src/tf_types.ts`、`src/tf_backend.ts`、`src/tf_i18n.ts` 保存 TF 卡游戏库。

## 打包

系统镜像会在 `decky-build` 阶段编译插件，并把生成的 `dist/` 与其他 Decky 插件一起安装。

## 许可证

Armada Installer 使用 [GNU 通用公共许可证第 3 版或更高版本](LICENSE)
（GPL-3.0-or-later）。ArmadaOS 及其内置组件可能使用各自的许可证，详见
[ArmadaOS 许可证说明](https://github.com/armada-os/armada/blob/main/LICENSE.md)。

## 支持与交流

ArmadaOS 镜像、设备支持和系统问题请先参考
[ArmadaOS 主项目](https://github.com/armada-os/armada)。插件安装或界面问题请在本仓库提交
[Issue](https://github.com/mydanyi/armada-installer/issues)。

特别感谢 **深圳市退格科技有限公司** 对 ArmadaOS 插件开发、测试和维护的支持。

- 赞助：[爱发电](https://afdian.com/a/meetmiku)
- QQ 粉丝群：**487945399**
- QQ 交流二群：**477426414**
