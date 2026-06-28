from deployguard.guard.models import Severity, Violation

# Kinds where pod templates live directly under spec.template.spec
_WORKLOAD_KINDS = {"Deployment", "DaemonSet", "StatefulSet", "ReplicaSet", "Job"}


def _get_pod_spec(manifest: dict) -> dict | None:
    kind = manifest.get("kind", "")
    if kind in _WORKLOAD_KINDS:
        return manifest.get("spec", {}).get("template", {}).get("spec", {})
    if kind == "CronJob":
        return (
            manifest.get("spec", {})
            .get("jobTemplate", {})
            .get("spec", {})
            .get("template", {})
            .get("spec", {})
        )
    if kind == "Pod":
        return manifest.get("spec", {})
    return None


def _get_containers(manifest: dict) -> list[dict]:
    pod_spec = _get_pod_spec(manifest)
    if pod_spec is None:
        return []
    containers = pod_spec.get("containers", [])
    init_containers = pod_spec.get("initContainers", [])
    return containers + init_containers


# ── Rule 1 ───────────────────────────────────────────────────────────────────

def rule_require_resource_limits(manifest: dict) -> list[Violation]:
    violations: list[Violation] = []
    for container in _get_containers(manifest):
        resources = container.get("resources", {})
        missing = []
        if not resources.get("requests"):
            missing.append("requests")
        if not resources.get("limits"):
            missing.append("limits")
        if missing:
            violations.append(
                Violation(
                    rule_id="require_resource_limits",
                    severity=Severity.ERROR,
                    message=(
                        f"Container '{container.get('name', '?')}' is missing "
                        f"resources.{' and resources.'.join(missing)}."
                    ),
                    why=(
                        "Without resource limits one pod can starve the node and bring "
                        "down its neighbours. Kubernetes will not schedule your pod if "
                        "the node is overcommitted."
                    ),
                    fix="Add resources.requests and resources.limits to every container spec.",
                )
            )
    return violations


# ── Rule 2 ───────────────────────────────────────────────────────────────────

def rule_require_probes(manifest: dict) -> list[Violation]:
    violations: list[Violation] = []
    for container in _get_containers(manifest):
        missing = []
        if not container.get("livenessProbe"):
            missing.append("livenessProbe")
        if not container.get("readinessProbe"):
            missing.append("readinessProbe")
        if missing:
            violations.append(
                Violation(
                    rule_id="require_probes",
                    severity=Severity.ERROR,
                    message=(
                        f"Container '{container.get('name', '?')}' is missing "
                        f"{' and '.join(missing)}."
                    ),
                    why=(
                        "Without probes Kubernetes sends traffic to pods that aren't "
                        "ready and never restarts ones that are stuck. Your deployment "
                        "will appear healthy while serving errors."
                    ),
                    fix=(
                        "Add livenessProbe (restart on hang) and readinessProbe "
                        "(gate traffic) to every container."
                    ),
                )
            )
    return violations


# ── Rule 3 ───────────────────────────────────────────────────────────────────

def rule_require_security_context(manifest: dict) -> list[Violation]:
    pod_spec = _get_pod_spec(manifest)
    if pod_spec is None:
        return []

    ctx = pod_spec.get("securityContext", {})
    if not ctx or not ctx.get("runAsNonRoot"):
        return [
            Violation(
                rule_id="require_security_context",
                severity=Severity.ERROR,
                message=(
                    "Pod spec is missing securityContext.runAsNonRoot: true."
                ),
                why=(
                    "A pod without a securityContext runs as root by default. "
                    "A container escape then gives the attacker host root."
                ),
                fix=(
                    "Add securityContext.runAsNonRoot: true to the pod spec and set "
                    "allowPrivilegeEscalation: false on each container."
                ),
            )
        ]
    return []


# ── Rule 4 ───────────────────────────────────────────────────────────────────

def rule_no_latest_tag(manifest: dict) -> list[Violation]:
    violations: list[Violation] = []
    for container in _get_containers(manifest):
        image: str = container.get("image", "")
        # No tag at all, or explicit :latest
        tag = image.split(":")[-1] if ":" in image else ""
        if not tag or tag == "latest":
            violations.append(
                Violation(
                    rule_id="no_latest_tag",
                    severity=Severity.ERROR,
                    message=(
                        f"Container '{container.get('name', '?')}' uses image "
                        f"'{image or '(empty)'}' — no pinned tag."
                    ),
                    why=(
                        ":latest is mutable — the image you tested is not guaranteed "
                        "to be the image that deploys. Pinned tags make rollbacks and "
                        "audits reliable."
                    ),
                    fix="Pin every image to a specific digest or semantic version tag.",
                )
            )
    return violations


# ── Rule 5 ───────────────────────────────────────────────────────────────────

def rule_no_root_user(manifest: dict) -> list[Violation]:
    violations: list[Violation] = []
    for container in _get_containers(manifest):
        ctx = container.get("securityContext", {})
        if ctx.get("runAsUser") == 0:
            violations.append(
                Violation(
                    rule_id="no_root_user",
                    severity=Severity.ERROR,
                    message=(
                        f"Container '{container.get('name', '?')}' sets "
                        "securityContext.runAsUser: 0 (root)."
                    ),
                    why=(
                        "Running as UID 0 inside a container means a breakout gives "
                        "the attacker root on the host. This is the most common "
                        "container misconfiguration."
                    ),
                    fix=(
                        "Set securityContext.runAsUser to a non-zero UID "
                        "(e.g. 1000) on each container."
                    ),
                )
            )
    return violations


# ── Rule 6 ───────────────────────────────────────────────────────────────────

def rule_no_privileged_containers(manifest: dict) -> list[Violation]:
    violations: list[Violation] = []
    for container in _get_containers(manifest):
        ctx = container.get("securityContext", {})
        if ctx.get("privileged") is True:
            violations.append(
                Violation(
                    rule_id="no_privileged_containers",
                    severity=Severity.ERROR,
                    message=(
                        f"Container '{container.get('name', '?')}' sets "
                        "securityContext.privileged: true."
                    ),
                    why=(
                        "A privileged container has nearly full access to the host "
                        "kernel. There is almost no legitimate use for this in an "
                        "application workload."
                    ),
                    fix=(
                        "Remove securityContext.privileged: true. If you need specific "
                        "capabilities, add only those via securityContext.capabilities.add."
                    ),
                )
            )
    return violations


# ── Rule 7 ───────────────────────────────────────────────────────────────────

def rule_iam_least_privilege(manifest: dict) -> list[Violation]:
    if manifest.get("kind") != "ServiceAccount":
        return []

    # Flag when automountServiceAccountToken is absent (defaults to True in k8s)
    # or explicitly set to True.
    auto_mount = manifest.get("automountServiceAccountToken")
    if auto_mount is not False:
        name = manifest.get("metadata", {}).get("name", "?")
        return [
            Violation(
                rule_id="iam_least_privilege",
                severity=Severity.WARN,
                message=(
                    f"ServiceAccount '{name}' does not explicitly disable "
                    "automountServiceAccountToken."
                ),
                why=(
                    "Auto-mounted service account tokens give every pod in the namespace "
                    "API server access by default. If a pod is compromised the token is "
                    "the attacker's first lateral-movement tool."
                ),
                fix=(
                    "Set automountServiceAccountToken: false on the ServiceAccount "
                    "(or pod spec) and only mount tokens in pods that call the "
                    "Kubernetes API."
                ),
            )
        ]
    return []


# ── Rule registry ─────────────────────────────────────────────────────────────

ALL_RULES: list[tuple[str, callable]] = [
    ("require_resource_limits", rule_require_resource_limits),
    ("require_probes", rule_require_probes),
    ("require_security_context", rule_require_security_context),
    ("no_latest_tag", rule_no_latest_tag),
    ("no_root_user", rule_no_root_user),
    ("no_privileged_containers", rule_no_privileged_containers),
    ("iam_least_privilege", rule_iam_least_privilege),
]
