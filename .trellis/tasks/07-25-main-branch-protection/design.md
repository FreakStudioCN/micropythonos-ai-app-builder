# Design: Main Branch Protection

## Existing State

- Default branch: `main`
- Repository visibility: public
- Active workflow: `.github/workflows/ci.yml`
- Existing check context: `test`
- Existing branch protection: none
- Existing repository rulesets: none

## Protection Policy

Use the GitHub branch protection API for `main` with:

- `required_status_checks.strict = true`
- required context `test`
- pull request requirement with zero mandatory approvals
- administrator enforcement
- conversation resolution
- force pushes disabled
- deletion disabled
- linear history not required

Zero mandatory approvals preserves a practical solo-maintainer flow while still preventing direct pushes and enforcing CI plus current-main alignment.

## Permission Boundary

Updating branch protection requires repository Administration write permission. A token with only Contents write or push permission cannot perform this operation.

