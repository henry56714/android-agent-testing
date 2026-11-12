# 11 用例生命周期与 Agent 接入

本文描述基础生命周期。0.2.0 的自动发现、默认全量和覆盖门禁以 [12 接管说明](12-discovery-and-takeover.md) 为准。

## 1. 调用边界

自然语言理解在现有 Coding Agent 中完成。本工具提供确定性的 CLI：读取配置/用例、保存、选择、冻结、构建、执行、归档、重放。Agent 的推断不直接变成任意 ADB shell 命令；内部逻辑写成 Android 原生测试，UI 写成 Maestro flow。自定义环境能力使用配置中的 argv 探针，退出 0 才满足能力。

完整流程：需求/diff → 检索旧用例 → 契约和测试 → 原子保存 → 校验 → 冻结 → 原生执行 → 报告 → Git 保存测试源码。文件库是事实来源，不需要后台数据库。所有命令输出 JSON；全局 `--project` 放在子命令前。

## 2. 持久化保存

`aat schema case|project|suite` 输出实际 JSON Schema。现存 schema_version 1 直接兼容，未知版本明确拒绝，不做猜测性迁移。

保存 envelope 示例：

```yaml
case:
  schema_version: 1
  id: counter.increment
  revision: 2
  status: active
  title: Increment exactly once
  layer: local
  tags: [fast]
  contract: testing/contracts/counter.md
  impact:
    paths: [app/src/main/**]
  implementation:
    engine: junit
    task: unit
    selector: example.CounterTest#increments
    path: app/src/test/java/example/CounterTest.java
  timeout_seconds: 30
files:
  testing/contracts/counter.md: |
    Starting at zero, one increment produces exactly one.
```

任务名、selector、路径须替换成实际工程值。`files` 可省略，只允许写入此 case 声明的契约、实现和 assets。API 不创建 Gradle 任务；tasks 需先在项目配置中定义。

```bash
aat --project android-testing.yaml cases save update.yaml --expected-revision 1
aat --project android-testing.yaml cases history counter.increment
aat --project android-testing.yaml cases recover
```

新 case 从 expected 0 / revision 1 开始。写入使用排他锁、before-image journal 和文件原子替换。任一校验失败回滚；进程意外终止时普通读取拒绝半写入目录，recover 恢复。已提交 journal 只移除，不回滚。每次 save 的历史保存到本项目 artifacts/catalog-history；测试源码与 metadata 应提交项目自己的 Git。不要将被工具记录的历史当成跨 clone 的全局版本注册服务。

同一资产被其他 case 引用时，save 拒绝单个 case 改写共享内容。跨 case 原子批量修改尚无 API；可在独占维护窗口内正常编辑文件、同步递增所有受影响 revision，再 validate 和 Git 提交。

## 3. 选择与档位

```yaml
profiles:
  fast: {tags: [fast]}
  change: {tags: [fast, smoke]}
  full: {}
```

```bash
aat run --profile fast
aat plan --base HEAD~1
aat run --case worker.recovers --device DEVICE_SERIAL
```

显式 case/tag/suite/changed 的并集覆盖档位选择；没有 profiles 时无筛选意味着全库。`--base` 同时考虑 Git diff 和未跟踪文件；任一文件未命中 impact.paths 时扩大为全库，避免公共构建变更漏测。没有 diff 时同样选择全库。路径规则只是召回机制，不是静态调用图证明；新需求仍可能需要新增用例。未选中项写入计划 excluded_cases。

## 4. 源码快照与重放

freeze 保存 Git 跟踪文件和未忽略文件、用例资产及 APK-only 输入，并记录 SHA-256。非 Git 目录按文件系统扫描，排除构建/虚拟环境等目录。默认排除 Agent/IDE 配置，可配置更多：

```yaml
snapshot:
  exclude: [private-fixtures/**, local-secrets/**]
  max_bytes: 536870912
```

快照默认限制 512 MiB 源码总量；大工程需显式调整。外部 SDK、依赖缓存、签名密钥不进入可移植性承诺。应避免忽略运行必需的测试 fixture；必要时通过 assets 声明。

```bash
aat replay RUN_ID --destination /new/nonexistent/directory
aat replay RUN_ID --destination /another/new/directory --execute --device DEVICE_SERIAL
```

重放先校验全部快照哈希，在原工程之外创建新目录，恢复当时的 dirty 源码和测试，重写根配置路径，再创建新的 run ID。原工作区与旧结果不改写。工具版本、JDK/SDK/Gradle/依赖应由锁文件及环境记录另行固定；这不是操作系统容器快照。历史证据被 prune 后无法恢复源码，保留的结果仍可读取。

## 5. 能力、日志与保留期

```yaml
capabilities:
  local_fixture:
    argv: [python3, testing/check_fixture.py]
    timeout_seconds: 10
privacy:
  redact_env: [TEST_ACCESS_TOKEN]
  patterns: ['token=[^\s]+']
retention:
  days: 30
  keep_failures: true
```

case 的 requires 可引用 local_fixture；探针失败返回 BLOCKED/CAPABILITY_PROBE_FAILED。探针每次 run 只执行一次，不能将破坏性初始化藏在能力检查中。

每个子进程日志上限 16 MiB，继续排空后续输出并标记 log_truncated，避免日志撑爆磁盘或阻塞子进程。XML 报告、源码、APK、截图不使用此日志上限。脱敏在执行结束时处理执行文本与最终 result；运行中的日志/progress、源码/配置快照、图片和二进制不保证脱敏，原始目录仍按敏感数据管理。

```bash
aat prune                 # 仅预览默认保留策略
aat prune --days 7 --apply # 清理到期证据，保留计划/结果摘要
aat pin RUN_ID            # 保留重要记录
```

默认保留失败与 pin 记录；清理与执行共用排他锁。证据状态标记 expired，不会把缺失证据解释为 PASS。保留期需由 CI/任务调度调用，不启动隐含后台服务。

## 6. 返回值与集成

| 退出码 | 语义 |
|---|---|
| 0 | 选中的必需用例全部 PASS |
| 1 | 断言或 Lint 策略失败 |
| 2 | 配置/输入无效或没有必需测试 |
| 3 | 能力阻塞或跳过 |
| 4 | 执行/报告/证据错误 |
| 130 | 取消 |

状态混合时按 CANCELLED、ERROR、BLOCKED、FAIL、SKIPPED 优先级聚合；不能只查某个 case 的 PASS。CI 上传 result.json、report.md、junit.xml，并按隐私策略选择日志/截图。当前流水线只运行工具自身 Python 测试和打包；Android 环境由接入项目配置。

## 7. 当前验证边界

已覆盖源码 App、Android SDK/AAR 宿主、APK-only、API 33/35 ARM64 模拟器、真实线程 Watchdog/JNI/UI。还没有真机、最低 API 28、Compose/WebView、release/混淆、Windows、设备云或长期压力结果。不自动分析任意动态脚本引用，不自动恢复系统级设置，不执行任意用户自然语言为 shell，也不提供跨机器分布式租约。
