"""Wrap the infracost CLI for cost estimation."""
from __future__ import annotations

import json
import shutil
import subprocess


def run_infracost(tf_dir: str) -> dict:
    """Run infracost scan on tf_dir and return parsed JSON.

    Returns empty dict when no Terraform files are found.
    Raises RuntimeError when infracost binary is missing or org not configured.
    """
    if not shutil.which("infracost"):
        raise RuntimeError(
            "infracost not found. Install it and re-run `dg doctor` to verify."
        )

    result = subprocess.run(
        ["infracost", "scan", tf_dir, "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    stderr = result.stderr.strip()

    if result.returncode != 0:
        if "no terraform" in stderr.lower():
            return {}
        if "no associated organization" in stderr.lower():
            raise RuntimeError(
                "infracost requires an organization. "
                "Create one at https://dashboard.infracost.io then re-run."
            )
        raise RuntimeError(f"infracost failed:\n{stderr}")

    # infracost scan may still warn about org on stdout even with exit 0
    stdout = result.stdout.strip()
    if not stdout:
        return {}

    return json.loads(stdout)
