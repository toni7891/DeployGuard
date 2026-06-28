# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

DeployGuard is a CLI tool (`deployguard`, alias `dg`) that scaffolds, validates, costs, provisions, and deploys a Python FastAPI + Postgres service to Kubernetes with health-checked automatic rollback. One golden path only. See `PRD.md` for the full spec.

The normal developer flow — no flags needed:

```bash
dg init payments-api
dg cost
dg provision
dg deploy
```

## Build order — strictly vertical

1. **M-1 (ship first):** `deployguard/guard/` + `skill/SKILL.md` — standalone validation module, no cluster required.
2. **M0:** thin spine — `dg init` → `dg provision` (minikube) → `dg deploy` (local). `tests/test_smoke_e2e.py` must pass before anything else deepens.
3. **M1:** harden `init` templates, absorb guard into `init`, `dg cost`, full `config.py` merge logic.
4. **M2:** gradual rollout (10/50/100), Prometheus watch, auto-rollback, audit log.
5. **M3:** AWS target, self-deploy, GitHub Actions CI.
6. **M4 (stretch):** LLM-assisted generation, MCP server.

Never merge a change that breaks `tests/test_smoke_e2e.py`.

## Commands

```bash
# Install
pip install -e ".[dev]"

# Run tests
pytest
pytest tests/test_guard.py          # guard/policy unit tests
pytest tests/test_smoke_e2e.py      # spine test — must always pass

# CLI (once installed)
dg doctor          # validate prerequisites + config files
dg init <name>     # scaffold golden-path service
dg cost [path]     # static cost report on infra/
dg provision       # bring up minikube (local) or k3s on EC2 Spot (aws)
dg deploy          # build → push → safe rollout → rollback if needed
dg dashboard       # open minikube Kubernetes dashboard in browser (--url for URL only)

# Cluster cost controls
make up
make pause
make resume
make destroy
```

## Planned repo layout

```
deployguard/
├── cli.py           # Typer entrypoint — thin; no logic lives here
├── config.py        # SINGLE config reader — every module reads through this
├── guard/           # M-1 module; also the guard stage of dg init
├── cost/            # terraform parse + infracost wrap + cost-policy rules
├── llm/             # thin adapter: local (LM Studio) or bedrock
├── provision/       # minikube + terraform orchestration
├── engine/          # safe-rollout + rollback core (reusable module)
└── scaffold/        # Jinja2 templates for the golden path
skill/SKILL.md       # standalone Claude skill — wraps guard/
tests/
infra/               # Terraform: k3s on EC2 Spot
```

## Tech stack

| Concern | Choice |
|---|---|
| CLI | Python + Typer |
| Config / schemas | Pydantic |
| Terminal UX | Rich (live rollout display) |
| Templating | Jinja2 |
| Manifest validation | kubeconform + trivy + Python policy rules (→ OPA Rego stretch) |
| Cost estimation | Infracost JSON + `terraform show -json` |
| Deploy/rollout | `kubernetes` client + kubectl |
| Metrics | Prometheus HTTP API (PromQL) |
| Audit | SQLAlchemy → Postgres |
| Local cluster | minikube + ingress addon (canary-weight annotations for traffic shift) |
| Prod cluster | k3s on EC2 Spot, no NAT Gateway |
| Registry | ECR |
| LLM (M4) | LM Studio local (default) or AWS Bedrock |
| Testing | pytest |

## Architecture rules

- **`config.py` is the only config reader.** No module reads `.deployguard/config.yaml` or `~/.deployguard/config.yaml` directly. Priority: CLI flag → project config → personal config → built-in defaults.
- **Core logic stays out of CLI handlers.** Guard in `guard/`, cost in `cost/`, rollout in `engine/`, LLM in `llm/`. `cli.py` calls them.
- **Guard layer is load-bearing.** Nothing ships without passing the guard. LLM (when added) is always the drafter; guard always has final say.
- **Every guard violation explains itself** — not just what failed but why it matters and what to add. Assert on explanation text in `test_guard.py`, not just the reject.
- **`guard/`, `cost/`, and `engine/` are libraries**, not scripts. The CLI, the standalone skill, and a future MCP server all import the same modules.
- **Flags are escape hatches only.** Behavior that applies to a team goes in `.deployguard/config.yaml`. Only add a flag when a single run genuinely needs to diverge from config.

## Config schema (`.deployguard/config.yaml`)

```yaml
llm:
  backend: local              # local | bedrock
  model: qwen2.5-coder:14b
  temperature: 0.2

guard:
  strict: true                # false = warn only; true = fail (use true in CI)
  explain: true
  rules:
    require_resource_limits: error
    require_probes: error
    require_security_context: error
    no_latest_tag: error
    no_root_user: error
    no_privileged_containers: error
    iam_least_privilege: warn
  custom_rules_dir: .deployguard/rules/

cost:
  currency: USD
  warn_threshold: 10.00
  reject_threshold: 50.00
  always_explain: false

deploy:
  target: local               # local | aws
  error_rate_threshold: 1.0   # % — rollback trigger
  rollout_steps: [10, 50, 100]
  smoke_timeout: 60
  audit: true
```

## Non-goals (flag and stop if these come up)

Web UI, multi-cluster, EKS, managed Kubernetes, second golden path (non-FastAPI), multi-language support, general FinOps platform, SaaS features, RBAC/org-level policy.

## Cost targets

- Running: ~$6/mo (EC2 Spot + RDS micro + Route 53, no NAT Gateway)
- Paused: ~$3.50/mo
- Any new standing-cost resource must have a `pause`/`destroy` path and be flagged by `dg cost`.
