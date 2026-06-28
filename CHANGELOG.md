# Changelog

All notable changes to DeployGuard are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [0.1.0] — 2026-06-28

First public release. M0–M3 complete; M4 (LLM/MCP) is stretch/not started.

### Added

- `dg init <name>` — scaffold hardened FastAPI + Postgres service (Dockerfile, k8s manifests, Terraform, CI)
- `dg cost [path]` — static cost report from Terraform JSON + infracost; rejects above threshold before provisioning
- `dg provision` — bring up minikube (local) or k3s on EC2 Spot via Terraform (AWS)
- `dg deploy` — build → push → canary rollout (10/50/100%) → Prometheus error-rate gate → auto-rollback
- `dg doctor` — validate prerequisites (docker, minikube, kubectl, kubeconform, trivy) and config files
- `dg dashboard` — open minikube Kubernetes dashboard in browser (`--url` for URL-only)
- Guard layer: 7 policy rules (resource limits, probes, security context, no `:latest`, no root, no privileged, IAM least-privilege) with kubeconform + trivy integration; every violation explains itself
- Cost guard: 8 cost-policy rules; warn/reject thresholds configurable per team
- Engine: blue/green precheck, gradual traffic shift, Prometheus watch, auto-rollback, SQLAlchemy audit log
- Config merge: CLI flag → `.deployguard/config.yaml` → `~/.deployguard/config.yaml` → built-in defaults
- LLM adapter: LM Studio local backend (working); Bedrock stub (not yet implemented)
- Rich Live precheck panel: live STABLE/GREEN pod counts, health-check status, coloured border on pass/fail
- Standalone Claude Code skill (`skill/SKILL.md`) — wraps guard module for use inside Claude Code
- Homebrew formula (`Formula/deployguard.rb`)
- PyPI package published as `dg-deploy` (`pip install dg-deploy` / `pipx install dg-deploy`)
- GitHub Actions CI pipeline with coverage gate

### Infrastructure

- Local cluster: minikube (profile `deployguard`) + ingress addon
- AWS cluster: k3s on EC2 Spot, no NAT Gateway; running ~$6/mo, paused ~$3.50/mo
- Registry: ECR

### Known limitations

- `deployguard.llm.bedrock` — `generate()` raises `NotImplementedError`; Bedrock support is not yet implemented
- MCP server wrapper not yet implemented (M4 stretch)
- Coverage gate set at 55% (scaffold templates lack unit tests)

[0.1.0]: https://github.com/toni7891/DeployGuard/releases/tag/v0.1.0
