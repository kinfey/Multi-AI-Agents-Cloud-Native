from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
from datetime import date
from typing import Any


def use_native_sidecar(
    pod_spec: dict[str, Any], router_name: str = "inference-router"
) -> None:
    containers = pod_spec.get("containers", [])
    routers = [container for container in containers if container["name"] == router_name]
    if len(routers) != 1:
        raise ValueError(f"expected exactly one {router_name} container")

    router = routers[0]
    router["restartPolicy"] = "Always"
    pod_spec["containers"] = [
        container for container in containers if container["name"] != router_name
    ]
    pod_spec.setdefault("initContainers", []).append(router)


def build_refresh_job(
    deployment: dict[str, Any],
    run_date: date,
    namespace: str,
) -> dict[str, Any]:
    name = f"media-refresh-{run_date:%y%m%d}-{int(time.time())}"
    template = copy.deepcopy(deployment["spec"]["template"])
    template["metadata"].pop("creationTimestamp", None)
    template["metadata"].setdefault("labels", {})["job-name"] = name

    pod_spec = template["spec"]
    pod_spec["restartPolicy"] = "Never"
    use_native_sidecar(pod_spec)

    openclaw = next(
        container
        for container in pod_spec["containers"]
        if container["name"] == "openclaw"
    )
    openclaw["command"] = ["python3", "-c", _refresh_command(run_date)]
    openclaw.pop("args", None)
    for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
        openclaw.pop(probe, None)
    _set_proxy_environment(openclaw)

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app": "media-refresh",
                "run-date": run_date.isoformat(),
            },
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,
            "template": template,
        },
    }


def _set_proxy_environment(container: dict[str, Any]) -> None:
    values = {
        "HTTP_PROXY": "http://127.0.0.1:8444",
        "HTTPS_PROXY": "http://127.0.0.1:8444",
        "NO_PROXY": "127.0.0.1,localhost,.svc,.cluster.local",
    }
    environment = container.setdefault("env", [])
    environment[:] = [item for item in environment if item["name"] not in values]
    environment.extend({"name": name, "value": value} for name, value in values.items())


def _refresh_command(run_date: date) -> str:
    return f"""import runpy
import socket
import sys
import time

for attempt in range(120):
    try:
        with socket.create_connection((\"127.0.0.1\", 8444), timeout=1):
            break
    except OSError:
        if attempt == 119:
            raise RuntimeError(\"proxy not ready after 120 seconds\")
        time.sleep(1)

print(\"PROXY_READY host=127.0.0.1 port=8444\", flush=True)
sys.argv = [
    \"media_claw_agent.main\",
    \"--date\",
    \"{run_date.isoformat()}\",
    \"--refresh-audio\",
]
runpy.run_module(\"media_claw_agent.main\", run_name=\"__main__\")
"""


def _kubectl(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", *args],
        input=stdin,
        capture_output=True,
        check=False,
        text=True,
    )


def _load_json(*args: str) -> dict[str, Any]:
    result = _kubectl(*args)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh existing audio and video using a finite native sidecar Job."
    )
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--namespace", default="kars-media-claw-agent")
    parser.add_argument("--deployment", default="media-claw-agent")
    args = parser.parse_args()

    jobs = _load_json("get", "jobs", "-n", args.namespace, "-o", "json")
    legacy_name = f"media-refresh-{args.date:%y%m%d}"
    for job in jobs["items"]:
        labels = job["metadata"].get("labels", {})
        same_date = labels.get("run-date") == args.date.isoformat() or job[
            "metadata"
        ]["name"].startswith(legacy_name)
        if same_date and job.get("status", {}).get("active", 0):
            parser.error(f"active refresh Job already exists: {job['metadata']['name']}")

    deployment = _load_json(
        "get", "deployment", args.deployment, "-n", args.namespace, "-o", "json"
    )
    job = build_refresh_job(deployment, args.date, args.namespace)
    result = _kubectl("create", "-f", "-", stdin=json.dumps(job))
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)
    print(result.stdout.strip())
    print(f"logs: kubectl logs -f job/{job['metadata']['name']} -n {args.namespace} -c openclaw")


if __name__ == "__main__":
    main()