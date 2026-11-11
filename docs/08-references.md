# 08 上游来源与调研边界

调研日期：2026-11-11。本方案基于官方资料与仓库说明；尚未对执行引擎进行本地性能基准或设备兼容性测试。版本与 API 以实施时锁定组合为准。

| 来源 | 本方案使用的依据 |
|---|---|
| [Maestro GitHub](https://github.com/mobile-dev-inc/Maestro) | 原生 YAML UI flow、Android 真机/模拟器、CLI 内置 MCP、Apache-2.0；首选 UI 引擎 |
| [Maestro MCP 文档](https://docs.maestro.dev/getting-started/maestro-mcp) | Agent 通过已有 MCP 接口操作设备；不需要先自建 MCP |
| [旧 maestro-mcp 仓库](https://github.com/mobile-dev-inc/maestro-mcp) | 已归档，不作为新框架依赖基础 |
| [Android 本地测试](https://developer.android.com/training/testing/local-tests) | JVM 单元测试与 Android framework 限制；returnDefaultValues 的风险 |
| [Android 命令行测试](https://developer.android.com/studio/test/command-line) | Gradle/设备 instrumentation 执行路径 |
| [Android Lint](https://developer.android.com/studio/write/lint) | 静态检查能力 |
| [Android UI Automator](https://developer.android.com/training/testing/other-components/ui-automator) | 系统与跨应用 UI、进程外设备测试；可作能力补充 |
| [Robolectric](https://robolectric.org/) | 依赖 Android framework 的本地测试选择，不代替真机 |
| [Appium UiAutomator2](https://github.com/appium/appium-uiautomator2-driver) | 原生/混合应用黑盒驱动，可作为以后适配器；需锁定驱动/服务版本组合 |
| [openatx/uiautomator2](https://github.com/openatx/uiautomator2) | Python Android 控制候选，与 Appium 驱动是不同项目 |
| [Airtest](https://github.com/AirtestProject/Airtest) | 图像/游戏自动化候选 |
| [Mobile MCP](https://github.com/mobile-next/mobile-mcp) | 通用设备操作接口候选；不替代测试契约和持久化资产 |

选择 Maestro 是针对“现有 Coding Agent + 可重放 UI 测试”的工程判断，并非经过性能实验得出的全行业最佳排名。线程正确性、业务副作用和项目内部状态的验证方式是本方案的设计建议，需要后续实现与验收。
