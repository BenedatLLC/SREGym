"""A persistent SREGym view proxy for k8srca's k8stools.

The conductor starts its own filtering proxy for each problem and writes a new
agent kubeconfig, with a new CA, every time. k8stools holds one kubeconfig for
its whole life, so it reads the cluster through this long-lived instance of the
same class instead (sregym/service/k8s_proxy.py: the same hidden namespaces and
labels, Chaos Mesh rules and filtered-mode write restrictions), as
`clients/claudecode_radar/radar_view_proxy.py` does for Radar.

It binds the docker bridge's gateway address, reachable from a container, and
not loopback. Run from the repository root with KUBECONFIG pointing at the
benchmark cluster:

    uv run python -m clients.k8srca.view_proxy --kubeconfig-out /path/view.kubeconfig
"""

import argparse
import logging
import time

from sregym.service.container_runner import get_container_host_bind_address
from sregym.service.k8s_proxy import KubernetesAPIProxy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=16450, help="Port for the proxy (default: 16450).")
    parser.add_argument("--listen-host", default=None, help="Bind address (default: the docker bridge gateway).")
    parser.add_argument("--kubeconfig-out", required=True, help="Where to write the kubeconfig for k8stools.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    host = args.listen_host or get_container_host_bind_address()
    proxy = KubernetesAPIProxy(listen_port=args.port, listen_host=host, restrict_network_access=True)
    proxy.start()
    proxy.generate_agent_kubeconfig(args.kubeconfig_out)
    print(f"k8srca view proxy on {host}:{args.port}, kubeconfig {args.kubeconfig_out}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        proxy.stop()


if __name__ == "__main__":
    main()
