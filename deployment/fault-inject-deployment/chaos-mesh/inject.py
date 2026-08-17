#!/usr/bin/env python3
"""Chaos Mesh fault injection helper.

Sibling to ../gray-release-manage.py: same approach of building a YAML
document with PyYAML and shelling out to kubectl to apply/delete it,
targeting pods by their `app: <service-name>` label (the convention used
by every deployment manifest in this repo). Unlike that script, this one
is a reusable module (`inject`/`cleanup`) rather than a single hardcoded
loop, since it's meant to be imported as the single call-site the fault
orchestrator uses - it's also runnable directly as a CLI for manual use.

Usage as a library:
    from inject import inject, cleanup
    name = inject("ts-travel-service", "network-delay", duration="30s",
                   params={"latency": "200ms"})
    ...
    cleanup(name)

Usage as a CLI:
    ./inject.py inject ts-travel-service network-delay \\
        --duration 30s --param latency=200ms
    ./inject.py cleanup network-delay-ts-travel-service-a1b2c3d4
    ./inject.py list-faults
"""
import argparse
import subprocess
import sys
import uuid
from pathlib import Path

import yaml

TEMPLATE_DIR = Path(__file__).resolve().parent
NAMESPACE = "default"  # namespace the target train-ticket app pods run in

# fault_type -> (CR kind, template filename)
FAULT_TYPES = {
    "stress-cpu": ("StressChaos", "stress-cpu.yaml"),
    "stress-mem": ("StressChaos", "stress-mem.yaml"),
    "stress-socket": ("StressChaos", "stress-socket.yaml"),
    "io-disk": ("IOChaos", "io-disk.yaml"),
    "network-delay": ("NetworkChaos", "network-delay.yaml"),
    "network-loss": ("NetworkChaos", "network-loss.yaml"),
}

# fault_type -> { param_name: (dotted path into the CR doc, type caster) }
FAULT_PARAMS = {
    "stress-cpu": {
        "workers": (("spec", "stressors", "cpu", "workers"), int),
        "load": (("spec", "stressors", "cpu", "load"), int),
    },
    "stress-mem": {
        "workers": (("spec", "stressors", "memory", "workers"), int),
        "size": (("spec", "stressors", "memory", "size"), str),
    },
    "stress-socket": {
        "stressng_stressors": (("spec", "stressngStressors"), str),
    },
    "io-disk": {
        "volume_path": (("spec", "volumePath"), str),
        "path": (("spec", "path"), str),
        "delay": (("spec", "delay"), str),
        "percent": (("spec", "percent"), int),
    },
    "network-delay": {
        "latency": (("spec", "delay", "latency"), str),
        "jitter": (("spec", "delay", "jitter"), str),
        "correlation": (("spec", "delay", "correlation"), str),
    },
    "network-loss": {
        "loss": (("spec", "loss", "loss"), str),
        "correlation": (("spec", "loss", "correlation"), str),
    },
}


class InjectError(RuntimeError):
    pass


def _load_template(fault_type):
    if fault_type not in FAULT_TYPES:
        raise InjectError(
            f"unknown fault_type '{fault_type}', must be one of: {', '.join(sorted(FAULT_TYPES))}"
        )
    _, filename = FAULT_TYPES[fault_type]
    with open(TEMPLATE_DIR / filename) as f:
        return yaml.safe_load(f)


def _set_path(doc, path, value):
    node = doc
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _apply_params(doc, fault_type, params):
    valid_params = FAULT_PARAMS[fault_type]
    for key, raw_value in (params or {}).items():
        if key not in valid_params:
            raise InjectError(
                f"unknown param '{key}' for fault_type '{fault_type}', "
                f"must be one of: {', '.join(sorted(valid_params))}"
            )
        path, caster = valid_params[key]
        _set_path(doc, path, caster(raw_value))


def inject(service: str, fault_type: str, duration: str = "60s", params: dict = None) -> str:
    """Creates a Chaos Mesh experiment targeting pods labeled
    `app: <service>`. Returns the experiment name, which is the sole
    handle cleanup() needs to tear it down later."""
    doc = _load_template(fault_type)

    experiment_name = f"{fault_type}-{service}-{uuid.uuid4().hex[:8]}"
    doc["metadata"]["name"] = experiment_name
    doc["metadata"]["namespace"] = NAMESPACE
    doc["spec"]["selector"]["labelSelectors"]["app"] = service
    doc["spec"]["duration"] = duration

    _apply_params(doc, fault_type, params)

    yaml_text = yaml.safe_dump(doc)
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=yaml_text,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise InjectError(f"kubectl apply failed: {result.stderr.strip()}")

    return experiment_name


def _fault_type_from_name(experiment_name: str) -> str:
    # experiment names are always "<fault_type>-<service>-<8 hex chars>";
    # fault_type itself may contain hyphens, so match against the known
    # set rather than splitting on "-".
    for fault_type in FAULT_TYPES:
        if experiment_name.startswith(f"{fault_type}-"):
            return fault_type
    raise InjectError(
        f"cannot determine fault type from experiment name '{experiment_name}' "
        f"(expected it to start with one of: {', '.join(sorted(FAULT_TYPES))})"
    )


def cleanup(experiment_name: str) -> None:
    """Deletes a previously-injected experiment by the name inject() returned."""
    fault_type = _fault_type_from_name(experiment_name)
    kind, _ = FAULT_TYPES[fault_type]

    result = subprocess.run(
        ["kubectl", "delete", kind, experiment_name, "-n", NAMESPACE, "--ignore-not-found"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise InjectError(f"kubectl delete failed: {result.stderr.strip()}")


def _parse_param(raw):
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--param must be key=value, got '{raw}'")
    key, value = raw.split("=", 1)
    return key, value


def _main():
    parser = argparse.ArgumentParser(description="Chaos Mesh fault injection helper")
    sub = parser.add_subparsers(dest="command", required=True)

    inject_p = sub.add_parser("inject", help="create a fault injection experiment")
    inject_p.add_argument("service", help="target service, matched via app=<service> label")
    inject_p.add_argument("fault_type", choices=sorted(FAULT_TYPES))
    inject_p.add_argument("--duration", default="60s")
    inject_p.add_argument(
        "--param", action="append", default=[], type=_parse_param,
        help="fault-specific override, key=value, repeatable",
    )

    cleanup_p = sub.add_parser("cleanup", help="delete a previously created experiment")
    cleanup_p.add_argument("experiment_name")

    sub.add_parser("list-faults", help="list available fault types and their params")

    args = parser.parse_args()

    try:
        if args.command == "inject":
            name = inject(args.service, args.fault_type, args.duration, dict(args.param))
            print(name)
        elif args.command == "cleanup":
            cleanup(args.experiment_name)
        elif args.command == "list-faults":
            for fault_type, (kind, _) in sorted(FAULT_TYPES.items()):
                params = ", ".join(sorted(FAULT_PARAMS[fault_type])) or "(none)"
                print(f"{fault_type:16s} kind={kind:14s} params: {params}")
    except InjectError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
