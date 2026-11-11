# Android Agent Testing

面向 AI Coding Agent 的通用 Android 自动化测试工作流：理解需求与代码变更，复用和持久化测试用例，调用已有 Android 测试工具，并输出可追溯的结果。

**当前状态：0.1.0.dev0 可运行原型。** 已实现文件用例库、计划冻结、任务执行、Gradle/JUnit/Lint、ADB/instrumentation、Maestro 和报告归档；已在两个合成 Android 工程及 APK-only 模式完成 API 35 ARM64 模拟器验证。尚未完成真机、多 API、Compose 和发布验收，未发布 GitHub/PyPI。

使用说明：[快速开始](docs/09-quickstart.md) · [实际验证记录](docs/10-validation-2026-11-11.md) · [Agent 工作规范](AGENT_WORKFLOW.md)。

```bash
uv sync --frozen
uv run pytest -q
uv run aat --project examples/counter-app/android-testing.yaml cases validate
uv run aat --project examples/counter-app/android-testing.yaml doctor
```

需要 Python 3.11+；Android 执行还需要项目对应的 JDK、Gradle、Android SDK，以及 UI 测试所需的 Maestro。测试元数据验证不需要设备；真实执行须先完成快速开始中的环境配置。

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
- [x] 核心元数据、只读检索、计划冻结、原生执行及报告实现
- [x] 工具自身单元/契约测试
- [x] 单模块 App 和 SDK + 宿主工程的模拟器集成验证
- [x] APK-only、模拟器、缺陷注入与阻塞路径验证
- [ ] 真机、多 API、Compose 与持续运行验证
- [ ] 设计中后续能力：元数据写入事务/迁移、复杂适配与完整复现
- [ ] 发布前源码和历史检查、许可证落定
- [ ] GitHub 开源发布

01–08 是总体设计，包含后续目标；09–10 列出当前实际行为、验证与限制，以免将设计目标误认为已实现。
