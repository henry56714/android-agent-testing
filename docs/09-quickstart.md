# 09 当前原型使用说明

## 1. 安装与自测

面向 macOS/Linux；本次主机实测为 macOS，Linux 尚未实测。Python 3.11+；自测覆盖 Python 3.11.15 与 3.13.12，依赖版本由 `uv.lock` 固定。

```bash
uv sync --frozen
uv run ruff check src tests
uv run pytest -q
uv build
```

也可在自己的虚拟环境 `pip install .` 安装 `aat`。包未发布 PyPI，不要使用 `pip install android-agent-testing` 假设会安装本项目。

所有全局参数放在子命令前：`aat --project path/to/android-testing.yaml --json <command>`。当前无论是否带 `--json` 均输出 JSON，方便 Agent 调用。

## 2. Android 环境

合成样例使用 AGP 8.7.3、compile/targetSdk 35、minSdk 28、JUnit 4.13.2、AndroidX runner 1.6.2。本次验证 Gradle 8.10、JDK 21、Android API 35 ARM64、Maestro 2.12.1。

样例的 `gradle.sh` 调用 `GRADLE_BIN` 或 PATH 中的 `gradle`，避免复制其他项目的 wrapper。使用者安装 Gradle 8.10 并配置 Android SDK：

```bash
export ANDROID_HOME=/path/to/android-sdk
export GRADLE_BIN=/path/to/gradle-8.10/bin/gradle
export PATH="$ANDROID_HOME/platform-tools:/path/to/maestro/bin:$PATH"
export MAESTRO_CLI_NO_ANALYTICS=1
export MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED=true
adb devices -l
```

`/path/to/...` 是需替换的示例。真实项目优先在 argv 中调用它自己的 `./gradlew`，不强制使用样例 Gradle 版本。

Maestro 从其官方发布/安装说明获取；Android SDK 需安装匹配平台与 build-tools。工具本身不静默下载 SDK 或模拟器。设备序列号必须选择实际 `adb devices` 列出的设备，多个设备时不指定则阻塞。

## 3. 两个源码样例

```bash
uv run aat --project examples/counter-app/android-testing.yaml cases list
uv run aat --project examples/counter-app/android-testing.yaml cases search counter
uv run aat --project examples/counter-app/android-testing.yaml cases validate
uv run aat --project examples/counter-app/android-testing.yaml doctor

# 本地 JUnit + Lint
uv run aat --project examples/counter-app/android-testing.yaml run --tag fast

# 替换 DEVICE_SERIAL：设备测试会构建并安装合成 App/test APK
uv run aat --project examples/counter-app/android-testing.yaml run --suite full --device DEVICE_SERIAL

# 第二种结构：sdk library + host application，显式构建 DAG
uv run aat --project examples/worker-sdk/android-testing.yaml run --device DEVICE_SERIAL
```

两个样例均为本仓库自编 Java/View 工程，不代表 Compose 已验证；SDK 样例使用 project dependency，不代表 AAR 手动复制或发布仓库场景已经实测。

## 4. APK-only

先构建 counter-app 的 APK，再复制到独立黑盒测试目录。输入 APK 不提交 Git。

```bash
mkdir -p examples/apk-only/input
cp examples/counter-app/app/build/outputs/apk/debug/app-debug.apk examples/apk-only/input/app.apk
uv run aat --project examples/apk-only/android-testing.yaml run --tag smoke --device DEVICE_SERIAL
```

不带 `--tag smoke` 会同时选择一个故意缺 instrumentation 能力的内部用例。预期 UI PASS、内部用例 BLOCKED、整体退出码 3；这是能力边界验证，不是预期全绿的套件。

## 5. 计划、历史与版本

```bash
uv run aat --project examples/counter-app/android-testing.yaml plan --case counter.increment
# 把输出中的 run_id 代入后续命令
uv run aat --project examples/counter-app/android-testing.yaml run --run-id RUN_ID
uv run aat --project examples/counter-app/android-testing.yaml report RUN_ID
```

计划保存到 `<project>/.aat/runs/<run-id>/plan.json`。执行后生成 `result.json`、`report.md`、`junit.xml`；证据保存在 tasks/cases/products 下。JSON 的 evidence_manifest 记录证据哈希和大小。失败/重试使用新 run ID，原记录不覆盖。

冻结时复制显式声明的测试资产及 APK-only 输入，登记源码指纹。执行前检查配置、源码和资产未变化；执行后发现源码变化，原 PASS 降为 ERROR。历史重建仍需要原始源码/工具版本，本版不会自动 checkout、迁移依赖或恢复完整 dirty 源码。

metadata 原子写入工具尚未实现：由 Agent/开发者以普通文件方式维护并提交 Git，`cases validate` 校验；目录库不依赖数据库。冻结时在本地 registry 检查“同 revision 内容变化”。不同 clone 没有共享 registry，跨分支变更仍需代码评审/Git 对比。

## 6. 当前准确的能力边界

| 能力 | 当前行为 |
|---|---|
| 配置路径 | root 相对配置文件；catalog/assets/products 相对 root。当前拒绝跨 root 资产 |
| native tests | JUnit XML、Lint XML；instrumentation 要求精确 Class#method |
| 案例依赖 | helper/fixture/子 flow 手工列 assets；不自动分析动态脚本依赖 |
| 构建 | 显式 DAG；单 APK；拒绝 split/AAB 的隐式选择 |
| 时间限制 | 本地任务使用 task timeout；Maestro/instrumentation 使用 case timeout |
| 生命周期 | draft/quarantined/deprecated 选择后 BLOCKED；不自动提升 active |
| 能力声明 | 内建 device/ui_accessibility；其他 requires 返回 BLOCKED，尚无探针插件注册 |
| 参数选择 | `--case/--tag/--changed/--suite` 为并集；profile 只作标签 |
| 设备互斥 | 同主机文件锁，串行控制；不支持跨主机租约/设备云 |
| 清理 | 结束时 force-stop 本次安装的测试包与 App，释放锁；不自动卸载或清数据，不修改系统设置 |
| 安装身份 | aapt 校验包名、安装不可变 APK 副本、检查包已安装；不声称读回并验证设备所有 split 内容 |
| 日志 | 本地原始输出；尚无通用敏感信息脱敏和磁盘配额管理 |
| 中断 | SIGINT/SIGTERM 持久化 CANCELLED；无法捕获 kill -9，report 标记 INCOMPLETE |
| 重放 | 同输入可新建计划重放；旧 run 不覆盖，不自动执行快照中的旧源码 |

release/混淆、真实后台恢复、网络故障、JNI/OEM、最低 API、多 API、真机、Compose 与长时间稳定性仍需按总体设计继续验证。当前不承诺发布级完备性。
