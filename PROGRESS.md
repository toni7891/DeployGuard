# DeployGuard — Project Progress

## End Goal

A CLI tool (`dg`) that takes a developer from zero to a safely-deployed FastAPI+Postgres service on Kubernetes in four commands with no flags. It scaffolds hardened configs, validates its own output, estimates cost before spending, provisions the cluster, and deploys with a gradual traffic shift + automatic rollback. It then deploys itself. See `PRD.md` for the full spec.

---

## Current State

**Phase:** M3 complete. M4 (LLM/MCP) not started.

**What exists:**
- Full CLI: `dg doctor`, `dg init`, `dg cost`, `dg provision`, `dg deploy`, `dg dashboard`
- Guard module (`deployguard/guard/`) — kubeconform + trivy + 7 policy rules with explanations
- Cost module (`deployguard/cost/`) — Terraform parse + infracost + 8 cost-policy rules
- Engine (`deployguard/engine/`) — blue/green precheck, canary traffic shift (10/50/100), Prometheus gate, auto-rollback, audit log
- Provision (`deployguard/provision/`) — local (minikube) and AWS (k3s on EC2 Spot via Terraform)
- LLM module (`deployguard/llm/`) — LM Studio adapter (working), Bedrock adapter (stub)
- `dg dashboard` — opens minikube Kubernetes dashboard in browser (`--url` for URL-only mode)
- Rich Live panel in precheck — shows STABLE/GREEN pod counts updating live during blue/green startup, health check result, red border on rollback, green border on pass

**Known implementation notes:**
- Local cluster: **minikube** (profile: `deployguard`) — switched from k3d due to helm ingress-nginx timeout issues. Uses `minikube addons enable ingress` instead of Helm.
- Pod counts in precheck panel read from pod-level `ownerReferences` (not `deployment.status.readyReplicas`) — the deployment controller has intermittent RBAC issues updating EndpointSlices in the `movies` namespace, causing `readyReplicas` to stay null even when pods are Running.
- Green deployment is deleted after successful precheck (before canary rollout begins) — cleanup bug was fixed.

---

## Milestone Status

| Milestone | Status | Notes |
|---|---|---|
| M-1: Guard skill | Done | Standalone module + SKILL.md complete |
| M0: Spine | Done | init → provision → deploy + spine test all complete |
| M1: Harden + cost + config | Done | guard in init, cost module, full config merge, doctor extended |
| M2: Deploy engine | Done | gradual rollout, Prometheus gate, auto-rollback, audit — 26 unit tests pass |
| M3: AWS + self-deploy | Done | ECR push, cost accuracy, CI pipeline, self-deploy |
| M4: LLM + MCP (stretch) | Not started | Blocked on M3 |

---

## Step-by-Step Progress

| Step | Title | Status |
|---|---|---|
| 1 | Project skeleton | Done |
| 2 | Guard core (kubeconform + trivy wrappers) | Done |
| 3 | Guard policy rules (Python) | Done |
| 4 | Guard explanation engine | Done |
| 5 | test_guard.py | Done |
| 6 | SKILL.md (standalone Claude skill) | Done |
| 7 | CLI entrypoint + stubs | Done |
| 8 | config.py (basic defaults) | Done |
| 9 | deployguard.yaml Pydantic schema | Done |
| 10 | dg init templates (minimal) | Done |
| 11 | dg provision — local k3d | Done |
| 12 | dg deploy — local basic | Done |
| 13 | test_smoke_e2e.py | Done |
| 14 | Golden-path templates (hardened) | Done |
| 15 | Guard absorbed into dg init | Done |
| 16 | config.py full merge logic | Done |
| 17 | dg doctor | Done |
| 18 | dg cost | Done |
| 19 | test_cost.py + test_config.py | Done |
| 20 | engine/ pre-check + smoke test | Done |
| 21 | engine/ gradual traffic shift | Done |
| 22 | engine/ Prometheus watch | Done |
| 23 | engine/ auto-rollback | Done |
| 24 | Audit log (SQLAlchemy → Postgres) | Done |
| 25 | provision/ AWS target (Terraform) | Done |
| 26 | deploy/ AWS (ECR + remote) | Done |
| 27 | dg cost accuracy vs real AWS bill | Done |
| 28 | GitHub Actions CI pipeline | Done |
| 29 | DeployGuard deploys itself | Done |
| 30 | llm/ LM Studio adapter (stretch) | Done |
| 31 | llm/ Bedrock adapter (stretch) | Not started (stub only — generate() raises NotImplementedError) |
| 32 | dg init with LLM generation (stretch) | Done |
| 33 | MCP server wrapper (stretch) | Not started |

---

## Next Steps (immediate)

M1–M3 complete. Remaining:
- Step 31: Bedrock LLM adapter (stub only — `generate()` raises `NotImplementedError`)
- Step 33: MCP server wrapper (not started)

**Recent additions (post-M3):**
- `dg dashboard` command — launches minikube dashboard, opens browser tab automatically
- Rich Live precheck panel — live STABLE/GREEN pod count display during deploy, health check status, colored border (green=pass, red=fail)
- Green deployment cleanup bug fixed — green pods now deleted after successful precheck, not just on failure
- Pod count reads from pod `ownerReferences` regex match instead of `deployment.status.readyReplicas` (RBAC stability fix)

---

## Blockers / Decisions Pending

- [ ] OPA/Rego vs pure Python for policy rules (decide before Step 3; PRD says Python first, Rego is stretch)
- [ ] Docker Desktop vs colima (default: Docker Desktop per PRD §6)
- [ ] Verify ROCm/Ollama support for AMD RX 9070 XT (RDNA 4) before M4 PC usage

---

## Key Invariants (never break these)

- `tests/test_smoke_e2e.py` must pass after every change (set as CI branch-protection rule)
- No module reads config files directly — everything goes through `deployguard/config.py`
- Guard layer always runs; nothing ships without it
- No NAT Gateway in infra — cost target ~$6/mo running
