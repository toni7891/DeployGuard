# DeployGuard - Product Requirements Document

> A CLI-driven golden path that takes a developer from `init` to a safely-deployed
> service on Kubernetes - scaffolding hardened configs, provisioning the cluster, and
> deploying with health-checked automatic rollback.
> 
> **The meta story: DeployGuard deploys itself, the same way it deploys everything else.**

- **Owner:** Tony
- **Status:** Refactor / v1 spec
- **Last updated:** 2026-06-13
- **Build window:** 3–4 weeks, team of 3, paired with Claude Code
- **Dev machines:**
  - **Primary:** MacBook Air M4, 16GB RAM - main dev machine; runs k3d, the full
    local stack, and a local LLM simultaneously without constraint.
  - **Secondary:** PC with AMD Radeon RX 9070 XT (16GB VRAM) - stretch LLM target for
    larger models (22B+). Verify ROCm/LM Studio support for RDNA 4 before committing to
    this machine for LLM workloads.
- **Build order:** ship the standalone **guard skill (M-1)** first as a quick, shareable
  Claude artifact, then build the full product spine-first. The skill is written as a
  library so it later becomes the `dg init` guard stage - the easy thing is a component
  of the hard thing, not a throwaway.

-----

## 1. Why this exists

Deploying an app to Kubernetes from scratch means writing a Dockerfile, a stack of
manifests, a CI pipeline, and infrastructure - each a place to get something subtly
wrong (no resource limits, no probes, an over-permissive IAM role, a hallucinated API
field that lints clean and fails in prod). Today people either copy-paste from an old
repo or have an LLM generate it and hope.

DeployGuard is an opinionated **paved road**: one command scaffolds a known-good stack,
one provisions the cluster, one deploys it safely. The name is literal - it *guards*
each stage: it validates generated configs, enforces cluster policy, **guards the bill**,
and guards the rollout with automatic rollback.

This is a deliberate refactor of the original DeployGuard (a standalone rollback tool).
The rollback engine is **not discarded** - it becomes the `deploy` stage of a larger
end-to-end flow. The pivot trades some depth-per-component for a much stronger narrative
arc, and protects two deep spots so it never reads as a thin wrapper:

1. the **generate-then-validate** hardening layer (the guard layer), and
1. the **safe-rollout engine** (health checks → gradual traffic shift → auto-rollback).

-----

## 2. Goals & non-goals

### Goals (v1)

- A single CLI (`deployguard`, alias `dg`) with four commands: `init`, `cost`,
  `provision`, `deploy`.
- **One** excellent golden path: a **Python FastAPI + Postgres** service.
- Generated configs are **hardened and validated**, not just emitted.
- Generated infra is **costed and cost-guarded** before it deploys (`dg cost`).
- Deploys are **zero-downtime with automatic rollback** on health/error-rate failure.
- Works against a **local** cluster (k3d) and an **AWS** cluster (k3s on EC2 Spot).
- Runs cheap: **~$6/mo running, ~$3.50/mo paused**, no NAT Gateway.
- DeployGuard deploys *itself* via its own `deploy` stage.
- Commands are **as simple as possible to remember** - team behavior lives in config,
  not in flags.
- The **guard layer ships first as a standalone, shareable Claude skill** - a small,
  on-trend artifact in hand before the full infra build is done.

### Non-goals (explicitly out of scope for v1 - resist these)

- More than one golden path / multi-language support.
- A web UI or Backstage portal. **CLI only.**
- Multi-cluster, multi-tenant, RBAC, or org-level policy management.
- A managed control plane, billing, or anything SaaS-shaped.
- EKS or any managed Kubernetes (cost - k3s on Spot is the point).
- A *learning* tool. The audience is developers shipping services, not students.
- A general cloud cost-management product. `dg cost` guards *this tool’s* generated
  infra; it is not a standalone FinOps platform.

If a feature isn’t on the Goals list, it waits. Scope creep is the primary risk.

-----

## 3. Users

- **Primary:** a solo developer (Tony) or a small team who want to ship a service to
  k8s without hand-writing infra each time - and share the same guard, cost, and rollout
  settings across the team without an onboarding doc.
- **Secondary:** any developer who installs the standalone guard skill to harden
  AI-generated manifests/Terraform inside their own Claude Code workflow.
- **Implicit reviewer:** an interviewer watching a 3-minute demo. Every design choice
  should survive the question *“why did you do it that way?”*

-----

## 4. The product: one CLI, four guarded stages

```
dg init      → scaffold a hardened FastAPI+Postgres golden path into ./<name>
dg cost      → static cost report: what spins up, est. monthly cost, bill-inflation risks
dg provision → ensure a configured cluster exists (local k3d or aws k3s)
dg deploy    → build → push → safe rollout with health checks + auto-rollback
dg doctor    → validate prerequisites AND config; report exactly what's missing
```

Plus cost controls (Makefile): `make up`, `make pause`, `make resume`, `make destroy`.

### Interface philosophy - convention over configuration, not flags

**The commands must be easy to remember and easy to share across a team.**
The normal flow for any developer on any machine is four commands with no flags:

```bash
dg init payments-api
dg cost
dg provision
dg deploy
```

That’s it. All team decisions - LLM backend, guard strictness, rollout steps, cost
thresholds, deploy target - live in a committed config file, not in flags everyone has
to agree on and remember to type. Every team member runs the same four commands and gets
identical behavior.

**Flags are escape hatches, not the primary interface.** A flag is only justified when
a single run genuinely needs to diverge from the team config - not as a way to avoid
writing config. The flag surface is kept deliberately minimal (see §4.6).

**CLI-only for v1 is a deliberate choice, not a limitation:** it fits the solo-dev
workflow, demos cleanly, and composes with CI (a pipeline calls `dg deploy`; a GUI
can’t). But the tool is **not CLI-trapped** - the guard, cost, and rollout logic live in
importable modules (`deployguard/guard/`, `deployguard/cost/`, `deployguard/engine/`),
with the CLI as just *one* caller. A FastAPI service, a Claude Code skill, or an MCP
server can all call the same core without a rewrite. (See M-1 and M4.)

### 4.1 `dg init <name>`

Scaffolds a new service from the golden-path templates, then **validates** the output
before writing it as final.

**Emits:**

- `Dockerfile` - multi-stage, non-root user, pinned base, healthcheck.
- `app/` - minimal FastAPI app with `/healthz` (liveness) and `/readyz` (readiness).
- `k8s/` - Deployment (probes, resource requests/limits, securityContext, non-root),
  Service, Ingress, and a Postgres dependency (StatefulSet + PVC for local; RDS for prod).
- `.github/workflows/ci.yaml` - build, test, scan, push to ECR, trigger deploy.
- `infra/` - Terraform stub for the service’s cloud resources.
- `.env.example` - referenced secrets, never real values.
- `deployguard.yaml` - **per-service** manifest: name, port, replicas, target cluster,
  health endpoints. Parsed and validated with Pydantic. This is *not* the tool config -
  it describes this specific service only. (See §4.5 for tool config.)
- `README.md` - generated, with the exact commands to provision and deploy.

**Guard layer (depth spot - do not skip; this is the M-1 module, absorbed here):**

- **YAML parse validation** (`yaml_parse` rule): malformed YAML surfaces as a named
  ERROR violation with explanation rather than a raw exception. Runs before all other
  checks — a file that can't be parsed skips kubeconform, trivy, and policy rules.
- Schema-validate all manifests (`kubeconform`).
- Image + IaC + manifest misconfig scanning (`trivy`).
- Policy checks (hand-rolled Python rules → OPA Rego): reject if missing resource
  limits, missing probes, missing securityContext, running as root, `:latest` image
  tags, or over-permissive IAM in the Terraform.
- Guard strictness is controlled by the tool config (`guard.strict`), not a flag.
- `dg init` **fails loudly** if its own output violates policy. The generator is never
  trusted blindly - that’s the whole thesis.
- **The guard explains, it doesn’t just reject.** Every violation carries a
  human-readable *why it matters* and *what to add* - not a bare `ERROR` line. Not
  "missing resource limits: ERROR" but "missing resource limits: without these one pod
  can starve the node and take its neighbours down with it - add `resources.requests`
  and `resources.limits`." Each policy rule is a best practice with a reason behind it,
  so the guard reads as a senior reviewer, not a linter. This makes the same code useful
  three ways: a gate in `dg init`, a teaching tool in the standalone skill (§M-1), and a
  stronger demo - a reviewer watching it reject *and explain* AI-generated infra sees
  judgement, not just a regex. Building the explanation alongside the rule costs nothing
  and is non-optional.

### 4.2 `dg cost [path]` - the guard layer pointed at the bill

A static cost report on generated `infra/` (or any existing Terraform dir). No cluster,
no spend - pure static analysis. Same machinery as the guard layer (parse IaC → run
rules), aimed at the wallet instead of security.

**Three sections:**

1. **What will spin up** - parse `terraform show -json` for the resource graph; parse
   k8s manifests for pods and storage. Fully deterministic.
1. **Estimated monthly cost** - drive **Infracost** for running and paused figures.
   Wrapped, not reinvented.
1. **Bill-inflation risks** - the hand-rolled cost-policy ruleset. Infracost gives
   numbers; these rules editorialize about footguns.

**Cost-policy rules (warn / reject):**

- NAT Gateway present (~$32/mo + data processing).
- On-demand instances where Spot was intended.
- Oversized instance type relative to the golden path.
- Unattached / idle Elastic IP.
- Load balancer (ALB/NLB) present (~$16+/mo each).
- Uncapped RDS storage autoscaling.
- `count` / `for_each` multiplying a billable resource.
- **Any standing-cost resource with no `pause`/`destroy` path** - the load-bearing rule.

Cost thresholds (warn and reject levels) are set in the tool config, not as flags.
`--explain` is the one flag: verbose reasoning per line, on demand.

**Honest by design:** output is labelled an estimate with stated assumptions. Never a
guaranteed invoice.

### 4.3 `dg provision`

Ensures a cluster exists and is configured for the golden path. The target (`local` or
`aws`) is read from the tool config - no flag needed for the normal flow.

- **local:** bring up **minikube** (profile: `deployguard`), enable ingress addon (built-in, no Helm needed), create namespace + resource quota.
- **aws:** run the **k3s-on-EC2-Spot** Terraform (public subnet, no NAT Gateway,
  Elastic IP, security group), then the same in-cluster config as local.
- Idempotent: safe to re-run; converges to the desired state.

### 4.4 `dg deploy` - *this is the DeployGuard engine*

Reads `deployguard.yaml` (per-service) and the tool config (behavior), then:

1. **Build** the image, tag with git SHA.
1. **Push** to ECR (aws) or load into k3d (local).
1. **Pre-checks:** apply to a green deployment, wait for readiness, smoke test `/readyz`.
   Abort before touching live traffic if it fails.
1. **Gradual rollout:** shift traffic across steps defined in tool config (default
   `[10, 50, 100]`) via nginx-ingress canary weights.
1. **Watch:** query Prometheus error rate at each step.
1. **Auto-rollback:** if error rate exceeds the configured threshold (default 1%) or
   readiness fails, `kubectl rollout undo` and stop. Live traffic never sees bad code.
1. **Record:** write a full audit row (who/what/when/result/why) to Postgres.

All thresholds and rollout steps come from tool config. A live `Rich` progress display
of the traffic shift is part of the demo, not cosmetic.

**Engineering rule:** the rollout/rollback logic lives in `deployguard/engine/` as a
reusable module. Don’t bury it inside CLI command handlers.

### 4.5 Tool configuration - two files, one principle

**The principle:** team behavior lives in config, not in flags. Commit the project
config, and every teammate gets identical behavior with zero onboarding.

**Two files, two scopes:**

```
~/.deployguard/config.yaml        # personal - machine-specific, never committed
.deployguard/config.yaml          # project/team - committed to the repo
```

The project file is the shareable team agreement. Personal file handles
machine-specific things (which local LLM model you have pulled) that shouldn’t be
shared. Both are optional - built-in defaults cover a solo dev with no config at all.

**Priority order (highest to lowest):**

```
CLI flag              → one-off override for a single run
  ↓
project config        → .deployguard/config.yaml  (committed, shared)
  ↓
personal config       → ~/.deployguard/config.yaml (local machine, not committed)
  ↓
built-in defaults     → sane out-of-the-box behavior, no config required
```

**Full config schema:**

```yaml
# .deployguard/config.yaml

llm:
  backend: local              # local | bedrock
  model: qwen2.5-coder:14b    # ignored when backend is bedrock
  temperature: 0.2            # low - deterministic structured output

guard:
  strict: true                # true = fail on violations | false = warn only
  explain: true               # true = every violation carries why-it-matters + the fix
  rules:
    require_resource_limits:    error   # error | warn | off
    require_probes:             error
    require_security_context:   error
    no_latest_tag:              error
    no_root_user:               error
    no_privileged_containers:   error
    iam_least_privilege:        warn    # warn - Terraform stubs are rough drafts
  custom_rules_dir: .deployguard/rules/ # drop .py or .rego rules here; auto-loaded

cost:
  currency: USD
  warn_threshold: 10.00       # warn if estimated monthly cost exceeds this
  reject_threshold: 50.00     # hard stop if estimate exceeds this
  always_explain: false       # true = verbose reasoning on every dg cost run

deploy:
  target: local               # local | aws
  error_rate_threshold: 1.0   # % - rollback trigger
  rollout_steps: [10, 50, 100]# traffic shift steps; customisable per team
  smoke_timeout: 60           # seconds to wait for /readyz before aborting
  audit: true                 # write audit rows to Postgres
```

**`guard.custom_rules_dir` is the extensibility valve.** Teams drop their own `.py`
or `.rego` rules into that directory and DeployGuard picks them up without a code
change. The rule engine is pluggable - teams extend it without forking the tool.

**`guard.strict`** is the most useful team toggle: `false` during rapid local
development (warnings, don’t block), `true` in CI (hard fail). Same config key,
different value per environment if needed.

**`deploy.rollout_steps`** reflects the team’s risk tolerance. `[10, 50, 100]`
is the sensible default; a cautious team might use `[5, 10, 25, 50, 100]`; a confident
one might use `[50, 100]`. Making it config shows the right rollout shape depends on the
service, not the tool.

### 4.6 Minimal flag surface

Flags are escape hatches for single-run overrides. The full list is deliberately short:

|Command       |Flag                |When to use it                                           |
|--------------|--------------------|---------------------------------------------------------|
|`dg init`     |`--no-guard`        |Skip guard validation for a throwaway spike (never in CI)|
|`dg cost`     |`--explain`         |Verbose reasoning per line, on demand                    |
|`dg provision`|`--target local|aws`|Override the config target for this run only             |
|`dg deploy`   |`--target local|aws`|Deploy to a different target than config specifies       |
|`dg doctor`   |*(none)*            |Always runs the full check                               |

Everything else is config. If a new flag is proposed that could live in config instead,
it goes in config.

-----

## 5. Automation boundaries (what’s one-command vs. an intentional human gate)

DeployGuard is **“one command per stage, with deliberate gates between them”** - not
“one button, walk away.”

- **Automated:** once prerequisites exist, `init → cost → provision → deploy` is
  hands-off. `deploy` rolls out, watches, and rolls back on its own.
- **One-time bootstrap:** required tools installed (Docker, kubectl, helm, terraform,
  infracost, the `dg` CLI), AWS credentials configured, Route 53 domain for aws TLS.
  Handled by **`dg doctor`**, which checks prerequisites *and* validates the config
  files - one command to confirm the machine and the config are both ready.
- **Intentional human gates:**
  - **App business logic** - `dg init` scaffolds a working skeleton; the developer
    writes the actual service.
  - **Secrets** - emitted as `.env.example`; real values supplied by the human, never
    committed.
  - **`provision` targeting aws** - spinning up billable infra is an explicit action.
    The tool never silently creates EC2 instances. `dg cost` is the recommended
    look-before-you-leap step right before this.
- **Full-auto path (M3 finale):** `git push` → GitHub Actions → `dg deploy`
  end-to-end. Sits on top of a working manual flow, never replaces the gates above.

-----

## 6. Tech stack

|Concern            |Choice                                             |Rationale                                            |
|-------------------|---------------------------------------------------|-----------------------------------------------------|
|CLI                |Python + **Typer**                                 |Python-strong; Typer is clean and testable           |
|Config parsing     |**Pydantic**                                       |Typed, validated config + `deployguard.yaml` for free|
|Terminal UX        |**Rich**                                           |Live 10/50/100 rollout display - half the demo       |
|Templating         |**Jinja2**                                         |Standard, transparent output                         |
|Manifest validation|**kubeconform** + **trivy** + policy (Python → OPA)|Deterministic guard layer (the M-1 module)           |
|Cost estimation    |**Infracost** (JSON) + `terraform show -json`      |Accurate pricing wrapped, not reinvented             |
|Deploy engine      |Python module; `kubernetes` client + `kubectl`     |Reusable by CLI, future API, and MCP server          |
|Metrics query      |**Prometheus HTTP API** (PromQL)                   |Rollout watches error rate per step                  |
|Audit writes       |**SQLAlchemy** → Postgres                          |Pays off when engine becomes a service               |
|Sample app         |FastAPI + Postgres                                 |The golden path being paved                          |
|Local cluster      |**minikube** (profile: deployguard) + ingress addon|Switched from k3d; helm ingress-nginx had timeout issues on local; `minikube addons enable ingress` is simpler and stable|
|Ingress            |**nginx-ingress**                                  |`canary-weight` annotation = clean traffic shift     |
|Container runtime  |**Docker Desktop** or **colima**                   |Both fine on M4; colima if RAM is a concern          |
|Prod cluster       |**k3s on EC2 Spot**, Terraform, no NAT Gateway     |~$3–5/mo                                             |
|Registry           |**ECR** (auth via `boto3`)                         |Already in toolchain                                 |
|CI/CD              |**GitHub Actions** + self-hosted runner (MacBook)  |Already set up                                       |
|Cluster install    |**Helm**                                           |Ingress + kube-prometheus-stack                      |
|Observability      |**Prometheus + Grafana**; CloudWatch on aws        |Rollout watches Prom error rate                      |
|Testing            |**pytest**                                         |Spine e2e + unit tests for guard/cost rules & engine |
|DNS / TLS (aws)    |Route 53 + ACM                                     |Matches prior design                                 |
|LLM generation (M4)|**Interchangeable** - see §6.1                     |Local or cloud, same guard layer either way          |

### 6.1 LLM backend - interchangeable by design (M4)

The LLM backend is a config value, not a flag. Set it once in `.deployguard/config.yaml`
and every team member uses the same backend without typing anything extra.

**`backend: local` → LM Studio on the M4 (default)**

- Fully on-device. No external API, no per-call cost, no internet required.
- M4 16GB unified memory runs 7B–14B models alongside the full dev stack comfortably.
- Recommended: `qwen2.5-coder-14b-instruct` (best structured output), `llama3.1-8b-instruct` (lighter).
- Served via LM Studio's OpenAI-compatible local server (default port 1234).
- PC (9070 XT, 16GB VRAM) is a stretch target for 22B+ models - verify ROCm support
  for RDNA 4 + current LM Studio before relying on it.

**`backend: bedrock` → Claude on AWS Bedrock (cloud fallback)**

- Best structured output quality for generated manifests and Terraform.
- Already in the AWS toolchain; no new account needed.
- Requires internet + AWS credentials.

The LLM adapter lives in `deployguard/llm/` as a thin interface. Swapping backends is
an adapter change; the guard layer and the rest of `dg init` are identical either way.
The LLM is always the drafter; the guard layer always has the final say.

### Decisions to resolve before M0

1. **nginx-ingress over Traefik.** k3d ships Traefik, but nginx-ingress’s
   `canary-weight` annotation is the simplest path to 10/50/100. Picked nginx; revisit
   only if it fights k3d.
1. **Docker Desktop vs colima.** Both fine on M4; default to Docker Desktop.
1. **OPA/Rego: v1 or stretch?** Hand-rolled Python policy is the spine; Rego is the
   stronger interview signal. Decide before M1.

-----

## 7. Repo structure

```
deployguard/
├── PRD.md                          # this file - single source of truth
├── pyproject.toml
├── Makefile                        # up / pause / resume / destroy
├── .deployguard/
│   └── config.yaml                 # project/team config - committed to repo
├── deployguard/
│   ├── cli.py                      # Typer entrypoint: doctor/init/cost/provision/deploy
│   ├── config.py                   # config loader: merges personal + project + defaults
│   ├── scaffold/                   # Jinja2 templates for the golden path
│   ├── guard/                      # validation + security policy checks (M-1 module)
│   ├── cost/                       # terraform parse + infracost wrap + cost-policy rules
│   ├── llm/                        # LLM adapter - local (LM Studio) and bedrock backends
│   ├── provision/                  # k3d + terraform orchestration
│   └── engine/                     # safe-rollout + rollback core (depth spot)
├── skill/
│   └── SKILL.md                    # standalone Claude skill - thin wrapper over guard/
├── templates/                      # raw golden-path template files
├── infra/                          # terraform: k3s on EC2 Spot (no NAT gateway)
├── tests/
│   ├── test_guard.py               # feed bad manifests, assert rejection
│   ├── test_cost.py                # feed footgun terraform, assert risk flagged
│   ├── test_config.py              # priority order, schema validation, custom rules
│   └── test_smoke_e2e.py           # the spine test - must always pass
└── examples/
    └── hello-fastapi/              # output of `dg init` for the demo
```

Note the new `deployguard/config.py` module - it owns the merge logic (flag → project
→ personal → defaults) and is the single place every other module reads settings from.
Nothing reads config files directly; everything goes through this module.

-----

## 8. Milestones

**Prime directive:** build **vertically, not horizontally.** Get `init → provision → deploy` working end-to-end for a hello-world app on a local cluster *first*, even dumb,
then deepen. A working thin spine beats two impressive halves that don’t connect.

### M-1 - The guard skill (ship first, standalone, days not weeks)

- `SKILL.md` + `deployguard/guard/` validation module. No cluster, no AWS.
- Built as an importable library - M1 absorbs it wholesale.
- **Why first:** everyone is now generating manifests and Terraform with an LLM and
  shipping them on hope - almost nothing validates AI-drafted infra before it lands. A
  skill that catches a hallucinated k8s field, an open IAM policy, or missing limits
  *and explains each one* rides exactly that wave. It’s just a `SKILL.md` plus a thin
  module: shareable in days (drop one file into `.claude/skills/`), in people’s hands
  long before the infra build is done.
- **DoD:** developer drops skill into `.claude/skills/`, pastes a sloppy manifest, gets
  back specific rejections *with a why-it-matters and what-to-add for each*;
  `tests/test_guard.py` passes (and asserts on the explanation text, not just the
  reject).

### M0 - The spine (Week 1, non-negotiable)

- `dg init` writes a hello FastAPI app + minimal manifests.
- `dg provision` brings up k3d + nginx-ingress (reads target from config).
- `dg deploy` builds, loads into k3d, applies, waits for ready.
- Basic `config.py` loader in place - even if only defaults are used.
- **DoD:** four clean commands, no flags, from nothing to a reachable app on localhost.
  `tests/test_smoke_e2e.py` passes and runs after every change.

### M1 - Harden `init` + cost + config (Week 2)

- Real golden-path templates: multi-stage Dockerfile, probes, resource limits,
  securityContext, CI pipeline, Terraform stub.
- Absorb M-1 guard module into `dg init`.
- Full `config.py`: priority merge, schema validation, custom rules dir.
- `dg doctor` validates config files as well as prerequisites.
- **`dg cost`:** resource inventory + cost-policy ruleset; Infracost pricing.
- **DoD:** guard rejects broken templates; `dg cost` flags a NAT Gateway; `dg doctor`
  catches an invalid config key; `tests/test_cost.py` and `tests/test_config.py` pass.

### M2 - Deepen `deploy` into DeployGuard (Week 2–3)

- Green pre-check + smoke test before live traffic.
- Gradual traffic shift (steps from config) via nginx-ingress, shown in Rich.
- Prometheus error-rate watch + auto-rollback.
- Audit row written to Postgres.
- **DoD:** broken image caught and rolled back; live traffic never errors; audit log
  records what happened and why. **This is the demo.**

### M3 - AWS target + self-deploy (Week 3–4)

- `dg provision` with `target: aws` in config spins up k3s on EC2 Spot.
- `dg deploy` pushes to ECR and deploys remotely.
- `dg cost` accuracy pass against the real AWS bill.
- GitHub Actions runs `dg deploy` end-to-end on git push.
- DeployGuard deploys itself.
- **DoD:** demo runs against AWS cluster; cost estimate within sane margin of real bill;
  `make pause` works.

### M4 - Stretch (only if M-1–M3 are solid)

- LLM-assisted generation behind the guard layer. Backend switchable via
  `llm.backend: local | bedrock` in config - no flags, no code changes.
- Expose engine as a Claude Code skill (`SKILL.md` → `dg` CLI) and/or MCP server.
- Grafana dashboard for the deploy timeline.
- Polished README + scripted 3-minute demo.

-----

## 9. Team split (3 people)

Two rules before fanning out:

- **Lock contracts on day one:** the `deployguard.yaml` Pydantic schema, the config
  schema, and the exact directory shape `init` emits. Everyone codes against the
  interface, not each other’s half-finished code.
- **Build the spine together first.** M-1 + M0 are a shared phase. No real lane
  boundaries until `init → provision → deploy` works end-to-end once.

|Lane                          |Owns                                                                                         |Milestones                 |Interview story                         |
|------------------------------|---------------------------------------------------------------------------------------------|---------------------------|----------------------------------------|
|**A - Guard & scaffold**      |`guard/`, `scaffold/`, `templates/`, `SKILL.md`                                              |M-1, M1 guard              |policy-as-code / guardrails for AI infra|
|**B - Deploy engine**         |`engine/` (rollout, Prom watch, rollback, audit)                                             |M2                         |reliability engineering / safe rollout  |
|**C - Platform, infra & cost**|`provision/`, `infra/`, `cost/`, `dg doctor`, Makefile, CI                                   |M0 provision, `dg cost`, M3|cloud / IaC / cost engineering          |
|**You - lead + integrator**   |`cli.py`, `config.py`, `deployguard.yaml` contract, `llm/` adapter, `test_smoke_e2e.py` green|all                        |owning the whole arc + the demo         |

`config.py` (the merge logic and schema) is owned by you as lead - every lane reads
from it, so it needs a single owner who understands all three lanes.

Lightweight process: branch-per-slice, conventional commits, each person reviews another
lane, “never merge red `test_smoke_e2e.py`” is a CI branch-protection rule, integrate
daily.

-----

## 10. Working with Claude Code (read at the start of every session)

- **Ship M-1 first, then the spine.** Confirm `tests/test_smoke_e2e.py` passes before
  deepening anything. Never merge a change that breaks it.
- **Convention over configuration.** If behavior can live in config instead of a flag,
  it goes in config. The flag surface stays minimal - see §4.6.
- **`config.py` is the single config reader.** No module reads config files directly.
  Everything goes through `deployguard/config.py` so the priority order is enforced
  consistently and testable in one place.
- **Build the guard, cost, and LLM layers as libraries, not scripts.** Each is a thin
  module; the CLI, skill, and future API/MCP all import the same module.
- **One vertical slice at a time.** Thin all the way through, then thicken.
- **Respect non-goals (§2).** Web UI, multi-cluster, EKS, second golden path, FinOps
  platform - flag as out of scope and stop.
- **Keep core logic out of CLI handlers.** Guard in `guard/`, cost in `cost/`, rollout
  in `engine/`, LLM in `llm/`, config in `config.py`.
- **The guard layer is load-bearing.** LLM or template, nothing ships without passing
  the guard. The LLM is always the drafter; the guard always has final say.
- **Cost is a feature.** No NAT Gateway. Anything that adds standing cost must be
  justified, flagged by `dg cost`, and have a `pause`/`destroy` path.
- **Commits:** conventional, small, one slice each.
- **When unsure, write the test first**, then make it pass.

-----

## 11. Cost model

|State  |Est. monthly|Notes                                          |
|-------|------------|-----------------------------------------------|
|Running|~$6.25      |EC2 Spot + RDS micro + Route 53; no NAT Gateway|
|Paused |~$3.55      |Compute stopped, storage + DNS only            |
|Local  |$0          |k3d on the MacBook                             |

Kill-switch: scheduled GitHub Actions (nightly pause, morning resume) + Makefile.
`dg cost` makes this table self-checking - it derives figures from the actual Terraform.

-----

## 12. Risks

|Risk                                        |Mitigation                                                                      |
|--------------------------------------------|--------------------------------------------------------------------------------|
|Scope creep                                 |Vertical slices; strict non-goals; one golden path                              |
|Reads as a thin wrapper                     |Two depth spots: guard layer + rollout engine; §5 automation boundaries         |
|Guard/cost built as throwaway               |Libraries from day one; CLI, skill, MCP all import the same module              |
|`dg cost` read as a guaranteed bill         |Labelled estimate; state assumptions; validate vs. real bill in M3              |
|Config priority order inconsistently applied|All config reads go through `config.py`; tested in `test_config.py`             |
|LLM backend swap breaks the pipeline        |Thin adapter in `llm/`; guard layer is backend-agnostic                         |
|ROCm/LM Studio lag for RDNA 4 (9070 XT)     |Verify before committing; M4 Mac is the safe default                            |
|Three lanes drift                           |Lock contracts day one; shared spine first; daily integration; cross-lane review|
|EC2 Spot interruption mid-demo              |Demo on local; aws is the “it runs in prod too” beat                            |
|Cloud spend before spine works              |M-1 and M0 ship before any AWS spend                                            |

-----

## 13. Interview narrative

> “I built a paved road that takes a service from `init` to running on Kubernetes with
> health-checked, auto-rollback deployments. Four commands, no flags to remember - all
> team behavior lives in a committed config file so every developer gets identical
> behavior without an onboarding doc. It generates the Dockerfile, manifests, and
> pipeline - validates its own output against policy, tells you what it’ll cost and
> where the bill could blow up, and deploys with a gradual traffic shift that rolls back
> automatically if error rates spike. In the stretch milestone the generation step uses
> an LLM - local by default, cloud as a fallback, switchable by one config line. And it
> deploys itself the same way.”

What it demonstrates: **platform engineering / golden paths**, **reliability engineering**
(safe rollout + rollback), **policy-as-code / guardrails for AI-generated infra** across
security and cost, and **thoughtful CLI design** - convention over configuration as a
first-class principle.