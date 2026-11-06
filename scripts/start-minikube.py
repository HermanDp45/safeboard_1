import json
import subprocess


def main():
    subprocess.run(
        [
            "minikube",
            "start",
            "-p",
            "safeboard",
            "--driver=docker",
            "--network=bridge",
            "--cpus=3",
            "--memory=5632",
            "--kubernetes-version=v1.37.0",
        ],
        check=True,
    )
    query = ["docker", "exec", "safeboard", "dig", "+time=2", "+tries=1", "+short"]
    udp = subprocess.run(
        [*query, "huggingface.co"], capture_output=True, text=True, check=False
    )
    kubectl = ["kubectl", "--context", "safeboard"]
    if udp.returncode != 0:
        tcp = subprocess.run(
            [*query, "+tcp", "huggingface.co"],
            capture_output=True,
            text=True,
            check=False,
        )
        if tcp.returncode != 0:
            raise SystemExit("Minikube DNS is unavailable over both UDP and TCP")
        # Some Docker Desktop versions drop UDP DNS in the nested node.
        subprocess.run(
            [
                "docker",
                "exec",
                "safeboard",
                "sh",
                "-c",
                (
                    "grep -q '^options use-vc$' /etc/resolv.conf || "
                    "printf '\\noptions use-vc\\n' >> /etc/resolv.conf"
                ),
            ],
            check=True,
        )
        command = [*kubectl, "-n", "kube-system"]
        config = json.loads(
            subprocess.check_output(
                [
                    *command,
                    "get",
                    "configmap",
                    "coredns",
                    "-o",
                    "json",
                ]
            )
        )
        corefile = config["data"]["Corefile"]
        if "force_tcp" not in corefile:
            corefile = corefile.replace(
                "forward . /etc/resolv.conf {",
                "forward . /etc/resolv.conf {\n       force_tcp",
            )
            patch = json.dumps({"data": {"Corefile": corefile}})
            subprocess.run(
                [
                    *command,
                    "patch",
                    "configmap",
                    "coredns",
                    "--type=merge",
                    "--patch",
                    patch,
                ],
                check=True,
            )
            subprocess.run(
                [*command, "rollout", "restart", "deployment/coredns"], check=True
            )
        subprocess.run(
            [*command, "rollout", "restart", "daemonset/kindnet"], check=True
        )
    subprocess.run(
        [*kubectl, "wait", "node/safeboard", "--for=condition=Ready", "--timeout=180s"],
        check=True,
    )


if __name__ == "__main__":
    main()
