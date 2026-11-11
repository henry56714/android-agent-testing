# Android Agent Testing

面向 AI Coding Agent 的通用 Android 自动化测试工作流：理解需求与代码变更，复用和持久化测试用例，调用已有 Android 测试工具，并输出可追溯的结果。

**当前状态：技术设计阶段。没有可安装的软件包，也没有已验证的设备兼容性承诺。** 工作名称暂定 `android-agent-testing`。先完成详细技术文档，再按阶段开发和测试；验证达到发布条件后开源到 GitHub。

## 设计原则

- 直接使用 Maestro CLI/MCP 执行 UI 测试，使用 Gradle、JUnit、AndroidX Test 执行代码检查与逻辑测试。
- 第一版不实现新的 UI 驱动、自定义测试脚本语言、LLM 服务、数据库或 MCP 服务。
- 自然语言由使用者现有的 Coding Agent 理解，生成标准测试文件；固定回归用例可以不经过 LLM 直接重放。
- 每次产生的可复用用例立即保存，先检索再新增；文件和 Git 是用例库的事实来源。
- 通用工具独立于被测项目。包名、模块、flavor、variant、构建步骤、服务端及探针均通过项目配置适配。
- 报告区分失败、未执行、环境异常和覆盖缺口，不承诺证明所有并发交错或所有设备正确。

## 技术文档

按以下顺序阅读：

1. [需求、范围与技术选择](docs/01-scope-and-decisions.md)
2. [总体架构与执行流程](docs/02-architecture.md)
3. [项目适配配置与能力边界](docs/03-project-adapter.md)
4. [持久化用例库与版本模型](docs/04-case-library.md)
5. [Agent 工作流与 CLI 契约](docs/05-agent-and-cli.md)
6. [开发计划、测试矩阵与验收](docs/06-development-and-validation.md)
7. [GitHub 开源发布准备](docs/07-open-source-release.md)
8. [上游来源与调研边界](docs/08-references.md)

## 三种接入方式

| 模式 | 可以验证 | 需要提供 |
|---|---|---|
| 有 Android 源码 | 静态检查、本地单元测试、设备逻辑、UI | 工程路径、任务映射、包名、测试入口 |
| 只有 APK | UI 和可观察的端到端行为 | APK、包名、设备与业务预期 |
| SDK/AAR | SDK 本地测试、宿主集成、对外行为 | SDK 构建、测试宿主与依赖关系 |

源码模式第一阶段面向 Java/Kotlin Gradle 工程；View/Compose 的 UI 能力取决于控件可访问性及实际验证。Flutter、React Native 等可先按 APK 模式接入，源码构建与测试适配器后续独立验证。

本仓库只保存通用文档、后续工具源码、合成示例及工具自身测试。各 App 的用例、配置、源码和运行证据保存在 App 仓库或其专用测试仓库，不汇总到公共仓库。

## 进度

- [x] 通用技术设计与独立仓库结构
- [ ] 文档规定的数据格式及核心模块实现
- [ ] 工具自身单元/契约测试
- [ ] 至少两个不同 Android 工程的集成验证
- [ ] APK-only、模拟器、真机与失败路径验证
- [ ] 发布前源码和历史检查、许可证落定
- [ ] GitHub 开源发布

文档中的配置、命令和目录均为目标设计，除明确注明外尚未实现。
