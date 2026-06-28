# DeployGuard — Development Path & Prompts

Each step has a self-contained prompt. Copy the prompt block into Claude Code to execute that step. Update `PROGRESS.md` after each step completes.

The build order is strictly vertical: M-1 guard skill first, then the spine (M0), then deepen layer by layer. Never widen before the current slice works end-to-end.

---

## M-1 — Guard Skill (ship first, standalone)

### Step 1 — Project skeleton

**Goal:** Create the Python package structure, pyproject.toml, and empty module directories so every subsequent step has a place to land.

```
PROMPT:

Context: DeployGuard is a Python CLI tool. No code exists yet. The PRD is in PRD.md and
the architecture rules are in CLAUDE.md. Read both before starting.

Task: Create the project skeleton.

What to create:
- pyproject.toml — package name "deployguard", entry point "dg = deployguard.cli:app",
  dependencies: typer[all], rich, pydantic, pydantic-settings, jinja2, sqlalchemy,
  kubernetes, boto3, requests. Dev deps: pytest, pytest-cov. Python >=3.11.
- deployguard/__init__.py — empty
- deployguard/cli.py — one Typer app with five stubbed commands: doctor, init, cost,
  provision, deploy. Each prints "not implemented yet" and exits 0. No logic.
- deployguard/config.py — empty module with a TODO comment marking it as the single
  config reader (do not implement yet).
- deployguard/guard/__init__.py — empty
- deployguard/cost/__init__.py — empty
- deployguard/llm/__init__.py — empty
- deployguard/provision/__init__.py — empty
- deployguard/engine/__init__.py — empty
- deployguard/scaffold/__init__.py — empty
- tests/__init__.py — empty
- tests/test_smoke_e2e.py — one test that imports deployguard and asserts True (placeholder).
- skill/ directory — empty, SKILL.md comes in Step 6.
- templates/ directory — empty.
- .gitignore — Python standard + .env + __pycache__ + .deployguard/personal.
- Makefile — four targets: up (placeholder), pause (placeholder), resume (placeholder),
  destroy (placeholder). Each prints "not implemented".

DoD: `pip install -e ".[dev]"` succeeds; `dg --help` shows all five commands;
`pytest` passes (1 test).

After completing, update PROGRESS.md: mark Step 1 as Done.
```

---

### Step 2 — Guard core: external tool wrappers

**Goal:** Build thin Python wrappers around `kubeconform` and `trivy` that the policy rules will call. No policy logic yet — just the subprocess calls and structured result objects.

```
PROMPT:

Context: DeployGuard project skeleton exists (Step 1 done). Read CLAUDE.md and PRD.md §4.1
for the guard layer spec. The guard module lives at deployguard/guard/.

Task: Build the guard core — thin wrappers over kubeconform and trivy.

What to build in deployguard/guard/:

1. deployguard/guard/models.py
   - Pydantic models:
     - Severity: Enum — ERROR, WARN
     - Violation(BaseModel): rule_id: str, severity: Severity, message: str, why: str,
       fix: str, path: str | None
     - GuardResult(BaseModel): passed: bool, violations: list[Violation]
       - property: has_errors -> bool (any violation with severity ERROR)

2. deployguard/guard/tools.py
   - run_kubeconform(manifest_path: str) -> list[Violation]
     Calls `kubeconform -strict -output json <path>`, parses output, returns violations
     with rule_id="schema", severity=ERROR, why="Invalid Kubernetes API field — will
     be silently ignored or rejected by the cluster.", fix="See kubeconform output above."
   - run_trivy_config(path: str) -> list[Violation]
     Calls `trivy config --format json <path>`, parses output, maps trivy severity
     (CRITICAL/HIGH → ERROR, MEDIUM/LOW → WARN), returns violations.
   - Both functions: if the tool binary is not found, raise a clear RuntimeError
     saying which tool is missing and pointing to `dg doctor`.

3. deployguard/guard/__init__.py
   - Export: GuardResult, Violation, Severity, run_kubeconform, run_trivy_config

DoD: `from deployguard.guard import GuardResult, Violation` works; the wrapper
functions exist and raise RuntimeError when the binary is absent (test with a mock).
No real kubeconform/trivy binary needed for the unit tests — mock subprocess.run.

After completing, update PROGRESS.md: mark Step 2 as Done.
```

---

### Step 3 — Guard policy rules (Python)

**Goal:** Implement the hand-rolled policy rules that check for the specific misconfigs DeployGuard enforces. These are the depth spot — each rule must produce a Violation with a real explanation.

```
PROMPT:

Context: deployguard/guard/models.py and tools.py exist (Steps 1–2 done). Read PRD.md
§4.1 carefully — the policy rules and the explanation requirement are both specified there.
Read CLAUDE.md for architecture rules.

Task: Implement the Python policy ruleset in deployguard/guard/rules.py.

What to build:

deployguard/guard/rules.py
- Each rule is a function: rule_<name>(manifest: dict) -> list[Violation]
- Manifest is a parsed YAML/JSON dict (a single Kubernetes object).
- Rules to implement (all seven from PRD §4.1 config schema):

  1. rule_require_resource_limits(manifest)
     Checks: Deployment/DaemonSet/StatefulSet containers have resources.requests and
     resources.limits set.
     why: "Without resource limits one pod can starve the node and bring down its
          neighbours. Kubernetes will not schedule your pod if the node is overcommitted."
     fix: "Add resources.requests and resources.limits to every container spec."

  2. rule_require_probes(manifest)
     Checks: containers in workload manifests have both livenessProbe and readinessProbe.
     why: "Without probes Kubernetes sends traffic to pods that aren't ready and never
          restarts ones that are stuck. Your deployment will appear healthy while serving
          errors."
     fix: "Add livenessProbe (restart on hang) and readinessProbe (gate traffic) to
          every container."

  3. rule_require_security_context(manifest)
     Checks: pod spec has securityContext set (at minimum runAsNonRoot: true).
     why: "A pod without a securityContext runs as root by default. A container escape
          then gives the attacker host root."
     fix: "Add securityContext.runAsNonRoot: true to the pod spec and set
          allowPrivilegeEscalation: false on each container."

  4. rule_no_latest_tag(manifest)
     Checks: no container image uses :latest or has no tag.
     why: ":latest is mutable — the image you tested is not guaranteed to be the image
          that deploys. Pinned tags make rollbacks and audits reliable."
     fix: "Pin every image to a specific digest or semantic version tag."

  5. rule_no_root_user(manifest)
     Checks: container securityContext does not set runAsUser: 0.
     why: "Running as UID 0 inside a container means a breakout gives the attacker root
          on the host. This is the most common container misconfiguration."
     fix: "Set securityContext.runAsUser to a non-zero UID (e.g. 1000) on each container."

  6. rule_no_privileged_containers(manifest)
     Checks: no container sets securityContext.privileged: true.
     why: "A privileged container has nearly full access to the host kernel. There is
          almost no legitimate use for this in an application workload."
     fix: "Remove securityContext.privileged: true. If you need specific capabilities,
          add only those via securityContext.capabilities.add."

  7. rule_iam_least_privilege(manifest)
     For now: checks that no ServiceAccount has automountServiceAccountToken: true
     (unless explicitly needed).
     why: "Auto-mounted service account tokens give every pod in the namespace API
          server access by default. If a pod is compromised the token is the attacker's
          first lateral-movement tool."
     fix: "Set automountServiceAccountToken: false on the ServiceAccount (or pod spec)
          and only mount tokens in pods that call the Kubernetes API."

- deployguard/guard/rules.py also exports:
  ALL_RULES: list of (rule_id: str, fn: callable) pairs — the registry the engine iterates.

- deployguard/guard/engine.py
  - run_policy(manifest: dict, enabled_rules: dict[str, str]) -> list[Violation]
    Iterates ALL_RULES, skips rules where enabled_rules[rule_id] == "off",
    sets severity from enabled_rules value ("error" → ERROR, "warn" → WARN).
    Returns all violations.

DoD: Each rule function exists; passing a manifest dict missing resource limits returns
a Violation with non-empty why and fix fields. `from deployguard.guard.rules import
ALL_RULES` works and returns 7 entries.

After completing, update PROGRESS.md: mark Step 3 as Done.
```

---

### Step 4 — Guard explanation engine + public API

**Goal:** Wire tools.py + rules.py together into a single `guard()` call that returns a `GuardResult` with all violations from all sources. This is the function everything else calls.

```
PROMPT:

Context: deployguard/guard/ has models.py, tools.py, rules.py, engine.py (Steps 1–3 done).
Read CLAUDE.md architecture rules. Read PRD.md §4.1 — the guard is called both from
dg init and from the standalone skill.

Task: Build the public guard API — the single function every caller uses.

What to build:

deployguard/guard/runner.py
- guard(
    path: str,                         # directory or single file to validate
    enabled_rules: dict[str, str],     # from config — rule_id → "error"|"warn"|"off"
    run_kubeconform: bool = True,
    run_trivy: bool = True,
  ) -> GuardResult
  
  Logic:
  1. Find all .yaml/.yml files under path (or just the single file if path is a file).
  2. Parse each file as YAML (handle multi-document YAML with ---, yield each doc).
  3. For each manifest: run run_kubeconform (if enabled), run run_trivy_config (if enabled),
     run run_policy (always).
  4. Collect all violations across all files.
  5. Return GuardResult(passed=len([v for v in violations if v.severity==ERROR])==0,
     violations=violations).

deployguard/guard/reporter.py
- format_result(result: GuardResult, explain: bool = True) -> str
  Returns a Rich-compatible string (use Rich markup: [red], [yellow], [green]).
  Format per violation:
    [red]ERROR[/red] <rule_id> in <path>
      <message>
      Why it matters: <why>          ← only when explain=True
      What to add: <fix>             ← only when explain=True
  Summary line: "X error(s), Y warning(s) — guard passed/failed."

Update deployguard/guard/__init__.py to export: guard, GuardResult, Violation, format_result.

DoD: `from deployguard.guard import guard, format_result` works; calling guard() on a
directory of test manifests returns a GuardResult; format_result() returns a string
containing "Why it matters:" when explain=True and omits it when False.

After completing, update PROGRESS.md: mark Step 4 as Done.
```

---

### Step 5 — test_guard.py

**Goal:** Write the guard test suite. Tests must assert on violation message content and explanation text — not just that a violation was returned.

```
PROMPT:

Context: deployguard/guard/ public API is complete (Steps 1–4 done). Read PRD.md §M-1
DoD: "assert on the explanation text, not just the reject."

Task: Write tests/test_guard.py. Use pytest. No real kubeconform/trivy binaries needed
— mock subprocess.run in tests that exercise tools.py. Use real manifest dicts for
policy rule tests.

Test cases to implement:

1. test_resource_limits_missing — a Deployment manifest with no resources block →
   GuardResult.passed == False, one violation with rule_id == "require_resource_limits",
   violation.why contains "starve the node", violation.fix contains "resources.requests".

2. test_probes_missing — Deployment missing livenessProbe →
   violation.rule_id == "require_probes", why contains "traffic to pods that aren't ready".

3. test_security_context_missing — pod spec with no securityContext →
   violation.rule_id == "require_security_context".

4. test_latest_tag — container image "myapp:latest" →
   violation.rule_id == "no_latest_tag", why contains "mutable".

5. test_root_user — container with runAsUser: 0 →
   violation.rule_id == "no_root_user".

6. test_privileged_container — container with privileged: true →
   violation.rule_id == "no_privileged_containers".

7. test_passing_manifest — a fully compliant Deployment with resources, probes,
   non-root, non-privileged, pinned tag, securityContext → GuardResult.passed == True,
   violations == [].

8. test_warn_vs_error — with iam_least_privilege set to "warn", a ServiceAccount
   with automountServiceAccountToken: true produces a WARN, not an ERROR,
   and GuardResult.passed == True (warnings don't fail the guard).

9. test_format_result_with_explain — format_result(result, explain=True) output
   contains "Why it matters:" and "What to add:".

10. test_format_result_without_explain — format_result(result, explain=False) output
    does NOT contain "Why it matters:".

DoD: `pytest tests/test_guard.py` passes all 10 tests.

After completing, update PROGRESS.md: mark Step 5 as Done.
```

---

### Step 6 — SKILL.md (standalone Claude skill)

**Goal:** Write the Claude skill file that wraps the guard module. This is the M-1 deliverable — a file someone drops into `.claude/skills/` to get guard validation inside any Claude Code session.

```
PROMPT:

Context: deployguard/guard/ is complete and tested (Steps 1–5 done). Read PRD.md §M-1
for the skill's DoD. Read PRD.md §4.1 for what the guard checks and explains.

Task: Write skill/SKILL.md — the standalone Claude Code skill.

The skill should:
1. Be triggerable as /guard in a Claude Code session.
2. Accept either: a path to a directory or file, OR a pasted manifest block.
3. Run the guard module (via subprocess call to a helper script, or by importing
   deployguard.guard if the package is installed) on the target.
4. Display the full format_result() output — violations with why/fix explanations.
5. If the guard passes, say so clearly and briefly.
6. If the guard fails, list violations clearly grouped by file, then give a summary.

The SKILL.md must include:
- Trigger: `/guard`
- A short description of what it does (1–2 sentences).
- Instructions for installation: drop this file into `.claude/skills/guard/SKILL.md`.
- Prerequisites: deployguard package installed (`pip install dg-deploy`) OR the
  guard module available locally.
- The full prompt/instructions that Claude follows when the skill is invoked.
- An example invocation and expected output format.

The skill instructions to Claude must emphasize:
- Show the why-it-matters and what-to-add for every violation.
- Group violations by file.
- End with a clear pass/fail summary line.
- Do not just list errors — act as a senior reviewer explaining each one.

DoD: The SKILL.md exists; a developer can drop it into `.claude/skills/` and invoke
`/guard k8s/` to get violations with explanations for any manifests in that directory.

After completing, update PROGRESS.md: mark Step 6 as Done. M-1 is complete.
```

---

## M0 — The Spine

### Step 7 — CLI entrypoint + dg doctor stub

**Goal:** Replace the placeholder CLI stubs with real Typer commands that have proper argument signatures and help text. Implement `dg doctor` to check prerequisites.

```
PROMPT:

Context: Project skeleton and guard module exist (Steps 1–6 done). Read CLAUDE.md and
PRD.md §4, §4.4 (doctor command) and §4.6 (minimal flag surface).

Task: Build the real CLI in deployguard/cli.py and implement dg doctor.

deployguard/cli.py:
- Typer app with these commands (real signatures, not stubs):
  - doctor() — no args, no flags. Checks prerequisites, prints results, exits 1 if any missing.
  - init(name: str) — positional. Flag: --no-guard (skip guard, default False).
  - cost(path: str = ".") — optional path arg. Flag: --explain (verbose, default False).
  - provision() — no args. Flag: --target (local|aws, overrides config).
  - deploy() — no args. Flag: --target (local|aws, overrides config).
- All commands except doctor print "not implemented yet" and return (except doctor).
- Use Rich for all terminal output (Console, Panel, Table where appropriate).

deployguard/cli.py doctor():
Checks (in order, print result for each):
  1. python >= 3.11 — `sys.version_info`
  2. docker — `docker info`
  3. kubectl — `kubectl version --client`
  4. helm — `helm version`
  5. minikube — `minikube version`
  6. kubeconform — `kubeconform -v`
  7. trivy — `trivy --version`
  8. terraform — `terraform version`
  9. infracost — `infracost --version`

Output per tool: green checkmark if found, red X if missing (include install hint).
Summary: "All prerequisites met." or "X prerequisite(s) missing — run the commands above."
Exit 1 if any prerequisite missing, exit 0 if all present.

Do NOT check AWS credentials or config files yet (that's M1).

DoD: `dg doctor` runs, checks all 9 prerequisites, shows colored output, exits correctly.
`dg --help` shows all five commands with correct signatures.

After completing, update PROGRESS.md: mark Step 7 as Done.
```

---

### Step 8 — config.py (basic loader with defaults)

**Goal:** Build the config loader with built-in defaults only. No merge logic yet — just enough to unblock init/provision/deploy. Full merge comes in Step 16.

```
PROMPT:

Context: CLI stubs and doctor exist (Steps 1–7 done). Read CLAUDE.md — "config.py is
the ONLY config reader." Read PRD.md §4.5 for the full config schema and priority order.

Task: Build deployguard/config.py with built-in defaults.

What to build:

1. Pydantic models for the full config schema (PRD.md §4.5):
   - LLMConfig, GuardConfig, GuardRulesConfig, CostConfig, DeployConfig, AppConfig
   - Use pydantic BaseModel with defaults matching the PRD schema.

2. load_config() -> AppConfig
   For now: return AppConfig() (all defaults). No file reading yet.
   Add a TODO comment: "Step 16 adds project + personal config file merging."

3. get_config() -> AppConfig — module-level singleton: calls load_config() once,
   caches result, returns it. Every other module calls this. Never re-reads files.

DoD: `from deployguard.config import get_config` works; `get_config().deploy.target`
returns "local"; `get_config().guard.strict` returns True;
`get_config().deploy.rollout_steps` returns [10, 50, 100].

After completing, update PROGRESS.md: mark Step 8 as Done.
```

---

### Step 9 — deployguard.yaml Pydantic schema

**Goal:** Define the per-service manifest schema that `dg init` writes and `dg deploy` reads.

```
PROMPT:

Context: config.py exists with defaults (Steps 1–8 done). Read PRD.md §4.1 (deployguard.yaml
spec) and §4.4 (deploy reads it).

Task: Build the per-service manifest schema.

deployguard/manifest.py:
- ServiceManifest(BaseModel):
    name: str                          # service name, e.g. "payments-api"
    port: int = 8000                   # container port
    replicas: int = 2
    health_liveness: str = "/healthz"
    health_readiness: str = "/readyz"
    image: str | None = None           # filled in at deploy time (git SHA tag)
    namespace: str = "default"

- load_manifest(path: str = "deployguard.yaml") -> ServiceManifest
  Reads the YAML file, validates via Pydantic, raises a clear ValueError on schema
  errors (include the field name and why).

- write_manifest(manifest: ServiceManifest, path: str = "deployguard.yaml") -> None
  Writes the manifest to YAML.

DoD: `from deployguard.manifest import ServiceManifest, load_manifest` works;
loading a valid deployguard.yaml returns a ServiceManifest; loading a file missing
`name` raises ValueError with a useful message.

After completing, update PROGRESS.md: mark Step 9 as Done.
```

---

### Step 10 — dg init: minimal templates

**Goal:** Make `dg init <name>` write a working hello-world FastAPI app and minimal k8s manifests. No guard yet. Just enough to deploy in Steps 11–12.

```
PROMPT:

Context: config, manifest, guard all exist (Steps 1–9 done). Read PRD.md §4.1 for what
dg init emits. Read CLAUDE.md for the tech stack.

Task: Implement dg init with minimal (not yet hardened) templates.

Templates to create in templates/:
- Dockerfile.j2 — single-stage Python image, COPY app/, RUN pip install, CMD uvicorn.
  Uses {{ name }} for the app name.
- app/main.py.j2 — minimal FastAPI app: GET / returns {"service": "{{ name }}"},
  GET /healthz returns {"status": "ok"}, GET /readyz returns {"status": "ready"}.
  Include `if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port={{ port }})`.
- app/requirements.txt.j2 — fastapi, uvicorn[standard].
- k8s/deployment.yaml.j2 — basic Deployment: 1 replica, image="{{ name }}:latest",
  containerPort={{ port }}. No probes, no resource limits yet (hardening is Step 14).
- k8s/service.yaml.j2 — ClusterIP Service on port {{ port }}.
- deployguard.yaml.j2 — writes a valid ServiceManifest YAML for this service.

deployguard/scaffold/init.py:
- scaffold_service(name: str, output_dir: str, config: AppConfig) -> None
  Creates output_dir/<name>/, renders all templates with Jinja2, writes files.
  Prints each file path as it's written (Rich).

Update cli.py init() command:
- Call scaffold_service(name, ".", config).
- Print summary of what was created.
- Do NOT call guard yet (that's Step 15).

DoD: `dg init hello-api` creates hello-api/ with Dockerfile, app/main.py,
app/requirements.txt, k8s/deployment.yaml, k8s/service.yaml, deployguard.yaml.
Files are syntactically valid. No guard runs.

After completing, update PROGRESS.md: mark Step 10 as Done.
```

---

### Step 11 — dg provision: local minikube

> **Implementation note:** Originally planned with k3d, but switched to minikube.
> helm ingress-nginx had persistent pending-upgrade timeouts on macOS with k3d.
> minikube with `minikube addons enable ingress` is simpler, stable, and requires no Helm for ingress.

**Goal:** Implement `dg provision` for the local target — bring up minikube, enable ingress addon, create namespace.

```
PROMPT:

Context: dg init works (Steps 1–10 done). Read PRD.md §4.3 for provision spec.
Local cluster uses minikube (not k3d) — see implementation note above.

Task: Implement dg provision for target=local in deployguard/provision/local.py.

deployguard/provision/local.py:
- provision_local(config: AppConfig) -> None
  Steps (each prints Rich status, idempotent):

  1. Check if minikube cluster "deployguard" exists: `minikube status --profile deployguard`
     If not: `minikube start --profile deployguard --driver docker`

  2. Enable ingress addon (idempotent):
     `minikube addons enable ingress --profile deployguard`

  3. Set kubectl context: `kubectl config use-context deployguard`

  4. Create namespace "deployguard" if not exists:
     `kubectl create namespace deployguard --dry-run=client -o yaml | kubectl apply -f -`

  Each step: use subprocess.run, capture output, raise RuntimeError with full stderr on
  non-zero exit. Print [green]✓[/green] on success, [red]✗[/red] on failure.

Update cli.py provision():
- Call load_config(), then provision_local() or provision_aws().

DoD: `dg provision` on a machine with minikube/kubectl creates the cluster (or skips if
exists), enables ingress, creates namespace. Re-running is safe (idempotent).

After completing, update PROGRESS.md: mark Step 11 as Done.
```

---

### Step 12 — dg deploy: local basic

**Goal:** Implement `dg deploy` for the local target — build the image, load into k3d, apply manifests, wait for readiness. No traffic shifting yet.

```
PROMPT:

Context: provision works (Steps 1–11 done). Read PRD.md §4.4 for deploy spec.
The engine/ depth (gradual rollout + rollback) comes in Steps 20–23. This step is
just "get it running" for the M0 spine.

Task: Implement basic dg deploy for local target.

deployguard/provision/deploy_local.py:
- deploy_local(manifest: ServiceManifest, config: AppConfig) -> None
  Steps:

  1. Get git SHA: `git rev-parse --short HEAD` → image_tag
     If not in a git repo, use "local".

  2. Build image: `docker build -t {manifest.name}:{image_tag} .`
     Stream output via Rich Live.

  3. Load into minikube: `minikube image load {manifest.name}:{image_tag} --profile deployguard`

  4. Update manifest image field and write back: manifest.image = f"{manifest.name}:{image_tag}"
     (in-memory only — don't overwrite deployguard.yaml).

  5. Apply k8s manifests: `kubectl apply -f k8s/ -n deployguard`

  6. Wait for rollout: `kubectl rollout status deployment/{manifest.name} -n deployguard
     --timeout=120s`

  7. Print service URL (cluster-local for now).

Update cli.py deploy():
- load_config(), load_manifest("deployguard.yaml"), call deploy_local().

DoD: From inside a directory created by `dg init`, running `dg deploy` builds the image,
loads it into k3d, applies the manifests, and waits for the pod to be ready.

After completing, update PROGRESS.md: mark Step 12 as Done.
```

---

### Step 13 — test_smoke_e2e.py

**Goal:** Write the spine test that must always pass. This is the branch-protection gate.

```
PROMPT:

Context: The full M0 spine works — init → provision → deploy (Steps 1–12 done).
Read PRD.md M0 DoD and §10 ("Never merge a change that breaks test_smoke_e2e.py").

Task: Write tests/test_smoke_e2e.py — the spine integration test.

The test must:
1. Create a temp directory.
2. Run `dg init test-svc` in it (subprocess).
3. Assert the expected files exist: Dockerfile, app/main.py, k8s/deployment.yaml,
   k8s/service.yaml, deployguard.yaml.
4. Assert deployguard.yaml is valid (load_manifest() succeeds).
5. Do NOT run provision or deploy in CI (they need Docker/minikube). Skip with
   pytest.mark.skipif(not shutil.which("minikube"), reason="minikube not available").
6. For the Docker-dependent steps, add a separate test class marked
   @pytest.mark.integration that runs dg provision + dg deploy and asserts the
   deployment reaches Running state.

The unit portion (steps 1–4) must always run and pass in any environment.
The integration portion requires k3d and is skipped otherwise.

DoD: `pytest tests/test_smoke_e2e.py` passes on a machine without minikube (unit tests only);
`pytest tests/test_smoke_e2e.py -m integration` passes on a machine with minikube.

After completing, update PROGRESS.md: mark Step 13 as Done. M0 is complete.
```

---

## M1 — Harden + Cost + Config

### Step 14 — Golden-path templates (hardened)

```
PROMPT:

Context: M0 spine works (Steps 1–13 done). Read PRD.md §4.1 — the full list of what
dg init must emit. Read CLAUDE.md tech stack.

Task: Replace the minimal templates from Step 10 with hardened golden-path templates.

Replace/add in templates/:
- Dockerfile.j2 — multi-stage: builder stage (pip install), runtime stage (non-root user
  UID 1000, HEALTHCHECK, pinned python:3.11-slim base, no :latest anywhere).
- app/main.py.j2 — same but add startup event logging.
- k8s/deployment.yaml.j2 — add: resources.requests + resources.limits (cpu/memory),
  livenessProbe (/healthz), readinessProbe (/readyz), securityContext
  (runAsNonRoot: true, runAsUser: 1000, allowPrivilegeEscalation: false,
  readOnlyRootFilesystem: true), image uses {{ image_tag }} not :latest.
- k8s/namespace.yaml.j2 — Namespace with a ResourceQuota (cpu + memory caps).
- k8s/service.yaml.j2 — unchanged.
- k8s/ingress.yaml.j2 — nginx Ingress for the service.
- .github/workflows/ci.yaml.j2 — build, test, push to ECR (placeholder push step),
  trigger dg deploy.
- infra/main.tf.j2 — Terraform stub: provider aws, placeholder EC2 + RDS resources.
  No NAT Gateway. Comments indicating what each resource costs.
- .env.example.j2 — DATABASE_URL, SECRET_KEY — no real values.
- README.md.j2 — generated README with the four dg commands.

DoD: `dg init hardened-svc` creates all files; the Deployment manifest passes guard
validation (test_guard.py's test_passing_manifest test).

After completing, update PROGRESS.md: mark Step 14 as Done.
```

---

### Step 15 — Guard absorbed into dg init

```
PROMPT:

Context: Hardened templates exist (Steps 1–14 done). Read PRD.md §4.1 guard layer spec.
Read CLAUDE.md: "Guard layer is load-bearing. Nothing ships without passing it."

Task: Wire the guard into dg init.

Update deployguard/scaffold/init.py scaffold_service():
1. After rendering templates into a temp directory, run guard() on the temp directory.
2. If guard fails (GuardResult.has_errors == True) AND config.guard.strict == True:
   - Print format_result(result, explain=config.guard.explain) via Rich.
   - Print "Guard failed — files not written. Fix violations above." in red.
   - Delete temp directory.
   - sys.exit(1).
3. If guard fails AND config.guard.strict == False:
   - Print format_result() as warnings.
   - Continue — write files to final location.
4. If guard passes: write files, print summary.

The guard must run on the generated files BEFORE they are written to the final destination.
Use a tempfile.TemporaryDirectory(), render there, guard, then copy to final path on pass.

Update cli.py init():
- Pass --no-guard flag through: if --no-guard, skip guard entirely (log a warning).

DoD: `dg init bad-svc` with a broken template (introduce a deliberate violation) prints
the violation with explanation and exits 1. `dg init good-svc` passes guard and writes files.
`dg init good-svc --no-guard` skips guard and writes files with a warning.

After completing, update PROGRESS.md: mark Step 15 as Done.
```

---

### Step 16 — config.py full merge logic

```
PROMPT:

Context: config.py has defaults only (Steps 1–15 done). Read PRD.md §4.5 fully —
priority order, two config files, full schema. Read CLAUDE.md: "config.py is the ONLY
config reader."

Task: Implement the full config merge in deployguard/config.py.

Priority (highest → lowest):
  CLI flag → project config (.deployguard/config.yaml) → personal config
  (~/.deployguard/config.yaml) → built-in defaults

load_config(cli_overrides: dict | None = None) -> AppConfig:
1. Start with AppConfig() defaults.
2. Load ~/.deployguard/config.yaml if it exists — deep merge over defaults.
3. Load .deployguard/config.yaml (relative to cwd) if it exists — deep merge over personal.
4. Apply cli_overrides dict (only non-None values) — merge over project.
5. Validate final merged config with Pydantic (raise clear error on invalid keys/values).
6. Load custom rules from config.guard.custom_rules_dir if it exists (collect .py files;
   actual loading happens in guard/engine.py but config.py resolves the path).

Deep merge: for nested dicts, merge recursively (don't replace the whole sub-dict).
Both config files are optional — missing file = silently skip.

Update get_config() to call load_config() (no args) and cache.
Add reset_config() for tests (clears the cache).

DoD: `pytest tests/test_config.py` passes (write these tests alongside the implementation):
- Default values are correct.
- Personal config overrides defaults.
- Project config overrides personal.
- Invalid config key raises a clear Pydantic ValidationError.
- reset_config() clears cache between tests.

After completing, update PROGRESS.md: mark Step 16 as Done.
```

---

### Step 17 — dg doctor (full)

```
PROMPT:

Context: Full config merge exists (Steps 1–16 done). Read PRD.md §4 (doctor spec) and
§4.5 (config schema) for what doctor should validate.

Task: Extend dg doctor to also validate config files.

Add to cli.py doctor() after the tool prerequisite checks:
1. Config file validation:
   - Try load_config() — if it raises, print the Pydantic error clearly and mark as failed.
   - If .deployguard/config.yaml exists, print its path and "valid" or the error.
   - If ~/.deployguard/config.yaml exists, print its path and "valid" or the error.
   - If custom_rules_dir is set, check the directory exists and .py/.rego files are present.
2. deployguard.yaml validation (if it exists in cwd):
   - Try load_manifest() — print "valid ServiceManifest" or the error.
3. Environment checks (AWS credentials for aws target):
   - Only check if config.deploy.target == "aws".
   - Check AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY env vars are set (or ~/.aws/credentials exists).
4. Print final summary: "All checks passed." or list what's wrong.

DoD: `dg doctor` with a valid config prints all green; with an invalid config key it
prints a clear error naming the invalid key; with a missing tool it lists the install command.

After completing, update PROGRESS.md: mark Step 17 as Done.
```

---

### Step 18 — dg cost

```
PROMPT:

Context: Full config and guard exist (Steps 1–17 done). Read PRD.md §4.2 fully — the
three sections of dg cost, the cost-policy rules, the --explain flag.

Task: Implement dg cost in deployguard/cost/.

deployguard/cost/parser.py:
- parse_terraform_resources(tf_dir: str) -> list[dict]
  Runs `terraform init -backend=false` then `terraform show -json` (or falls back to
  parsing .tf files directly if no state exists). Returns list of resource dicts with
  type, name, config.
- parse_k8s_resources(k8s_dir: str) -> list[dict]
  Parses all yaml files in k8s/, returns list with kind, name, replicas, storage.

deployguard/cost/infracost.py:
- run_infracost(tf_dir: str) -> dict
  Runs `infracost breakdown --path <tf_dir> --format json`, returns parsed JSON.
  If infracost not installed, raise RuntimeError pointing to dg doctor.

deployguard/cost/rules.py:
- Cost policy rules — each is a function(resources: list[dict]) -> list[CostViolation]:
  CostViolation(BaseModel): rule_id, severity (WARN|REJECT), message, why, monthly_impact_estimate
  Rules (PRD §4.2):
  1. rule_nat_gateway — any aws_nat_gateway resource → REJECT, ~$32/mo
  2. rule_on_demand_instance — ec2 instances not using spot → WARN
  3. rule_oversized_instance — instance type larger than t3.small → WARN
  4. rule_unattached_eip — EIP with no association → WARN, ~$3.65/mo
  5. rule_load_balancer — any ALB/NLB → WARN, ~$16+/mo
  6. rule_uncapped_rds_storage — RDS with allocated_storage > 20 and no max_allocated_storage → WARN
  7. rule_multiplied_resource — count or for_each on a billable resource → WARN
  8. rule_no_pause_path — any standing-cost resource with no corresponding destroy/stop path → WARN

deployguard/cost/reporter.py:
- format_cost_report(resources, infracost_result, violations, explain: bool) -> str
  Three sections (Rich tables):
  Section 1: "What will spin up" — table of resources.
  Section 2: "Estimated monthly cost" — running and paused figures from infracost.
  Section 3: "Bill-inflation risks" — violations table.
  Footer: "This is an estimate. Actual costs depend on usage."

Update cli.py cost():
- parse path arg (default "."), run parser + infracost + rules, format, print.
- Check warn_threshold and reject_threshold from config — print warnings / exit 1 accordingly.
- Pass --explain flag through to format_cost_report.

DoD: `dg cost` on the generated infra/ stub prints three sections; a Terraform file with
an aws_nat_gateway triggers a REJECT violation with why text about $32/mo.

After completing, update PROGRESS.md: mark Step 18 as Done. M1 is complete.
```

---

### Step 19 — test_cost.py + test_config.py

```
PROMPT:

Context: dg cost and full config.py exist (Steps 1–18 done). The test files may have
been partially written as part of Steps 16 and 18. Finalize them now.

Task: Write/complete tests/test_cost.py and tests/test_config.py.

tests/test_cost.py:
1. test_nat_gateway_rejected — fake terraform resource list with aws_nat_gateway →
   rule_nat_gateway returns a REJECT violation with "32" in the why text.
2. test_on_demand_warn — ec2 instance without spot → WARN violation.
3. test_no_violations_clean_infra — minimal spot-only infra → empty violations list.
4. test_cost_report_format — format_cost_report() output contains "What will spin up",
   "Estimated monthly cost", "Bill-inflation risks", and the estimate disclaimer.
5. test_infracost_missing — run_infracost() raises RuntimeError when binary absent.

tests/test_config.py:
1. test_defaults — get_config() returns correct defaults (strict=True, target="local",
   rollout_steps=[10,50,100]).
2. test_personal_config_override — write a tmp personal config, load, assert override.
3. test_project_config_override — write a tmp project config, load, assert it overrides personal.
4. test_invalid_key_raises — config with unknown key raises ValidationError with field name.
5. test_priority_order — flag override (deploy.target="aws") overrides project config "local".
6. test_reset_cache — reset_config() + change project config → load_config() picks up change.

DoD: `pytest tests/test_cost.py tests/test_config.py` all pass.

After completing, update PROGRESS.md: mark Step 19 as Done.
```

---

## M2 — Deploy Engine (the Demo)

### Step 20 — engine/ pre-check + smoke test

```
PROMPT:

Context: M1 complete (Steps 1–19 done). Read PRD.md §4.4 steps 3 and 4 — the pre-check
logic before live traffic is touched. Read CLAUDE.md: "Keep core logic out of CLI handlers."

Task: Build deployguard/engine/precheck.py — the pre-deployment validation.

deployguard/engine/precheck.py:
- PreCheckResult(BaseModel): passed: bool, reason: str | None
- run_precheck(manifest: ServiceManifest, config: AppConfig) -> PreCheckResult
  1. Apply manifests to a "green" deployment (deployment name: "{name}-green"):
     `kubectl apply -f k8s/ -n {namespace}` with image set to new version.
  2. Wait for readiness: `kubectl rollout status deployment/{name}-green -n {namespace}
     --timeout={config.deploy.smoke_timeout}s`
     On timeout → PreCheckResult(passed=False, reason="Readiness timeout").
  3. Smoke test: HTTP GET to the service's /readyz endpoint (port-forward temporarily,
     or use kubectl exec curl).
     On non-200 → PreCheckResult(passed=False, reason=f"/readyz returned {status_code}").
  4. If all pass → PreCheckResult(passed=True, reason=None).
  5. On failure: delete the green deployment before returning.

deployguard/engine/__init__.py — export run_precheck, PreCheckResult.

DoD: run_precheck() can be called in a test with mocked kubectl and requests; returns
PreCheckResult with correct passed/reason for timeout, HTTP failure, and success cases.

After completing, update PROGRESS.md: mark Step 20 as Done.
```

---

### Step 21 — engine/ gradual traffic shift (Rich display)

```
PROMPT:

Context: Pre-check exists (Steps 1–20 done). Read PRD.md §4.4 step 4 — gradual rollout
via nginx-ingress canary weights, shown in Rich Live. The rollout_steps come from config.

Task: Build deployguard/engine/rollout.py — traffic shifting with live display.

deployguard/engine/rollout.py:
- RolloutStep(BaseModel): weight: int, status: str ("pending"|"active"|"done"|"failed")
- rollout_traffic(
    manifest: ServiceManifest,
    config: AppConfig,
    on_step: callable[[int, int], bool],  # called with (step_index, weight) → continue?
  ) -> bool  # True = completed, False = rolled back
  
  1. Create canary Deployment: "{name}-canary" pointing to new image.
  2. Create canary Service + Ingress with nginx canary annotations:
     nginx.ingress.kubernetes.io/canary: "true"
     nginx.ingress.kubernetes.io/canary-weight: "<weight>"
  3. For each weight in config.deploy.rollout_steps:
     a. Patch the canary Ingress canary-weight annotation.
     b. Wait 10 seconds (let traffic flow).
     c. Call on_step(step_index, weight) — if returns False, rollback and return False.
     d. Update Rich Live display (progress bar showing current weight %).
  4. On completion (100%): promote canary to stable (replace stable Deployment image),
     delete canary resources.
  5. Return True.

- rollback(manifest: ServiceManifest) -> None
  Deletes canary resources and runs `kubectl rollout undo deployment/{name} -n {namespace}`.

Update deployguard/engine/__init__.py exports.

DoD: rollout_traffic() with mock kubectl calls iterates steps and calls on_step at each;
rollback() is called when on_step returns False. Rich Live display shows the weight steps.

After completing, update PROGRESS.md: mark Step 21 as Done.
```

---

### Step 22 — engine/ Prometheus watch

```
PROMPT:

Context: Traffic shifting exists (Steps 1–21 done). Read PRD.md §4.4 step 5 —
Prometheus error-rate watch per step.

Task: Build deployguard/engine/metrics.py — Prometheus HTTP API query.

deployguard/engine/metrics.py:
- get_error_rate(
    service_name: str,
    namespace: str,
    prometheus_url: str = "http://localhost:9090",
    window: str = "1m",
  ) -> float  # error rate as percentage 0.0–100.0

  Query: rate of 5xx responses / total requests for the service in the last `window`.
  PromQL: sum(rate(nginx_ingress_controller_requests{service=~"<service>",status=~"5.."}[1m]))
          / sum(rate(nginx_ingress_controller_requests{service=~"<service>"}[1m])) * 100
  
  HTTP GET http://{prometheus_url}/api/v1/query?query=<encoded_promql>
  Parse JSON response → extract value → return as float.
  If Prometheus unreachable: return 0.0 (log warning — don't abort a deploy because
  metrics are down; the smoke test is the hard gate).

Wire into rollout.py: the on_step callback passed to rollout_traffic() queries
get_error_rate() and returns False if rate > config.deploy.error_rate_threshold.

DoD: get_error_rate() with a mocked requests.get returning a valid Prometheus response
returns the correct float; returns 0.0 on connection error; the rollout's on_step
returns False when rate exceeds threshold.

After completing, update PROGRESS.md: mark Step 22 as Done.
```

---

### Step 23 — engine/ auto-rollback + full deploy wiring

```
PROMPT:

Context: Pre-check, traffic shift, and metrics all exist (Steps 1–22 done).
Read PRD.md §4.4 fully — the complete deploy sequence.

Task: Wire everything into the full deploy flow in deployguard/engine/deploy.py
and update cli.py.

deployguard/engine/deploy.py:
- deploy(manifest: ServiceManifest, config: AppConfig) -> bool  # True = success
  Full sequence per PRD §4.4:
  1. Build image (subprocess docker build), tag with git SHA.
  2. Push/load (local → k3d load; aws → ECR push; ECR part is a stub for now).
  3. Pre-check via run_precheck(). If failed: print reason, return False.
  4. Gradual rollout via rollout_traffic() with the Prometheus on_step callback.
     Show Rich Live progress panel: current step, weight%, error rate.
  5. If rollout returns False: auto-rollback was triggered. Print "Rolled back. Live traffic unaffected." Return False.
  6. If rollout returns True: print "Deploy complete. 100% traffic on new version." Return True.
  
  All steps print Rich status. On any exception: call rollback() and re-raise.

Update cli.py deploy() to call deploy(manifest, config) and exit 1 on False.

DoD: A simulated deploy with mocked kubectl/docker/prometheus runs the full sequence;
a simulated error-rate spike triggers rollback; the Rich display shows step progression.
`pytest` still passes.

After completing, update PROGRESS.md: mark Step 23 as Done.
```

---

### Step 24 — Audit log (SQLAlchemy → Postgres)

```
PROMPT:

Context: Full deploy flow exists (Steps 1–23 done). Read PRD.md §4.4 step 7 — audit row.

Task: Build deployguard/engine/audit.py.

deployguard/engine/audit.py:
- SQLAlchemy model:
  class DeployAudit(Base):
      id: int (PK)
      service: str
      image_tag: str
      target: str  ("local"|"aws")
      started_at: datetime
      finished_at: datetime
      result: str  ("success"|"rollback"|"precheck_failed")
      reason: str | None  (rollback/failure reason)
      rollout_steps: JSON  (list of {weight, error_rate, timestamp})
      operator: str  (git user.email or $USER)

- write_audit(session: Session, **fields) -> DeployAudit
- get_db_session(database_url: str) -> Generator[Session, None, None]

Wire into deploy.py: if config.deploy.audit == True, write an audit row on completion
or failure. Use DATABASE_URL env var for the connection string. If DATABASE_URL is not
set or Postgres is unreachable, log a warning and continue (audit is non-blocking).

DoD: write_audit() with a SQLite in-memory session (for tests) creates a row with all
fields; deploy.py calls it; missing DATABASE_URL logs a warning without crashing.

After completing, update PROGRESS.md: mark Step 24 as Done. M2 is complete.
```

---

## M3 — AWS + Self-Deploy

### Step 25 — provision/ AWS target (Terraform)

```
PROMPT:

Context: M2 complete (Steps 1–24 done). Read PRD.md §4.3 aws provision spec and §6
(tech stack: k3s on EC2 Spot, no NAT Gateway, Elastic IP, Route 53 + ACM).
Read PRD.md §11 cost model.

Task: Build the AWS provision path.

infra/main.tf — complete Terraform for k3s on EC2 Spot:
- VPC + public subnet (no NAT Gateway).
- Security group (port 22, 80, 443, 6443).
- EC2 Spot instance (t3.small, Amazon Linux 2023) with user_data that installs k3s.
- Elastic IP associated to the instance.
- Route 53 A record for the configured domain → EIP.
- ACM certificate for TLS (DNS validation).
- Outputs: cluster_endpoint, eip_address.
- All resources tagged with "deployguard=true" for cost tracking.
- Explicitly no aws_nat_gateway (comment explaining why — cost).

deployguard/provision/aws.py:
- provision_aws(config: AppConfig) -> None
  1. Run `terraform init` then `terraform apply -auto-approve` in infra/.
  2. Parse terraform outputs to get cluster endpoint.
  3. Download kubeconfig from the EC2 instance via SSH (use paramiko or subprocess ssh).
  4. Merge into ~/.kube/config, set context to "deployguard-aws".
  5. Run the same in-cluster setup as local (nginx-ingress Helm, namespace, resource quota).

Makefile targets (implement):
- pause: `terraform apply -auto-approve -var="instance_count=0"` (or stop EC2).
- resume: `terraform apply -auto-approve -var="instance_count=1"`.
- destroy: `terraform destroy -auto-approve`.

DoD: `dg provision --target aws` runs terraform and configures kubectl (test with
--dry-run flag or a mock). Makefile pause/resume/destroy run terraform.

After completing, update PROGRESS.md: mark Step 25 as Done.
```

---

### Step 26 — deploy/ AWS (ECR push + remote)

```
PROMPT:

Context: AWS provision exists (Steps 1–25 done). Read PRD.md §4.4 deploy spec —
aws target uses ECR push instead of k3d load.

Task: Implement the AWS deploy path in deployguard/engine/deploy.py.

Update deploy.py step 2 (push/load) to branch on config.deploy.target:
- "local": k3d image import (existing)
- "aws": ECR push
  deployguard/engine/ecr.py:
  - push_to_ecr(image_name: str, tag: str, region: str, account_id: str) -> str
    1. Get ECR login: `aws ecr get-login-password | docker login --username AWS ...`
       Use boto3 to get the token (cleaner than subprocess).
    2. Tag: docker tag {image_name}:{tag} {account_id}.dkr.ecr.{region}.amazonaws.com/{image_name}:{tag}
    3. Push: docker push ...
    4. Return the full ECR image URI.

The ECR repository name matches the service name. Create it if it doesn't exist
(boto3 create_repository with --if-not-exists logic).

DoD: push_to_ecr() with mocked boto3 and docker subprocess calls runs without error;
deploy() with target=aws calls push_to_ecr instead of k3d load.

After completing, update PROGRESS.md: mark Step 26 as Done.
```

---

### Step 27 — dg cost accuracy pass

```
PROMPT:

Context: AWS infra terraform exists (Steps 1–26 done). Read PRD.md §4.2 cost spec
and §11 cost model table (~$6.25/mo running, ~$3.55/mo paused).

Task: Run `dg cost` against the real infra/main.tf and validate accuracy.

1. Run `dg cost infra/` with infracost installed.
2. Compare output against PRD §11 table — running ~$6.25/mo, paused ~$3.55/mo.
3. If estimates are significantly off: update infra/main.tf (instance type, RDS size)
   to match the cost targets. The goal is no NAT Gateway and <$7/mo running.
4. Add a test in test_cost.py: parse the actual infra/main.tf, run cost rules,
   assert no NAT Gateway violation (rule_nat_gateway returns empty list).
5. Verify `make pause` reduces the estimated running cost.

DoD: `dg cost infra/` produces a cost estimate within 20% of the PRD §11 targets;
no NAT Gateway is present; rule_nat_gateway returns no violations on this infra.

After completing, update PROGRESS.md: mark Step 27 as Done.
```

---

### Step 28 — GitHub Actions CI pipeline

```
PROMPT:

Context: Full local + AWS deploy working (Steps 1–27 done). Read PRD.md M3 — GitHub
Actions runs `dg deploy` end-to-end on git push.

Task: Implement the CI pipeline.

.github/workflows/ci.yaml:
  on: push (main), pull_request
  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - checkout
        - python 3.11 setup
        - pip install -e ".[dev]"
        - pytest tests/ -m "not integration" (skip k3d tests in CI)
        - pytest --cov=deployguard --cov-fail-under=70
    
    deploy:
      needs: test
      runs-on: self-hosted  # MacBook runner
      if: github.ref == 'refs/heads/main'
      steps:
        - checkout
        - pip install -e ".[dev]"
        - dg deploy
      env:
        DATABASE_URL: ${{ secrets.DATABASE_URL }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}

Branch protection rule (document in README, can't configure via code):
  - Require `test` job to pass before merging.
  - "Never merge red test_smoke_e2e.py" enforced here.

DoD: CI yaml is valid; pushing to a branch runs the test job; pushing to main triggers
deploy job. test_smoke_e2e.py runs in CI (unit portion).

After completing, update PROGRESS.md: mark Step 28 as Done.
```

---

### Step 29 — DeployGuard deploys itself

```
PROMPT:

Context: Full pipeline working (Steps 1–28 done). Read PRD.md §2 — "DeployGuard deploys
itself, the same way it deploys everything else."

Task: Add a deployguard.yaml and k8s manifests so DeployGuard can deploy itself.

1. Create deployguard.yaml in the repo root (the tool's own service manifest):
   name: deployguard
   port: 8080
   replicas: 1
   health_liveness: /healthz
   health_readiness: /readyz

2. Add a minimal FastAPI health endpoint to deployguard itself:
   deployguard/server.py — GET /healthz, GET /readyz returning {"status": "ok"}.
   This is NOT the sample app — it's the tool exposing its own health endpoints
   so the deploy engine can monitor itself.

3. Create k8s/ manifests for the tool (using its own hardened templates as a model).

4. Run `dg init --no-guard` to generate k8s/ (or write them directly — the tool
   has its own hardened templates to follow).

5. Run `dg guard k8s/` (or guard via dg init) — all manifests must pass.

6. Run `dg deploy` — DeployGuard deploys itself to the local k3d cluster.

7. Verify: the deployed pod responds on /healthz and /readyz.

DoD: `dg deploy` run from the DeployGuard repo root deploys DeployGuard itself;
/healthz returns 200; the audit log records the event.

After completing, update PROGRESS.md: mark Step 29 as Done. M3 is complete.
```

---

## M4 — Stretch (only after M3 is solid)

### Step 30 — llm/ LM Studio adapter

```
PROMPT:

Context: M3 complete. Read PRD.md §4.1 (LLM as drafter for init), §6.1 (LLM backends),
§4.5 (llm config). Read CLAUDE.md: "LLM is always the drafter; guard always has final say."

Task: Build the local LM Studio LLM adapter.

deployguard/llm/base.py:
- LLMAdapter(ABC): abstract method generate(prompt: str, schema: dict) -> dict
  schema is a JSON Schema for structured output. Returns validated dict.

deployguard/llm/lmstudio.py:
- LMStudioAdapter(LLMAdapter):
  POST http://localhost:1234/v1/chat/completions (OpenAI-compatible API) with
  model from config, messages=[{"role": "user", "content": prompt}],
  response_format={"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": true}}.
  Parse JSON from choices[0].message.content. Raise on non-200 or invalid JSON.
  Model default: qwen2.5-coder-14b-instruct (must match the model name loaded in LM Studio).

deployguard/llm/__init__.py:
- get_adapter(config: AppConfig) -> LLMAdapter
  Returns LMStudioAdapter if backend="local", BedrockAdapter if backend="bedrock"
  (BedrockAdapter is a stub that raises NotImplementedError until Step 31).

Wire into scaffold/init.py (optional path — only if config.llm is set and M4 is active):
- Instead of pure Jinja2 templates, call llm adapter to draft the Dockerfile and
  deployment.yaml, then run guard on the output. If guard fails, retry once with
  the violations as feedback. Guard always has final say.

DoD: LMStudioAdapter.generate() with a mocked HTTP response returns the parsed dict;
get_adapter() returns the right adapter class based on config.

After completing, update PROGRESS.md: mark Step 30 as Done.
```

---

### Step 31 — llm/ Bedrock adapter

```
PROMPT:

Context: LM Studio adapter exists (Steps 1–30 done). Read PRD.md §6.1 — Bedrock is the
cloud fallback using Claude on AWS.

Task: Build the AWS Bedrock adapter.

deployguard/llm/bedrock.py:
- BedrockAdapter(LLMAdapter):
  Use boto3 bedrock-runtime client.
  Model: anthropic.claude-3-5-sonnet-20241022-v2:0 (or latest Claude on Bedrock).
  Call invoke_model() with the prompt and a tool_use definition matching the schema
  (structured output via tool use).
  Parse the tool_use response block, return the input dict.

Update get_adapter() to return BedrockAdapter when backend="bedrock".

DoD: BedrockAdapter.generate() with a mocked boto3 client returns the correct dict;
switching config.llm.backend from "local" to "bedrock" swaps the adapter with no
other code changes.

After completing, update PROGRESS.md: mark Step 31 as Done.
```

---

### Step 32 — dg init with LLM generation

```
PROMPT:

Context: Both LLM adapters exist (Steps 1–31 done). Read PRD.md §4.1 — the LLM drafts,
the guard validates, retry once on failure.

Task: Add LLM-assisted generation to dg init as an optional path.

Update scaffold/init.py:
- If config.llm is set (backend is not None):
  1. Use LLM adapter to draft Dockerfile + deployment.yaml (not all templates — just
     the ones that benefit from LLM flexibility).
  2. Run guard on the draft.
  3. If guard fails: build a retry prompt that includes each violation's why + fix.
     Retry once. If still failing, fall back to Jinja2 templates (log that LLM draft
     failed guard twice).
  4. If guard passes: use LLM-drafted files, Jinja2 for the rest.

- If config.llm is not set: pure Jinja2 templates (existing path).

DoD: With LM Studio running locally, `dg init llm-svc` uses the LLM to draft the
Dockerfile; guard runs on the output; a violation in the first draft is included in
the retry prompt. The Jinja2 path still works when llm config is absent.

After completing, update PROGRESS.md: mark Step 32 as Done.
```

---

### Step 33 — MCP server wrapper

```
PROMPT:

Context: M3 + LLM adapters complete. Read PRD.md §4 (interface philosophy) —
"the CLI is just one caller; an MCP server can call the same core."

Task: Expose guard/ and engine/ as an MCP server.

Create deployguard/mcp_server.py using the MCP Python SDK (pip install mcp).
Expose these tools:
1. guard_manifests(path: str) -> GuardResult as JSON
   Calls deployguard.guard.guard() and returns the result.
2. get_deploy_status(service: str) -> dict
   Queries kubectl for the current deployment status of a service.
3. run_deploy(service: str) -> bool
   Calls deployguard.engine.deploy.deploy() — for use from Claude Code or other MCP clients.

Add an mcp entry point in pyproject.toml: dg-mcp = deployguard.mcp_server:main

DoD: The MCP server starts (`dg-mcp`); a Claude Code session with the server configured
can call guard_manifests() and get results; the server imports from the same modules as the CLI.

After completing, update PROGRESS.md: mark Step 33 as Done. M4 complete.
```

---

## How to use this file

1. Open `PROGRESS.md` to see current state.
2. Find the next "Not started" step.
3. Copy the prompt block for that step into Claude Code.
4. After Claude completes the step, update `PROGRESS.md` (mark the step Done, update Milestone Status and Blockers).
5. Run `pytest` to confirm nothing regressed.
6. Commit: `git commit -m "feat: step <N> — <title>"`.
7. Move to the next step.
