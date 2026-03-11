# Contributing

Thanks for helping keep the AI demo project production-ready. This repo follows a light-weight, trunk-based workflow so every change lands through a reviewed pull request.

## Branching & Commit Style
- Work off `main`; create short-lived feature branches named `<type>/<context>` (e.g., `fix/eval-flake`).
- Use Conventional Commits (`feat:`, `fix:`, `docs:`). They drive release notes and make browsing history easy.
- Keep commits focused. If your change spans multiple sections, split it into separate PRs so each gate can be validated independently.

## Pull Requests
1. Open/assign an issue using the templates under `.github/ISSUE_TEMPLATE/`.
2. Link the issue in your PR description and fill the PR template completely.
3. Run `python demo/run_all.py --mock` locally. When touching live API calls, record a mock + live run in the PR notes.
4. Keep diffs readable (< 400 lines whenever possible) and include screenshots when updating demo output.

## Reviews & Approvals
- CODEOWNERS enforces at least one maintainer review for high-risk areas (demo scripts, CI pipelines, GitHub workflows).
- GitHub branch protection should block direct pushes to `main`, require status checks, and enforce linear history.
- When reviewers request changes, address them in follow-up commits (no force-push after review without coordination).

## CI/CD Expectations
- Every PR must keep the CI `regression-gate` workflow green (see `.github/workflows` once Section 2 is wired up).
- Include unit-style tests when practical. At minimum, add deterministic mocks for any new API integrations so the mock path stays zero-cost.

## Release Tags
- Tag recruiter/demo-ready drops with `demo-vX.Y.Z` after the regression gate passes on `main`.
- Document notable behaviour changes in `CHANGELOG.md` (to be introduced in a later section).

Questions? Tag `@marlondridley` or post in the issue discussion before investing significant effort.
