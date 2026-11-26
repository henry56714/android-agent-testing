# Android Agent Testing 技术方案与整改验收

更新：2026-11-12。当前版本 **0.3.0**，项目/用例/结果 schema 仍为 1。审查基线为 `da8296a`（0.2.0）。使用说明统一见 [README](../README.md)。

“技术方案最右”按“技术方案最优”理解：适合当前规模、测试结论可靠、操作可预测、维护边界明确。原审查的 10 项已完成整改与本地自测；远程 CI、真机和未列出的引擎组合不计入已验证范围。

## 1. 选型与系统边界

保留 Python 薄 CLI、原生测试引擎和文件/Git 用例库。没有引入数据库、常驻服务、模型网关、Web 管理台或新的测试 DSL。

| 选择 | 依据与边界 |
|---|---|
| Python 3.11+ | 负责配置、编排、文件事务与证据；当前使用 POSIX 锁和进程组，Windows 不可直接运行 |
| Gradle/JUnit/Lint | 使用工程原有任务、断言和实际报告，不强制迁移框架版本 |
| ADB instrumentation、Maestro | 复用设备与 UI 执行能力；APK 黑盒无法证明内部线程正确性 |
| YAML + 原生测试 + Git | 元数据可审查，原生测试保留在项目内；本机运行历史不是跨机器版本注册中心 |
| 调用方 Coding Agent | 负责理解需求、检索/维护测试和解释缺口；固定回归无需模型 |

通用工具负责选择、执行和证据。被测工程负责契约、包名、variant、构建依赖、fixture、断言及观察能力。项目数据留在项目内，运行证据默认进入 `.aat/runs/`。

```mermaid
flowchart LR
    A[Agent 与原生测试] --> B[项目配置和用例库]
    B --> C[发现与差异预览]
    C --> D[显式事务同步]
    D --> E[只读选择与计划冻结]
    E --> F[原生引擎执行]
    F --> G[结果与证据]
    G --> H[报告和历史重放]
```

## 2. 模块与执行契约

| 模块 | 职责 |
|---|---|
| [cli.py](../src/aat/cli.py)、[bootstrap.py](../src/aat/bootstrap.py) | 参数、输出 envelope、source/APK 接入模板与操作分派 |
| [schema.py](../src/aat/schema.py)、[project.py](../src/aat/project.py) | 严格字段、路径、任务 DAG、用例与资产依赖校验 |
| [catalog.py](../src/aat/catalog.py)、[transaction.py](../src/aat/transaction.py) | 单个/批量保存、统一事务、revision 历史与恢复 |
| [discovery_model.py](../src/aat/discovery_model.py)、[discover.gradle](../src/aat/resources/discover.gradle) | 查询真实 Gradle 任务、variant/source set 和报告位置 |
| [discovery_scan.py](../src/aat/discovery_scan.py)、[discovery_sync.py](../src/aat/discovery_sync.py) | 独立审计测试范围，计算差异，显式同步；discovery.py 保留公共入口 |
| [selection.py](../src/aat/selection.py)、[planner.py](../src/aat/planner.py)、[source.py](../src/aat/source.py) | 选择、源码指纹、冻结/校验计划；不隐式改写用例库 |
| [runner.py](../src/aat/runner.py)、[engines.py](../src/aat/engines.py) | Session 生命周期、构建/安装/清理，五种原生引擎的能力与结果协议 |
| [process.py](../src/aat/process.py)、[device.py](../src/aat/device.py) | 有界进程与短查询、取消/超时、设备和分层环境诊断 |
| [results.py](../src/aat/results.py)、[lifecycle.py](../src/aat/lifecycle.py)、[storage.py](../src/aat/storage.py) | 解析/门禁、报告、恢复、脱敏/保留期、原子文件与锁 |

### 配置与用例

`project.root` 相对配置文件，其他项目路径相对解析后的根目录，resolve 后检查边界。任务使用 argv 数组、依赖与 timeout。配置和用例拒绝未知字段/schema、重复 YAML key、DAG 环及非法 selector。实际格式通过 `aat schema project|case|suite` 获取，业务数据位于返回的 `data`。

层次 static/local/device/ui 与引擎 lint/junit/gradle-device/instrumentation/maestro 分离。直接 instrumentation 要求精确 `Class#method`；JUnit 按原生 XML 匹配 selector。仅 active 执行，其余选中状态阻塞。手工资产缺失时拒绝加载；自动条目可先重扫退休，冻结仍检查全部所需资产。

### 发现、同步和历史

发现查询实际 Gradle Test XML 位置、Android connected task 的 resultsDir、Lint report task 的 XML 输出，以及 Android variant/JVM class directories 对应的 source set。记录模型版本、Gradle/AGP 版本、报告/源码来源；不能取得模型时产生明确缺口。项目可通过 task_sources/task_reports 显式适配，自定义执行入口通过 task_cases/source_cases 映射。

`discover` 输出逐文件 diff 和 needs_sync；`discover --apply` 执行统一事务。未修改的配置顶层区段保持原字节，变更区段重新序列化，其注释/格式变化会出现在 diff 中。生成契约由工具维护，业务契约应独立保存。full plan/run 每次重扫；需同步时明确拒绝沿用旧库，run 可用显式 `--sync`。plan 只写运行目录。

save、save-batch、discovery 共用执行锁、目录锁、before-image journal 和历史事件。提交前写恢复日志，异常回滚项目文件和历史文件；兼容旧 journal。每次 revision 记录来源、原因、前版本、资产摘要；退休和重新发现保留单调版本。旧缺失历史标记不完整，不伪造。共享资产必须批量包含全部受影响用例。

### 计划、执行和证据

计划保存配置哈希、源码指纹、case revision、资产哈希和发现清单。执行前后检查输入；变化、旧报告、零测试、缺报告或未执行的 required 用例不能通过。显式筛选限制目录项，原生 task 仍执行其自身套件；同一 Session 缓存 task 结果，精确设备方法可复用已完成的匹配套件。

所有引擎先检查 requires；device 缺失阻塞，ui_accessibility 需要同名项目探针。未实现的设备策略不能设为 true。设备锁在同一用户/主机内按 serial 生效，不是分布式租约；结束时停止涉及的 App/test 包并释放锁，不恢复完整系统状态。

结果为 PASS/FAIL/BLOCKED/ERROR/SKIPPED/CANCELLED；只有 required 用例参与 gate。完整退出码见 README。full_project_passed 表示纳入发现审计的全部入口通过且无未解决项或人工排除，不代表业务语义覆盖率或穷尽隐藏脚本。

progress/result 在写入前脱敏，执行文本在结束时清理，之后计算证据哈希。源码/配置快照、APK、图片仍是私有证据。prune 删除到期证据及 progress，保留摘要并标记 expired；失败和 pin 默认保留。硬终止后的 INCOMPLETE 记录不自动重跑。

源码默认限 512 MiB，单进程日志限 16 MiB；XML、图片和 APK 不受日志限额保护。非 Git 扫描剪枝，单次发现复用文件哈希，冻结复用已验证源码哈希。source/snapshot 中相同文件只在**同一次运行的冻结副本之间**建立硬链接，失败则复制；不链接活跃工作区，不改变执行前后检查和恢复校验。

## 3. 十项整改对照

基线问题与对应修复如下。P1 优先解决结论/证据正确性，P2 处理接口与维护，P3 依据实测优化。

<a id="issue-01"></a>
### 01 · P1 · 快照排除绕过审计：已修复

基线将 snapshot.exclude 同时用于发现，未管理测试可消失且误报 full pass。现在扫描范围独立，私有测试记为 SNAPSHOT_EXCLUDED_TEST，依赖私有子 flow 的入口也不可通过；冻结拒绝存储被排除的测试资产。

证据：[test_remediation.py](../tests/test_remediation.py) 的文件/目录/Maestro 排除回归；基线 8 项负向回归全部失败，修复后通过。

<a id="issue-02"></a>
### 02 · P1 · 能力与策略静默无效：已修复

基线 local requires=device 不触发检查，device_policy 可接受却不执行。现在各引擎统一检查，缺失设备或项目探针返回 BLOCKED；未实现策略设 true 在配置阶段拒绝，false 保持兼容。探针配置也拒绝无效的 device/dependency/report 字段。

证据：local 缺设备、缺 ui_accessibility 探针、两个设备策略拒绝用例；真实 APK-only 内部测试与不可用设备分别得到预期阻塞。

<a id="issue-03"></a>
### 03 · P1 · 已完成 progress 遗留原文：已修复

基线只清理最终 result，progress 永久保留未脱敏数据。现在使用统一 Redactor，在每次进度与最终结果落盘前清理，并在证据过期时移除 progress。

证据：正常完成、执行异常和取消三条路径检查 progress/result/Markdown/JUnit 中均无合成秘密；已有证据哈希、脱敏与取消回归继续通过。

### 04 · P2 · 预览、同步、执行混杂：已修复

分离 scan/propose/commit，discover 展示 diff，plan 不修改版本管理文件。未同步变更显式报错；run --sync 或兼容 auto_sync 完成事务后再冻结。兼容选项带迁移提示，不影响 plan 的只读约束。

证据：预览与 stale plan 前后文件逐字节比较、同步幂等、配置首部注释保留；真实 Android 冻结前后项目文件指纹一致。

### 05 · P2 · 重复事务与缺失历史：已修复

统一 CatalogTransaction，同时处理业务文件和历史事件。增加 save-batch 支持共享资产；历史覆盖自动导入、修改、退休和重新发现，revision 单调递增。

证据：历史写入失败整体回滚、原有写文件失败/中断恢复、共享资产批量冲突与成功、自动 revision 1→2、退休后恢复版本；已有 0.2 journal 恢复测试保留。

### 06 · P2 · 接入与 CLI 契约不一致：已修复

init 提供 source/apk 模板、缺项清单、ignore 合并与覆盖保护，change 默认不引用不存在的 smoke 标签。doctor 按层检查工具、命令、APK/runner、设备、环境与探针，strict 不就绪退出 3。统一 JSON 成功/错误 envelope，另有 text 和 legacy-json；冻结计划拒绝 profile/sync/筛选冲突。--changed 与 --base 均有未映射变更的保守回退。

证据：解析错误 JSON、未知 profile、冻结参数拒绝、无 Android 工具的 local 就绪、缺设备 strict 非零、APK 初始化保护；独立 wheel 在新目录 init→run→report 通过。

### 07 · P2 · 大模块与适配推断：已修复

拆出 planner/source/selection、engine adapters、discovery model/scan/sync 和事务模块；保持简单函数与 Session，未引入插件框架。execute/query 共用超时和进程组处理。Android 报告位置与源码归属来自实际模型，不能识别时要求显式配置，保留旧 inventory 的兼容路径。

证据：模型未知时报缺口、显式路径覆盖、variant 源码不靠子串混入、query 超时；真实 Gradle 8.10 / AGP 8.7.3 查询与完整执行通过。尚不据此承诺所有 AGP 版本组合。

### 08 · P2 · 多份矛盾文档：已完成

18 份说明文档收敛为 README 和本文。使用操作、当前实现、升级行为、整改结果和证据分工明确。保留四份运行契约、GitHub 问题模板、LICENSE/NOTICE/第三方许可证；这些属于执行、仓库功能或分发资产。

证据：[validate_docs.py](../tools/validate_docs.py) 检查文档数量、链接、示例语法/schema、公开用例资产和工作流 YAML。完整旧文档由 Git 历史保留。

### 09 · P2 · 自动验证不足：已完成

增加针对性回归、独立 wheel 安装与实际运行、自测证据记录器及原生矩阵脚本。核心 CI 包含格式检查、测试、文档、打包和基准；Android 工作流独立手动触发，并上传日志/XML/摘要。

证据：[selftest.py](../tools/selftest.py)、[package_smoke.py](../tools/package_smoke.py)、[native_selftest.py](../tools/native_selftest.py)、[核心 CI](../.github/workflows/tests.yml)、[Android CI](../.github/workflows/android.yml)。CI 配置尚未远程执行；本轮在本机执行了相同脚本。

### 10 · P3 · 重复扫描与复制：已优化并测量

非 Git 遍历跳过构建/缓存目录，发现不再先哈希整棵源码后重哈希测试，freeze 复用源码 manifest，并对同 run 冻结副本去重。

[benchmark_snapshot.py](../tools/benchmark_snapshot.py) 固定生成 256 个测试资产（16 MiB）和 2,000 个忽略的构建文件。首次前后测量：copy2 复制字节 **33,568,794 → 16,784,676**；run 唯一 inode 字节 **33,630,547 → 16,846,429**；freeze **0.216 → 0.194 秒**。这是本机合成样本，耗时受缓存/系统负载影响，不承诺所有项目同等提速。

证据：benchmark-before/after 日志、冻结副本不与活跃源码共享 inode、源码变化与快照损坏的既有回归。

## 4. 0.2 → 0.3 兼容与维护规则

| 变化 | 迁移方式 |
|---|---|
| 默认 JSON 增加 envelope | 读取 data.run_id/data.gate 等；旧消费者暂用 --format legacy-json |
| 默认不自动同步元数据 | discover → 审查 diff → discover --apply；或 run --sync |
| 旧 CI 依赖自动同步 | discovery.auto_sync: true 兼容 run 并输出迁移提示；plan 始终不改用例库 |
| 原先无效的设备策略 | 删除 true 配置；不可再假定自动恢复系统状态 |
| ui_accessibility 能力 | 提供同名 argv/timeout 探针，或按实际契约移除不成立的要求 |
| 新发现模型的清单内容 | 升级后执行一次 discover --apply，同步来源与版本信息 |

项目/用例/result schema 不变，旧结果只读可查；旧 journal 可恢复。新增 JSON envelope 的 schema 与结果本体独立。没有把旧失败结果改成成功，也没有为缺失历史生成虚构版本。后续改接口必须同时更新两份文档与相应契约测试。

## 5. 本次自测与证据

本机证据根目录：`.aat/verification/20261112-remediation/`，不进入 Git。最终完整自测位于其 `final/`；可按 README 的 selftest 命令在新目录复跑。每项记录 argv、cwd、起止时间、退出码、预期退出码、日志 SHA-256；最终保存源码清单/归档和证据清单。

| 验证 | 结果与证据位置（相对证据根目录） |
|---|---|
| 原版本已有测试 | 102 项通过；baseline-core.json / .log / .junit.xml |
| 修复前负向回归 | 8 项全部失败，证明确实触发缺陷；baseline-regressions.json / .log / .junit.xml |
| 整改后 Python | 132 项回归；最终 core.junit.xml、core.json；Python 3.11 的额外记录为 python311.junit.xml |
| 静态检查与文档 | 最终 ruff/format/docs 的 JSON 记录和原始日志 |
| 独立 wheel | 在新虚拟环境、源码树之外读取 schema、资源、init→本地 run→report；package/summary.json |
| 真实 Android | 独立 wheel 的 13 个检查；native/summary.json、各检查日志及 projects/*/.aat/runs/* 原生 XML/过程日志 |
| 性能 | benchmark-before.log、benchmark-after.log；最终另存 benchmark.log |

首轮独立 wheel 的真实运行证据位于 `native-wheel-02/`，代表性结果如下；最终脚本会生成新的 run ID，不覆盖这些记录。

| 场景 | 实际结果 | 首轮 Run ID |
|---|---|---|
| counter-app 全量 | 9 个目录用例、6 个发现入口通过；full_project_passed=true | `20261112T063044-63ab0631e70b` |
| worker-sdk 全量 | 10 个目录用例通过；full_project_passed=true | `20261112T063118-d2eee335a69c` |
| APK-only UI | PASS，退出 0，partial | `20261112T063203-14e1fd91e827` |
| APK-only 内部能力 | UI PASS、内部 BLOCKED，退出 3 | `20261112T063213-a4da4e2f9b66` |
| 注入一次加二的实现缺陷 | FAIL，退出 1；恢复源码后同一测试通过 | `20261112T063222-f58a2ba324e9` / `20261112T063223-f81ea0a51dac` |
| 指定不存在的设备 | BLOCKED，退出 3；并非运行中物理拔线测试 | `20261112T063224-fca77a23d4a2` |
| 指向不存在的原生 XML | ERROR / REPORT_MISSING，退出 4 | `20261112T063224-e9e1ca2a5470` |

环境：macOS、JDK 21、Gradle 8.10、AGP 8.7.3、API 35 ARM64 专用模拟器、Maestro 2.12.1。没有改动用户的 Android 业务工程，所有原生验证在公开样例副本执行。首次 native-installed-wheel 尝试因自测脚本错误解引用虚拟环境 Python 软链接而失败，已修正；原失败记录保留，后续完整检查通过。

边界：未执行远程 CI、Linux 主机、真机/OEM、最低 API 28、Compose/WebView、release APK/混淆、所有 JUnit5/AGP 组合、设备云或长期压力。Release JVM/Lint 通过不等于 release APK 设备验证。早期 API 33 和私有工程记录留在原 Git 文档历史，本次没有把它们当作重新验证的证据。

## 6. 许可与外部依据

自有代码、文档和合成样例采用 [Apache-2.0](../LICENSE)，声明见 [NOTICE](../NOTICE)。运行依赖单独安装，以 [uv.lock](../uv.lock) 为准。

<a id="licenses"></a>
| 组件 | 当前锁定版本 | 保留的许可文本 |
|---|---|---|
| PyYAML | 6.0.3 | [MIT](../third_party/PyYAML-LICENSE.txt) |
| jsonschema | 4.26.0 | [MIT](../third_party/jsonschema-COPYING.txt) |
| attrs | 26.1.0 | [MIT](../third_party/attrs-LICENSE.txt) |
| referencing | 0.37.0 | [MIT](../third_party/referencing-COPYING.txt) |
| rpds-py | 2026.9.1 | [MIT](../third_party/rpds-py-LICENSE.txt) |
| jsonschema-specifications | 2025.9.1 | [MIT](../third_party/jsonschema-specifications-COPYING.txt) |

Maestro、Gradle、Android SDK、JDK、JUnit、AndroidX 为外部执行/构建依赖；不在工具包中分发其 CLI 二进制。pytest/Ruff 为开发依赖，Hatchling 为构建后端。本文只说明仓库现有许可文件，不承诺上游未来版本。

Android 工作流的配置依据为 [emulator-runner 官方仓库](https://github.com/ReactiveCircus/android-emulator-runner)、[Gradle Actions 官方仓库](https://github.com/gradle/actions) 和 [Maestro 2.12.1 官方发布](https://github.com/mobile-dev-inc/Maestro/releases/tag/cli-2.12.1)；Maestro ZIP 固定版本并校验 SHA-256。运行事实以上面的本机证据为准，不将工作流配置等同于远程执行成功。

运行流水由机器报告保存，项目只维护 README 与本文；不再新增独立阶段报告或重复使用指南。2026-11-12 整改自测时未创建远程仓库；2026-11-26 已将源码推送至 [GitHub 私有仓库](https://github.com/henry56714/android-agent-testing)，尚未发布软件包。远程 CI 的实际状态以 [Actions 运行记录](https://github.com/henry56714/android-agent-testing/actions)为准。
