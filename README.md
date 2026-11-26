# Android Agent Testing

面向 Coding Agent 的 Android 测试 CLI：管理可复用用例，调用项目已有的 Gradle/JUnit/Lint、ADB instrumentation 和 Maestro，并保存计划、源码快照与执行证据。自然语言由调用方 Agent 理解，固定回归无需模型服务。

当前代码版本 **0.3.0**，配置 schema 为 **1**。源码托管于 [GitHub](https://github.com/henry56714/android-agent-testing)，当前为私有仓库，需要仓库访问权限；尚未发布软件包。实现、已知问题和整改方案集中在[技术方案](docs/technical-design.md)。

## 安装与首次检查

需要 Python 3.11+ 和 uv。通过已认证的 GitHub 账号克隆仓库后执行，不需要 Android 设备；已有本地仓库可跳过前两行：

```bash
git clone https://github.com/henry56714/android-agent-testing.git
cd android-agent-testing
uv sync --frozen
uv run aat --help
uv run aat --project examples/counter-app/android-testing.yaml cases validate
uv run pytest -q
```

在其他 Android 工程中使用时，可在自己的 Python 虚拟环境执行 `python -m pip install /absolute/path/to/android-agent-testing`，之后直接调用 `aat`。不要假定 PyPI 上的同名包就是本仓库。

所有全局参数放在子命令前：`aat --project /path/to/android-testing.yaml --json <command>`。默认读取当前目录的 `android-testing.yaml`。默认返回统一 JSON envelope：`schema_version`、`ok`、`exit_code`、`data`、`error`；命令行语法错误也返回 JSON。原始业务结果位于 `data`（例如 `data.run_id`）。`--format text` 输出摘要，`--format legacy-json` 保留 0.2 的输出形状；`--json` 明确选择新版 JSON。帮助信息仍为文本。

## Android 环境与公开样例

Android 执行需要项目对应的 JDK、Gradle、Android SDK；UI 测试另需 Maestro。现有代码使用 POSIX 锁和进程组，面向 macOS/Linux，Windows 不可直接运行。历史设备验证使用 macOS、JDK 21、API 33/35 ARM64 模拟器；不代表其他组合已验证。

两个公开源码样例使用 AGP 8.7.3、compile/target SDK 35、min SDK 28。它们的 `gradle.sh` 调用 `GRADLE_BIN` 或 PATH 中的 `gradle`，没有内置 Gradle Wrapper；历史验证使用 Gradle 8.10、Maestro 2.12.1。真实项目优先用自己的 Wrapper。

```bash
# 将 /path/to/... 替换为本机实际安装位置
export ANDROID_HOME=/path/to/android-sdk
export GRADLE_BIN=/path/to/gradle-8.10/bin/gradle
export PATH="$ANDROID_HOME/platform-tools:/path/to/maestro/bin:$PATH"
export MAESTRO_CLI_NO_ANALYTICS=1
export MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED=true
adb devices -l

uv run aat --project examples/counter-app/android-testing.yaml doctor --strict --layer local
# 本地 JUnit + Lint，不要求设备
uv run aat --project examples/counter-app/android-testing.yaml run --tag fast
# 首次或升级后，检查并同步发现结果
uv run aat --project examples/counter-app/android-testing.yaml discover
uv run aat --project examples/counter-app/android-testing.yaml discover --apply
# 替换 DEVICE_SERIAL；会构建、安装并执行测试
uv run aat --project examples/counter-app/android-testing.yaml run --device DEVICE_SERIAL
```

`doctor` 默认是信息模式；CI 使用 `doctor --strict`，必要能力缺失返回 3。可用 `--layer local`、`--layer ui` 等限定诊断范围；不需要设备的本地测试不会因缺少 ADB/Maestro 而阻塞。结果中的 `ready`、`issues` 和修复提示用于定位缺项，不代表业务测试通过。工具不自动安装 SDK、Maestro 或模拟器。多设备时须显式传入 serial 或设置配置指定的环境变量（默认 `ANDROID_SERIAL`）。

| 接入模式 | 配置示例 | 注意事项 |
|---|---|---|
| 源码 App | [counter-app](examples/counter-app/android-testing.yaml) | 已开启自动发现；默认重扫并检查同步状态 |
| SDK + 宿主 | [worker-sdk](examples/worker-sdk/android-testing.yaml) | SDK → host 的构建 DAG；当前默认运行手工登记的用例，接管前先执行 `discover --apply` |
| APK-only | [apk-only](examples/apk-only/android-testing.yaml) | 需要先提供 APK；只验证可观察的黑盒行为 |

```bash
uv run aat --project examples/worker-sdk/android-testing.yaml run --device DEVICE_SERIAL

# counter-app 构建出 APK 后，再运行独立黑盒样例
mkdir -p examples/apk-only/input
cp examples/counter-app/app/build/outputs/apk/debug/app-debug.apk examples/apk-only/input/app.apk
uv run aat --project examples/apk-only/android-testing.yaml run --tag smoke --device DEVICE_SERIAL
```

APK-only 样例去掉 `--tag smoke` 会选中一个故意缺少 instrumentation 能力的用例；预期 UI PASS、内部用例 BLOCKED、退出 3。它用于验证能力边界。

## 接入自己的项目

1. 在被测工程根目录执行 `aat init --id my.android.app`。APK-only 使用 `aat init --id my.blackbox --mode apk --apk /path/to/app.apk --application-id example.app`；显式输入的 APK 会复制到项目内。已有配置不要重复 init。
2. init 自动补充 `.aat/` 忽略规则并保留原 `.gitignore` 内容；APK 模板还会忽略输入 APK。配置中的 `project.root` 相对配置文件，其余项目路径相对解析后的项目根目录；常规接入保持 `root: .`。
3. 检查 Wrapper；必要时设置 `tools.gradle_wrapper` 或 `discovery.gradle_argv`。需要 UI/直接 instrumentation 时，参照样例补齐包名、构建任务、APK 路径和 runner；init 不会生成这些内容。
4. 先 `aat discover` 查看入口与 gaps，再 `aat discover --apply` 保存接管结果。两者都会执行项目 Gradle 配置和发现任务；`--apply` 还会改写项目配置、发现清单及用例元数据。
5. 执行 `aat cases validate`、`aat doctor --strict --device DEVICE_SERIAL`，处理缺口后运行 `aat run --device DEVICE_SERIAL`。

设备安装要求单 APK；split APK/AAB 由项目构建流程先转换。SDK/AAR 的复制、发布到本地仓库、宿主构建等步骤须显式声明，工具不会猜测：

```yaml
discovery:
  enabled: true
  gradle_argv: [./gradlew]
  task_dependencies:
    ':host:*': [sdk_build] # sdk_build 必须已经在 tasks 中定义
```

自定义 verification 任务和非标准测试文件分别通过 `discovery.task_cases`、`discovery.source_cases` 映射到已有 active、required 用例。`discovery.exclude` 必须写明 `pattern` 和 `reason`；排除会阻止全项目通过。`snapshot.exclude` 只控制存储，排除的测试仍被审计并阻止全量通过，私有测试内容不写入快照。实际任务模型无法提供源目录/报告路径时，配置 `discovery.task_sources` / `task_reports`（任务完整路径 → 路径列表），不能靠猜测通过。

## 日常命令

以下命令在被测工程根目录执行，也可统一在子命令前添加 `--project`。

```bash
aat cases list
aat cases show CASE_ID
aat cases search keyword
aat cases validate
aat schema project       # 另有 case、suite；以实际 schema 为准

aat plan --case CASE_ID  # 从 JSON 的 data.run_id 取得运行 ID
aat run --run-id RUN_ID --device DEVICE_SERIAL
aat report RUN_ID
aat cases history CASE_ID

aat run --profile fast
aat plan --base HEAD~1
aat run --tag smoke --case CASE_ID --device DEVICE_SERIAL
aat run --profile full --device DEVICE_SERIAL
aat run --sync --device DEVICE_SERIAL # 明确授权同步发现结果后再执行
```

- 开启 discovery 后，无筛选的 plan/run 默认 full。**plan 只写运行计划/快照，不修改用例库和项目配置**；发现未同步变更时返回 `DISCOVERY_OUT_OF_SYNC`，先检查 `discover` 的逐文件 diff，再 `discover --apply`，或显式 `run --sync`。未开启时默认 change，无档位配置则选择全库。
- 显式 `--case`、`--tag`、`--suite`、`--changed` 取并集并覆盖 profile 选择；不能和显式 `--profile full` 合用。`--base` 加入 Git diff 与未跟踪文件，未映射变更或空 diff 会扩大到全库。`--changed PATH` 也会对未映射路径保守扩大范围。
- `full` 总是选择全库，不使用 `profiles.full` 的筛选项；未开启 discovery 的 full 仍没有原生入口完整性审计。局部筛选只限制目录项，同一 Gradle task 仍执行其原生套件。
- 计划冻结源码、配置和测试资产；后续修改会导致 `STALE_PLAN`，需新建计划。已启动 run 不可再次执行，失败重试使用新 ID；`run --run-id` 拒绝 profile、筛选和 sync 参数。
- `aat build --run-id RUN_ID` 可单独构建并记录产物，但当前 build 与 run 的任务缓存不跨进程共享；通常直接 run 即可。

## 保存和维护用例

测试仍写在项目原生 JUnit/AndroidX/Maestro 文件中；元数据描述契约、执行入口与适用范围。新增或更新通过 envelope 保存：

```yaml
case:
  schema_version: 1
  id: my.counter.increment
  revision: 1
  status: draft
  title: 一次递增只增加一
  layer: local
  contract: testing/contracts/counter.md
  implementation:
    engine: junit
    task: unit
    selector: example.CounterTest#increments
    path: app/src/test/java/example/CounterTest.java
  timeout_seconds: 30
files:
  testing/contracts/counter.md: |
    初始值为零，调用一次 increment 后必须恰好为一。
```

这是格式示例：`unit` 必须已在 tasks 定义，测试路径和 selector 须替换为真实值。保存为 `update.yaml` 后：

```bash
aat cases save update.yaml --expected-revision 0
aat cases validate
# 仅在发现未完成的写入事务时恢复
aat cases recover
```

新用例使用 expected 0 / revision 1；更新读取当前 revision N，再以 expected N / revision N+1 保存。`files` 可省略，只能改写该用例声明的契约、实现或 assets。共享 helper/fixture 放入 assets；源码业务实现不属于测试资产。Maestro 字面量子 flow/script 会自动收集，动态依赖须另行声明。

只有 active 用例执行；选中的 draft/quarantined/deprecated 均 BLOCKED，后两者还需 reason。确认契约、断言和清理后再升 revision 激活；不得为了通过而删除失败断言。共享资产使用 `aat cases save-batch updates.yaml`：文件顶层是 `{envelope: {case: ..., files: ...}, expected_revision: N}` 对象的列表，必须包含全部受影响用例，整体成功或回滚。手工保存和自动发现均记录版本、来源、原因及资产摘要；旧版本缺失历史显示 `history_complete: false`，不会补造记录。测试文件仍应提交项目自己的 Git。

## 结果、重放与清理

默认结果位于 `<project>/.aat/runs/<run-id>/`：`plan.json`、`result.json`、`report.md`、`junit.xml`，以及 source/snapshot/tasks/cases/products 等证据目录。JSON 中的 `evidence_manifest` 记录关联证据哈希。

| run 退出码 | 含义 |
|---|---|
| 0 | 选中的必需用例全部 PASS |
| 1 | 断言或 Lint 策略失败 |
| 2 | 输入/配置无效或无必需用例 |
| 3 | BLOCKED 或必需项 SKIPPED |
| 4 | 执行、报告或证据错误 |
| 130 | 取消 |

混合状态按 CANCELLED → ERROR → BLOCKED → FAIL → SKIPPED 聚合。只读命令返回 0 不等于产品测试通过。`coverage.scope` 区分 full/partial；全项目验收还应检查 `coverage.full_project_passed`、unresolved/excluded 和原生报告。这个标志表示已发现并纳入审计的原生入口通过，不能证明业务语义完整或穷尽任意隐藏脚本。

```bash
aat replay RUN_ID --destination /new/nonexistent/directory
aat replay RUN_ID --destination /another/new/directory --execute --device DEVICE_SERIAL
aat prune                 # 默认预览，通常为 30 天
aat prune --days 7 --apply
aat pin RUN_ID
```

重放目录必须尚不存在且在原项目之外；恢复源码后仍需对应 JDK/SDK/Gradle/依赖与环境变量。prune 默认保留失败和 pin 记录，保留结果摘要，删除到期证据后不能再重放。中断运行可能只显示 INCOMPLETE，不自动恢复执行。

## Agent、隐私与维护约定

Agent 应先读项目规则、需求和 diff，再检索已有用例，补充契约与原生测试，保存/校验，执行明确范围，最后汇报通过、失败、阻塞和未覆盖行为。不要把 UI 可见结果当成内部线程正确性的证明，也不要将局部通过表述为完整项目通过。

仅执行可信项目的配置和测试脚本。将源码、APK、账户、日志和截图保存在被测项目内；公共问题反馈只使用合成复现。配置示例：

```yaml
privacy:
  redact_env: [TEST_ACCESS_TOKEN]
  patterns: ['token=[^\s]+']
snapshot:
  exclude: [local-secrets/**] # 仅用于不应进入快照的数据，不应用来缩小测试范围
  max_bytes: 536870912
retention:
  days: 30
  keep_failures: true
```

脱敏处理执行文本和最终结果，**不会使整个运行目录可公开分享**：源码/配置快照、图片和二进制仍可能包含原文；progress 与结果在落盘前脱敏，执行日志在结束时处理。只分享逐项检查过的文件。设备结束时 force-stop 本次涉及的 App/test 包，不自动卸载或恢复系统设置；未实现的 `device_policy` 字段设为 true 会明确拒绝。各引擎统一检查 `requires: [device]`；`ui_accessibility` 必须提供同名项目探针，退出 0 才满足能力。

开发验证：

```bash
uv sync --frozen
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv run pytest -q
uv build
```

自测与证据可以一条命令重建；输出目录必须尚不存在，避免覆盖旧证据：

```bash
uv run python tools/selftest.py --output .aat/verification/my-check
# 已准备好 Android 工具链与专用测试设备时：
uv run python tools/selftest.py --output .aat/verification/my-android-check --device DEVICE_SERIAL
```

脚本保存命令/退出码/日志、pytest JUnit XML、独立 wheel 安装与运行记录、源码哈希、源码归档、性能测量和证据 SHA-256 清单。带设备时从公开样例创建副本，验证源码 App、SDK 宿主、APK-only 和负向场景，原生 XML/日志保存在副本的 `.aat/runs/`。CI 核心矩阵自动运行；Android 工作流独立手动触发，未触发不算已验证。

0.2 升级须注意：JSON 业务数据移至 `data`；自动同步改为显式操作。旧调用方可暂用 `--format legacy-json`，旧 CI 可配置 `discovery.auto_sync: true` 兼容 run 并获得迁移提示；它不会使 plan 修改用例库。配置/result schema 仍为 1，旧报告可读取。

通用核心不写入客户包名、目录或业务阈值。修改 CLI/schema/结果语义时同步这两份文档；设备或发现逻辑变更还需实际 Android 验证。问题反馈应包含工具/引擎版本、系统/API/ABI、合成配置、复现步骤与 reason_code。当前没有已配置的远程私密漏洞报告渠道，不公开提交凭证或私有数据。

自有代码采用 [Apache-2.0](LICENSE)，声明见 [NOTICE](NOTICE)，依赖许可证保存在 [third_party](third_party/)，清单见[技术方案](docs/technical-design.md#licenses)。项目说明文档只维护本 README 和技术方案；示例中的 `contract.md` 是被用例引用的运行输入，GitHub 问题模板和许可证文件也继续保留。
