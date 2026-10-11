simee-kicad: simee's KiCad fork; this `simee-ci` branch holds only simee's packaging tooling. README.md has how releases are built.

- tests: `uv run pytest`; targeted tests (simee-agents#87; `$SIMEE_CHAIN_CMD tests` says whether the chain runs targeted tests or full suites): `uv run pytest tests/<file>` / `-k <name>` for what you changed; this repo is public, so its own GitHub CI (`.github/workflows/test.yml`) runs the whole suite on every push, not simee-agents' local CI
