# Changelog

## 0.1.0 — 2026-11-11 (local release candidate)

- Persistent YAML case catalog, transactional revision saves, recovery and history.
- Project configuration for source apps, SDK/AAR hosts and APK-only testing.
- Native JUnit/Lint, ADB instrumentation and Maestro execution; explicit build DAG.
- Profile and conservative Git-diff selection, source/asset snapshots and replay.
- Structured outcomes, immutable run records, hash-indexed evidence, retention and pinning.
- Configurable capability probes, text redaction, bounded process logs and cancellation cleanup.
- Two self-authored Android samples, Python regression tests and CI configuration.
- Local macOS validation on Android API 33/35 ARM64 emulators; see docs/10 for receipts and limits.

No GitHub or PyPI publication has been performed. Schema version is 1; existing v1
metadata remains valid. Legacy runs without source snapshots remain readable but
cannot use source restoration. True devices, Compose, release builds and Linux
execution remain outside the verified matrix.
