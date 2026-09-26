# Repository guide for coding agents

This repository is in internal development. Before changing code, read these
files in order:

1. `README.md`
2. `DEVELOPMENT_STATUS_ZH.md`
3. The implementation file(s) named by the selected task ID

`DEVELOPMENT_STATUS_ZH.md` is the source of truth for implementation progress.
Use its stable task IDs in plans, commits and pull-request descriptions.

## Status update rules

- Update the relevant task row in the same pull request as the implementation.
- Use only `DONE`, `PARTIAL`, `TODO`, `BLOCKED` or `VERIFY`.
- Mark a task `DONE` only when its code, required artifact and acceptance check
  all exist. Code without production data or validation is `PARTIAL`.
- Add concise evidence to the status cell, such as a file path, test name or
  generated artifact. Do not use unsupported completion percentages.
- Update the audit date and base commit when performing a full repository audit.
- If implementation contradicts an older planning document, treat current code
  and the status dashboard as authoritative, then correct the stale document.

## Repository boundaries

- The authoritative submission code is under `submission_repo/`.
- `Labs/` contains workshop exercises and sample data, not production results.
- `deprecated/` is reference-only and must not be used as the active runtime.
- Never commit `.env`, API keys, credentials, local virtual environments,
  generated caches or private on-site photographs without explicit approval.
- Runtime answering must not crawl websites. Crawling, image description and
  index building belong to the offline build stage.
- The grader contract is one short answer per question and a maximum of 30
  seconds per question; stdout must not contain debug logs.
