# Backend Code-Spec

## Required contracts

- [Closed-beta auth, billing and durable deployment](auth-and-durable-beta.md)

## Quality Check

- Run `PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v`.
- Run `git diff --check`.
- For auth changes, verify 401 for missing login and 404 for cross-user resources.
- For Render changes, verify PostgreSQL and S3 are both required; temporary disk is never the
  production source of truth.
