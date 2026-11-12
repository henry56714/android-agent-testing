# 12 自动发现与项目测试接管

本次升级将全量测试的事实来源从“手工登记的 case”扩展为“项目现有测试 + Gradle 实际执行入口 + 用例库”。0.2.0 已实现，验证记录见文末。

## 接入与执行契约

- `aat discover`：查询真实 Gradle 模型，扫描测试源文件和 Maestro flow，报告现有登记、待接管入口和无法识别项。
- `aat discover --apply`：事务保存发现清单、生成的 case/契约与任务配置，保留手工维护的用例。
- 开启接管后，默认 `aat run` 使用 full；每次 full 都重新扫描。清单过期、未登记测试、禁用/过滤测试、未知执行器等会阻止“全量通过”。
- fast/change 或显式 selector 仍可使用，但报告只能是部分测试，不能声称完整项目通过。
- 用例按原生执行入口分组；实际测试方法以原生运行结果枚举。JUnit4/5、参数化/动态测试依赖原生引擎，不通过源码正则猜测最终测试数量。
- 单元测试使用 Gradle 实际 Test 任务；设备全量使用 connected variant 任务，避免只执行已登记的少数 Class#method。Maestro 顶层 flow 自动登记，子 flow 跟随其入口执行。
- 无法自动推断的自定义任务/外部构建/引擎进入未解决清单，需显式映射或记录排除理由；不能将“扫描不到”当成不存在。
- 测试源码仍保留在项目原位置。发现清单和元数据随项目 Git；运行证据留在 .aat。

## 依据

Gradle Test 任务提供运行时 JUnit XML 输出位置，支持 JUnit 和其他通过该任务执行的测试框架：[Gradle 官方测试文档](https://docs.gradle.org/current/userguide/java_testing.html)。Android 设备测试通过 variant 的 connectedAndroidTest 入口执行：[Android 官方命令行测试](https://developer.android.com/studio/test/command-line)。

## 0.2.0 的使用方式

新工程：

```bash
aat init --id my.android.app
aat discover --apply
aat run --device DEVICE_SERIAL
```

已有 0.1.0 配置：先执行一次 `aat discover --apply` 开启接管。此后不带筛选参数的 `run`/`plan` 默认 full，每次自动重扫和同步用例库。原有 Gradle wrapper、源码、断言和手工 metadata 保留。接管不是把业务测试移入工具仓库。

保存位置：

- `testing/discovery.json`：执行入口、测试源文件哈希、入口到 case 的映射、未解决项及排除理由。
- `testing/cases/discovered.*.yaml`：自动登记的原生套件；JUnit 使用 `selector: '*'`，结果列出实际测试方法。
- `testing/contracts/discovered-tests.md`：导入已有断言的契约。
- `android-testing.yaml`：原生执行任务和 discovery 配置。
- `.aat/runs/<id>/`：冻结的清单、原始报告和统一结果。

这些测试源码和配置随被测项目 Git，运行证据继续忽略 Git。同步使用写入日志和排他锁，失败回滚；重复扫描无变化时不重写文件。不再存在的自动入口从当前目录移除，旧 Git 和 run 历史保留。

测试文件新增/修改后，下次默认 run 自动纳入任务资产和快照，并更新受影响的用例 revision。手工用例的断言、selector 和状态不被改写；如果其测试资产变化但 revision 未更新，接管同步只递增版本。被隔离的用例不会自动恢复 active，选中后仍阻止通过。

## 任务与覆盖核对

Gradle 模型枚举实际注册的 `Test` 任务，包括自定义 JVM Test、不同 variant 和 XML 输出位置。文件扫描同时发现标准测试目录、Git 忽略的原生测试、常见其他测试文件与 Maestro flow；Gradle 声明的额外源目录也会扫描。

设备全量通过 `connected<Variant>AndroidTest` 执行，使用选定的 ANDROID_SERIAL 和设备锁，XML 展示实际运行方法。已经完成全套设备测试后，手工登记的精确方法用例复用该结果，避免再执行相同方法。同一个本地 Gradle task 在一次 run 内也只执行一次。

在没有实际测试源码的 Android source set 中，原生空入口记录为 `NO_TEST_SOURCE_IN_VARIANT`；不会伪造测试方法。Java/Kotlin 测试发现由原生运行器完成，Python/JavaScript/C++ 等未接入引擎的测试文件、自定义 verification 任务、复合构建会作为缺口返回，不能默默忽略。

报告中：

- `coverage.scope = full|partial` 表示本次请求范围，不单独代表通过。
- `coverage.entries` 与 `entry_outcomes` 表示发现入口及实际结果。
- `coverage.unresolved` 非空时加入必需的 BLOCKED 项，整体非零退出。
- `coverage.full_project_passed` 只有全量、无未解决项、无人工排除且门禁通过时才为 true。
- `--case`、`--tag`、`--suite`、`--base` 或 fast/change 是显式局部运行；不能和 `--profile full` 混用，也不会报告全项目通过。

源码扫描不等同于语义证明：任意隐藏在脚本中的测试、自定义生成器或项目外构建不能靠文件名穷尽。项目需要把非标准入口映射进配置；当前不宣称能无配置识别所有语言和所有插件。

## 项目特有依赖与扩展

例如 SDK 先复制 AAR，App 再测试：

```yaml
discovery:
  enabled: true
  gradle_argv: [./gradlew]
  task_dependencies:
    ':app:*': [sdk_aar]
```

`task_dependencies` 引用已有 tasks；工具不会凭猜测决定项目分发/签名步骤。已有本地任务的 XML 路径会对齐 Gradle 实际输出，不能用单个类的报告文件冒充整套结果。

非标准入口可通过 `task_cases`（Gradle 完整任务路径 → case ID 列表）和 `source_cases`（项目相对测试源文件 → case ID 列表）显式绑定已有执行器。映射用例必须 active 且 required。自定义执行器须产生目前支持的原生报告格式。

`exclude: [{pattern: '...', reason: '...'}]` 记录经项目决定的范围排除；排除结果仍出现在报告中；全量运行会返回 BLOCKED/退出 3，`full_project_passed` 不会为 true。需要只运行其余部分时显式选择局部范围。Gradle 禁用任务、JUnit 过滤、instrumentation 选类/分片等会被识别为完整性缺口。

## 已验证与边界

覆盖 macOS、Gradle 8.10 / 9.2.1、AGP 8.7.3 / 9.0.1、API 35 ARM64。JUnit4 实际运行，JUnit5/参数化测试通过 Gradle 原生 Test 机制接入；不声称本轮额外验证了所有 JUnit5 插件组合。未知插件、自定义报告路径或生成式测试应检查扫描清单和运行结果后接入。


## 2026-11-12 验证记录

- Python 3.13.12 和 3.11.15：各 101 项通过；Ruff 检查及格式检查通过。
- 回归覆盖：任务导入、幂等同步、新测试自动登记、revision 更新、未知测试阻塞、禁用/过滤检测、子 flow、删除入口、局部运行标记、事务回滚、忽略目录测试、完整报告范围、隔离状态保留、设备结果复用与跨模块隔离、人工排除阻塞、已有文件冲突保护。
- Gradle 8.10 / AGP 8.7.3 公开样例发现 6 个入口：Debug/Release JVM、Debug/Release Lint、完整设备套件和 UI。接管后新增 CounterIsolationTest，不手写 case YAML；默认 run 自动扫描，并在 Debug/Release 报告中均出现 instancesDoNotShareState。
- 源码环境全量 run：`20261112T010352-0f423b865b63`，9 个目录项 PASS，6 个原生入口全部通过，full_project_passed=true。
- 独立 wheel 安装后再次执行全量 run：`20261112T010740-b44290703033`，9 个目录项 PASS，full_project_passed=true；精确设备方法复用了原生套件结果。
- wheel 内包含 Gradle 发现脚本，并通过实际 discover 验证，不依赖源码 checkout 的资源路径。
- 私有多模块 AAR 宿主：发现 8 个入口、9 个测试源文件/flow，40 个 JVM 方法与 4 个设备方法实际执行通过。此前遗漏的设备 context 测试、Release Lint 均已纳入；SDK Lint 和 UI 恢复失败继续阻止全量通过。原始路径、包名和运行证据只保存于该项目。
- 执行时遇到的 UTP 依赖下载 TLS 错误已通过本机代理预取解决，没有将构建错误标成 PASS。

Gradle 8 与 9 的清单来自实际模型查询。CI 配置已保留，远程 CI、真机和所有第三方插件组合仍未验证。未创建 GitHub 远程或发布软件包。
