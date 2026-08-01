from datetime import date

from media_claw_agent.kubernetes_job import build_refresh_job, use_native_sidecar


def test_use_native_sidecar_moves_router_to_restartable_init_container() -> None:
    pod_spec = {
        "initContainers": [{"name": "egress-guard", "image": "guard"}],
        "containers": [
            {"name": "openclaw", "image": "agent"},
            {"name": "inference-router", "image": "router"},
        ],
    }

    use_native_sidecar(pod_spec)

    assert [container["name"] for container in pod_spec["containers"]] == [
        "openclaw"
    ]
    assert [container["name"] for container in pod_spec["initContainers"]] == [
        "egress-guard",
        "inference-router",
    ]
    assert pod_spec["initContainers"][-1]["restartPolicy"] == "Always"


def test_build_refresh_job_preserves_runtime_and_makes_workload_finite() -> None:
    deployment = {
        "spec": {
            "template": {
                "metadata": {
                    "creationTimestamp": "2026-07-27T00:00:00Z",
                    "labels": {
                        "app": "media-claw-agent",
                        "azure.workload.identity/use": "true",
                    },
                },
                "spec": {
                    "serviceAccountName": "sandbox",
                    "affinity": {"podAntiAffinity": {}},
                    "initContainers": [{"name": "egress-guard", "image": "guard"}],
                    "containers": [
                        {
                            "name": "openclaw",
                            "image": "agent",
                            "args": ["serve"],
                            "env": [{"name": "HTTPS_PROXY", "value": "old"}],
                            "livenessProbe": {"exec": {"command": ["true"]}},
                            "readinessProbe": {"exec": {"command": ["true"]}},
                        },
                        {"name": "inference-router", "image": "router"},
                    ],
                },
            }
        }
    }

    job = build_refresh_job(
        deployment, date(2026, 7, 27), "kars-media-claw-agent"
    )
    template = job["spec"]["template"]
    pod_spec = template["spec"]
    openclaw = pod_spec["containers"][0]

    assert job["kind"] == "Job"
    assert job["metadata"]["labels"]["run-date"] == "2026-07-27"
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["ttlSecondsAfterFinished"] == 3600
    assert pod_spec["restartPolicy"] == "Never"
    assert pod_spec["serviceAccountName"] == "sandbox"
    assert pod_spec["affinity"] == {"podAntiAffinity": {}}
    assert template["metadata"]["labels"]["azure.workload.identity/use"] == "true"
    assert [container["name"] for container in pod_spec["containers"]] == [
        "openclaw"
    ]
    assert [container["name"] for container in pod_spec["initContainers"]] == [
        "egress-guard",
        "inference-router",
    ]
    assert pod_spec["initContainers"][-1]["restartPolicy"] == "Always"
    assert openclaw["command"][:2] == ["python3", "-c"]
    assert '"2026-07-27"' in openclaw["command"][2]
    assert '"--refresh-audio"' in openclaw["command"][2]
    assert "args" not in openclaw
    assert "livenessProbe" not in openclaw
    assert "readinessProbe" not in openclaw
    assert {item["name"]: item["value"] for item in openclaw["env"]} == {
        "HTTP_PROXY": "http://127.0.0.1:8444",
        "HTTPS_PROXY": "http://127.0.0.1:8444",
        "NO_PROXY": "127.0.0.1,localhost,.svc,.cluster.local",
    }