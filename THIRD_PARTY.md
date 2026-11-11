# Third-party components

Runtime Python dependencies are installed separately; exact versions are in `uv.lock`.
License texts are retained in `third_party/` based on the installed distributions.

| Component | Version tested | License |
|---|---|---|
| PyYAML | 6.0.3 | MIT |
| jsonschema | 4.26.0 | MIT |
| attrs | 26.1.0 | MIT |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |

Maestro, Gradle, Android SDK, JDK, JUnit and AndroidX are external execution/build
dependencies, not vendored binaries. Their own licenses and installation terms apply.
Synthetic Android examples declare their Gradle dependencies in source. Development
tools pytest, Ruff and Hatchling are locked in the development/build environment.

The Apache 2.0 license text in LICENSE comes from https://www.apache.org/licenses/LICENSE-2.0.txt.
