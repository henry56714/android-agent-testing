# 使用本工具的 Agent 工作规范

本文件是可供任意 Coding Agent 读取的项目说明，不是自动安装的 Skill，也不要求专用模型 API。

1. 读取被测项目开发规则、需求、diff 与 `android-testing.yaml`。没有配置时参考 examples 创建配置；不要把样例包名或阈值当成业务默认值。
2. 使用 `aat cases search <关键词>`、`cases list` 查找已有用例；读原生测试和契约，确认适用性。缺口、推断与未确认需求先写明。
3. 用 Gradle 工程原有 JUnit/instrumentation 或 Maestro YAML 生成测试。将 metadata 写入 testing/cases，保存契约及 helper/fixture。需要探索界面时调用已有 Maestro MCP，不依赖本 CLI 解释自然语言。
4. 新用例先 draft；校验、执行器可发现、断言与清理机制验证后再 active。只有 active 用例执行；其他状态返回 BLOCKED 并留在报告里。
5. 测试契约/脚本/helper 改动递增 revision。被测业务源码变化只改变 source fingerprint；不能为让测试通过而修改期望。
6. `cases validate` 后 `plan --suite ...` 冻结；用 `run --run-id ... --device ...` 执行。相关源码/用例变更后创建新计划。直接 `run` 会先生成新计划。
7. `--profile` 当前只是报告标签，选择范围以 `--case/--tag/--suite/--changed` 为准。无过滤时选择整个目录，包括非 active 用例，不能默认当成只运行 active。
8. 每次 run 都是新记录。不要删除旧报告、反复重试到绿或停用失败回归用例。使用 report 读取结果。
9. 报告本次验证范围、失败、阻塞、未覆盖和证据位置。设备未连接、test APK 缺失、测试数量为零、报告缺失都不是通过。
10. 各 App 的源码、密钥、端点、运行证据留在各自项目；不要复制到工具的公共仓库。当前过程日志是原始输出，项目应使用合成数据，分享前检查内容。

当前不支持自动推断所有调用关系。`--changed` 使用 metadata 中声明的路径规则召回用例；Agent 在公共组件/构建变更或分析不确定时扩展范围。
