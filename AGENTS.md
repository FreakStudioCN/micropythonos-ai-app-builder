# Agent notes

- Product overview, layout, development rules: `README.md`.
- Architecture: `docs/architecture.md`. Payments: `docs/payments.md`.
- Production deploy: `.github/workflows/publish-image.yml` and `docs/deployment-domestic-plan.md`.
  The deploy job runs on a self-hosted runner on a shared production host (`mpos.upypi.net`,
  same compose stack as other services). Never add `pull_request` / `pull_request_target`
  triggers to a workflow that uses that runner, and keep every `docker compose` call scoped
  to `mpos-app`.
