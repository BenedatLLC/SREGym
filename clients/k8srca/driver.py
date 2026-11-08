"""k8srca client for SREGym (k8srca design 014 §4.5).

A shim. The agent runs elsewhere: a host-side service holds the credentials and
runs k8srca's Managed Agents session, reading the cluster through k8stools and
SREGym's view proxy. This container has no kubeconfig and no API key. It waits
for the conductor, passes the application information to the service, and
submits the diagnosis the service returns.

    K8SRCA_SERVICE_URL   the service, as the container reaches it
                         (allow it with --allow-agent-endpoint)

Diagnosis only: a problem whose stage list includes mitigation is submitted for
diagnosis and then left, as k8srca does not mitigate.
"""

import logging
import os
import sys
import time

import requests

from clients.harness.problem_id import resolve_problem_id
from logger import init_logger

init_logger()

logger = logging.getLogger("all.k8srca.driver")

#: Seconds the service gets, before the conductor's own deadline is looked at.
DEFAULT_SERVICE_TIMEOUT = 45 * 60


def api_base_url() -> str:
    host = os.getenv("API_HOSTNAME", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{host}:{port}"


def wait_for_stage(timeout: int = 300) -> dict:
    """Block until the conductor accepts a diagnosis; return its /status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{api_base_url()}/status", timeout=10)
            r.raise_for_status()
            status = r.json()
            if status.get("stage") == "diagnosis":
                return status
        except Exception as exc:  # noqa: BLE001 - retry until the deadline
            logger.debug("conductor not ready: %s", exc)
        time.sleep(1)
    raise TimeoutError(f"the conductor did not reach the diagnosis stage within {timeout}s")


def get_app() -> dict:
    r = requests.get(f"{api_base_url()}/get_app", timeout=30)
    r.raise_for_status()
    return r.json()


def ask_service(app_info: dict, problem_id: str, timeout: float) -> str:
    url = os.environ["K8SRCA_SERVICE_URL"].rstrip("/")
    r = requests.post(f"{url}/diagnose", json={"problem_id": problem_id, "app_info": app_info}, timeout=(15, timeout))
    if r.status_code != 200:
        raise RuntimeError(f"the k8srca service answered HTTP {r.status_code}: {r.text[:300]}")
    answer = r.json().get("answer", "")
    if not answer.strip():
        raise RuntimeError("the k8srca service returned no answer")
    return answer


def submit(answer: str) -> None:
    r = requests.post(f"{api_base_url()}/submit", json={"solution": answer, "stage": "diagnosis"}, timeout=600)
    r.raise_for_status()
    logger.info("submitted: %s", r.json().get("message", ""))


def main() -> int:
    problem_id = resolve_problem_id()
    logger.info("k8srca client for %s", problem_id)
    try:
        status = wait_for_stage()
        app_info = get_app()
        remaining = status.get("agent_remaining_seconds")
        timeout = (
            min(DEFAULT_SERVICE_TIMEOUT, max(60.0, float(remaining) - 60)) if remaining else DEFAULT_SERVICE_TIMEOUT
        )
        answer = ask_service(app_info, problem_id, timeout)
        submit(answer)
    except Exception as exc:  # noqa: BLE001 - the harness reads the exit code
        logger.error("k8srca client failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
