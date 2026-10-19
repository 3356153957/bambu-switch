# 参考与打包依赖

本项目独立编写 Windows 实现，没有复制两个 macOS 项目的源码或图形资源。
参考了下列项目公开说明中的独立配置、切换登录状态、按账号创建快捷入口的思路。

| 项目 | 作者 | 页面显示 Star | 核对日期 | 参考范围 |
| --- | --- | --- | --- | --- |
| [bamboo-switch](https://github.com/c0ryd/bamboo-switch) | c0ryd | 3 | 2026-10-19 | 独立配置与切换流程；上游为 MIT |
| [bambu-account-switcher](https://github.com/Shotarrow2003/bambu-account-switcher) | Shotarrow2003 | 4 | 2026-10-19 | 按账号创建桌面入口的体验 |

Star 为当次取得的页面显示值，可能有缓存延迟。
本项目与 Bambu Lab、以上项目作者均无隶属关系。

Windows EXE 由 PyInstaller 生成，包含 CPython 与 Tcl/Tk 运行库。
它们分别遵循 PSF License、Tcl/Tk License，以及 PyInstaller GPL 与 Bootloader exception。
PyInstaller 的 exception 允许分发打包应用；本项目源码采用 MIT。
发布包的 licenses 目录包含构建使用的 Python LICENSE.txt、Tcl/Tk license.terms
以及 PyInstaller 许可证。构建脚本会自动复制这些文本。

## 拓竹图标

按项目需求，界面、程序与快捷方式使用 Bambu Studio 随附的官方图标。
`assets/bambu-logo.png` 与 `assets/BambuStudio.ico` 分别取自 Studio 的
`resources/web/image/logo.png` 和 `resources/images/BambuStudio.ico`，没有修改图形。
上游为 [bambulab/BambuStudio](https://github.com/bambulab/BambuStudio)，
原仓库许可全文保留在 `assets/BambuStudio-LICENSE.txt`。
本项目 MIT 许可仅覆盖自行编写的代码与文档；官方标识的权利仍归原权利人所有。
