# Android Agent Testing

面向 AI Coding Agent 的通用 Android 测试工具：把自然语言需求与代码变更转为可复用的原生测试，持久化用例，执行静态检查、单元测试、设备测试与 UI 回归，并保存可核验的结果。

**版本 0.2.0，已完成本地开发和模拟器自测，尚未发布 GitHub/PyPI。** 使用 Apache-2.0 许可证。实际验证范围及应用缺陷见[验证记录](docs/10-validation-2026-11-11.md)，不将未测设备写成支持承诺。

[快速开始](docs/09-quickstart.md) · [完整操作与配置](docs/11-lifecycle-and-agent-integration.md) · [自动发现与全量接管](docs/12-discovery-and-takeover.md) · [Agent 工作规范](AGENT_WORKFLOW.md) · [许可证](LICENSE)

```bash
uv sync --frozen
uv run pytest -q
uv run aat --project examples/counter-app/android-testing.yaml cases validate
uv run aat --project examples/counter-app/android-testing.yaml doctor
```

需要 Python 3.11+、macOS/Linux；Android 执行还需要项目对应的 JDK、Gradle、Android SDK，以及 UI 测试使用的 Maestro。macOS 已实测，Linux 已配置 CI，尚未运行远程 CI。

## 为什么在 Maestro 之上增加这一层

Maestro 可以直接承担 UI 自动化。本工具复用它的 YAML 和 CLI，不再开发 UI 驱动。额外处理的是跨层测试：Gradle/JUnit/Lint、设备 instrumentation、SDK/AAR 宿主构建，以及用例版本、变更选择、不可变计划、源码快照和证据保留。自然语言由现有 Coding Agent 理解；固定回归不需要 LLM，也不需要自建模型服务。

## 已实现

- 全量接管：扫描 Gradle 原生任务、已有测试源码和 Maestro flow，自动登记；默认全量重扫，未映射测试阻止通过。

- 文件 + Git 用例库：检索、严格校验、事务保存、revision 冲突检测、历史与中断恢复。
- 项目适配：源码 App、SDK + 宿主、APK-only；显式构建 DAG，包名/产物/探针通过配置定义。
- 测试选择：用例、标签、套件、fast/change/full 档位、Git diff；未映射变更扩大到全库。
- 原生执行：JUnit、Lint、ADB instrumentation、Maestro；超时/取消、设备互斥和清理。
- 可追溯运行：源码与资产快照、APK 哈希、JSON/Markdown/JUnit 报告、源码恢复与执行重放。
- 证据管理：可配置文本脱敏、进程日志大小限制、保留期清理和固定重要运行。
- 故障不会伪通过：零测试、缺报告、旧报告、能力缺失、设备异常分别记录并返回非零状态。

## 三种接入方式

| 模式 | 可以验证 | 需要提供 |
|---|---|---|
| Android 源码 | Lint、本地单元测试、设备逻辑、UI | 工程、任务、产物、测试入口 |
| APK-only | UI 与外部可观察行为 | APK、包名、业务预期 |
| SDK/AAR | SDK 本地测试、宿主集成、设备行为 | SDK 构建、宿主及任务依赖 |

本仓库包含两个独立自编 Java/View 示例和 APK-only 配置。实际私有 App 的源码、用例、数据和运行证据始终保存在该 App 项目中。

## 技术文档

01–08 记录总体设计与长期目标；09–11 定义基础行为；12 定义 0.2.0 自动发现与全量接管。

1. [范围与选型](docs/01-scope-and-decisions.md)
2. [架构与执行流程](docs/02-architecture.md)
3. [项目适配](docs/03-project-adapter.md)
4. [持久化用例库](docs/04-case-library.md)
5. [Agent 与 CLI](docs/05-agent-and-cli.md)
6. [开发与验收](docs/06-development-and-validation.md)
7. [开源准备](docs/07-open-source-release.md)
8. [上游调研](docs/08-references.md)

真机/OEM、Compose/WebView、release/混淆、Windows、设备云以及长期压力验证不属于本次已经完成的测试矩阵。工具不承诺穷尽线程调度或自动证明所有业务行为。
