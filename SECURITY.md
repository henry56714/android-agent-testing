# Security and private test data

Run only project configurations and test scripts you trust: they intentionally
execute local build/test commands and control the selected Android device.

Keep source snapshots, APKs, test accounts, screenshots and logs in the target
project's ignored `.aat/` directory. Configure `privacy.redact_env` for secret
environment values and `privacy.patterns` for project-specific data. Screenshots,
binary artifacts and source snapshots are not automatically anonymized.

Do not include credentials or private app data in public issues. Before GitHub
publication there is no hosted security reporting channel; after publication,
enable GitHub private vulnerability reporting and use that channel for sensitive
reports. Public issues should contain only synthetic reproductions.
