# 使用本工具的 Agent 工作规范

任何 Coding Agent 都可以读取此文件并调用 CLI；不依赖专用模型、Skill 或 MCP 服务。

1. 读取被测工程规则、需求、diff 和 `android-testing.yaml`。无配置时用 `aat init --id PROJECT` 建立骨架，再补任务/产物；不得把样例包名或阈值套到真实项目。
2. 首次接入执行 `discover --apply`，检查未管理测试和项目特有构建依赖。接管后默认 run 每次自动发现全部原生入口；需要局部回归时显式使用 fast/change 或筛选条件。再用 `cases search`/`cases list`，读取已有契约和原生测试。列出本次行为、失败条件、并发/生命周期边界与可观察性缺口。
3. 使用项目原有 JUnit、AndroidX instrumentation、Maestro YAML 编写测试，不另造测试步骤 DSL。共享 helper/fixture 列入 assets；字面量嵌套 Maestro flow/script 自动收集，动态引用仍须显式声明。
4. 通过 `cases save ENVELOPE --expected-revision N` 原子保存 metadata 和声明资产。新 ID 使用 expected 0、revision 1；更新递增 revision，冲突后重新读取。已存在 schema v1 文件无需迁移；未知 schema 拒绝加载。
5. 初始状态可为 draft。审查断言、可发现性及清理逻辑后以新 revision 激活；只有 active 实际执行，其他状态在选中时报告 BLOCKED。不得为变绿改变期望或移除失败用例。
6. `cases validate` 后 `plan --base REF` 或明确指定用例/档位；`run --run-id ID --device SERIAL` 执行冻结计划。源码或测试有变动须新建计划。未知路径变更保守扩大到全库，仍需 Agent 判断已有测试是否覆盖新需求。
7. profiles 在项目配置中定义；显式 case/tag/suite/changed 取并集并覆盖 profile 的选择。无 profile 配置时默认全库。计划列出未选中项，不能把局部通过当作全量通过。
8. 用 `report ID`、`cases history ID` 检索证据；旧运行不可覆盖。需要复现旧 dirty 源码时 `replay ID --destination NEW_DIR --execute`，外部工具/SDK/依赖需另外提供。
9. 汇报范围、断言、失败、阻塞、环境与缺口。退出 0 仅代表选中必需用例全通过，不代表 Android 所有机型正确。
10. 项目源码、密钥、端点和原始证据留在项目内。配置 privacy 和 snapshot.exclude；脱敏只覆盖执行文本及最终结果，不处理源码快照、图片或二进制。分享前另行检查。prune 默认预览，--apply 才清理；失败与 pin 记录默认保留。

例如线程改动：保存契约，生成边界/上限的本地测试与真实 Watchdog 的设备测试，再补 UI 恢复冒烟。用 gate/latch 控制阻塞，断言真实 worker 身份、替换结果和旧任务清理。已有组件只观察日志而无法断言时，应报告缺口，不能声称测试到所有线程交错。

自动发现行为与覆盖判定以 [12 接管说明](docs/12-discovery-and-takeover.md) 为准。全项目通过必须检查 coverage.full_project_passed，不能仅把局部 gate=passed 当成全量通过。
