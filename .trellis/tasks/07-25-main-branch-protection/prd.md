# PRD: Main Branch Protection

## Background

The repository already uses `main` as its default branch and has an active GitHub Actions workflow named `CI`. Its required check candidate is `test`, and the latest `main` run passed.

The deployment branch and development branches must not be merged into `main` while behind the current `main` head.

## Requirements

- Require changes to enter `main` through a pull request.
- Require the GitHub Actions check `test` to pass.
- Require the pull request branch to be updated with the latest `main` before merge.
- Apply the policy to repository administrators.
- Require review conversations to be resolved.
- Disallow force pushes and branch deletion.
- Keep the repository's existing merge method choices.

## Out of Scope

- Changing application code.
- Replacing the existing CI workflow.
- Switching the Render deployment branch before the current work is merged.
- Adding mandatory approving reviewers for a single-maintainer repository.

## Acceptance Criteria

- GitHub branch protection for `main` reports `required_status_checks.strict = true`.
- Required status check contexts include `test`.
- Pull requests are required.
- Administrator enforcement is enabled.
- Force pushes and deletion are disabled.

