simee-kicad: simee's KiCad fork; this `simee-ci` branch holds only simee's packaging tooling. README.md has how releases are built.

- tests: `uv run pytest`; targeted tests (simee-agents#87; `$SIMEE_CHAIN_CMD tests` says whether the chain runs targeted tests or full suites): `uv run pytest tests/<file>` / `-k <name>` for what you changed; local CI runs the whole suite on `simee-ci` after every merge
