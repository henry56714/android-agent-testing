# 03 项目适配配置与能力边界

## 1. 配置位置

默认在被测项目根目录保存 `android-testing.yaml` 与 `testing/`；APK-only 模式可建独立测试仓库。工具仓库的路径不是解析基准，配置中的路径相对配置文件所在目录。

下面是 v1 目标格式示例，尚未提供解析器，不能作为已经可执行的配置使用。只描述编排与元数据，不替代原生测试语言。

```yaml
schema_version: 1
project:
  id: sample.todo
  mode: source
  root: .
catalog: testing/cases
suites: testing/suites
artifacts: .aat/runs
tools:
  gradle_wrapper: ./gradlew
  adb: adb
  maestro: maestro
target:
  application_id: org.example.todo.debug
  variant: demoDebug
  device:
    serial_env: ANDROID_SERIAL
    min_api: 28
    required_abis: []
tasks:
  build:
    depends_on: []
    argv: [./gradlew, ':mobile:assembleDemoDebug', ':mobile:assembleDemoDebugAndroidTest']
    timeout_seconds: 900
  local_tests:
    depends_on: []
    argv: [./gradlew, ':core:testDebugUnitTest']
    reports: ['core/build/test-results/testDebugUnitTest/*.xml']
    timeout_seconds: 300
  lint:
    depends_on: []
    argv: [./gradlew, ':mobile:lintDemoDebug']
    reports: ['mobile/build/reports/lint-results-demoDebug.xml']
    timeout_seconds: 300
products:
  app:
    from_task: build
    path: mobile/build/outputs/apk/demo/debug/mobile-demo-debug.apk
  instrumentation:
    from_task: build
    path: mobile/build/outputs/apk/androidTest/demo/debug/mobile-demo-debug-androidTest.apk
instrumentation:
  test_application_id: org.example.todo.debug.test
  runner: androidx.test.runner.AndroidJUnitRunner
environment:
  references: [TEST_SERVER_URL]
device_policy:
  allow_clear_test_package: false
  restore_changed_settings: true
```

配置中的 module、API、超时、runner 和输出路径只属于这个合成示例。接入时由 Agent 检查真实工程，不能照抄成默认值。

## 2. 配置验证

- 未知 schema 主版本拒绝；未知字段报错，避免拼写错误静默生效。
- task ID 唯一、依赖存在、DAG 无环、timeout 为正数且有上限策略。
- argv 是字符串数组，默认不经过 shell。项目需要复杂脚本时显式调用版本管理的脚本路径。
- 产物精确定位。首期要求单 APK；多匹配拒绝。split APK/AAB 转换后续增加明确 adapter，不自动选择目录里最新文件。
- 结果路径在运行开始前隔离/标记，结束后确认属于本次任务。
- 配置可包含命令，按代码信任边界处理。打开第三方配置不会自动执行；执行仅发生在显式 run/build 操作。
- 路径规范化并检查 symlink；默认资产位于项目内。跨仓库资产需显式允许的根目录及来源指纹。
- 环境变量缺失报缺能力，密钥值不写入配置或普通报告。
- serial 选择明确；多设备时未指定目标则报 BLOCKED，不任选一台。

## 3. 不同工程接入

| 工程形态 | 项目适配责任 |
|---|---|
| 单模块 App | 提供任务、APK、包名、测试入口 |
| 多模块/多 flavor | 显式声明目标 variant 与所有相关任务/报告 |
| SDK + 宿主 | 将 SDK 构建、AAR 复制/依赖发布、宿主构建串为 DAG |
| View | 优先 resource-id/contentDescription 定位 |
| Compose | 验证可访问语义；必要时项目增加测试语义或使用 Compose 设备测试 |
| JNI/Native | ABI 对齐、真实加载验证；native 覆盖单独统计 |
| APK-only | 指定输入 APK；没有 Gradle 能力；只选择可观察黑盒用例 |
| Flutter/RN APK | 可先跑黑盒；源码任务接入不意味着已验证整个生态 |

APK-only 配置省略 source tasks，使用 `project.mode: apk` 与 `products.app.input_path`；记录输入 APK 哈希与 package metadata。需要 instrumentation 的 case 在缺 test APK/runner 时 BLOCKED，不能自行伪造内部测试能力。

## 4. 设备逻辑扩展

Service、WorkManager、JobScheduler、数据库、线程池等采用项目原生测试库。工具只运行 selector、收集结果。项目可配置自己的故障注入 fixture；跨进程事件协议作为以后可选扩展，不要求所有 App 集成测试 SDK。

如果加入探针，仅在测试变体构建；发布产物验证不含测试导出组件。权限授予/撤回、后台限制和网络操作均需 capability check。force-stop、进程回收、Activity 重建各自建 case，不混用恢复预期。

## 5. 接入完成条件

doctor 能给出工具与能力清单；构建节点可以准确产出目标 APK；至少一个原生逻辑测试和一个 UI flow 可映射到 catalog；同一份用例在新会话重放；能力不足/错误包名/设备断连能得到非 PASS 结果。
