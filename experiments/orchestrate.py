#!/usr/bin/env python3
"""Experiment orchestrator - the top of the stack built across this
session's phases:

  Chaos Mesh fault injection (deployment/fault-inject-deployment/chaos-mesh/inject.py)
  Locust load generator      (deployment/load-generator/, via hack/load-generator/deploy.sh)
  Telemetry export           (deployment/telemetry-export/run_writer.py)
  Cluster health checks      (a lightweight subset of verify/'s checks)

For each (service, fault_type) pair, repeated N times:
  1. confirm the cluster is healthy (abort the sweep otherwise - see
     --continue-on-failure)
  2. make sure Locust warm-up traffic is running (deploys it if it isn't)
  3. record inject_ts, then inject.py::inject(...)
  4. sleep for duration + settle
  5. run_writer.py::run(...) exports the window - starting `--baseline`
     seconds before injection (so the export includes pre-fault baseline
     data, not just the fault itself) through when the settle period ends
  6. inject.py::cleanup(...) - always runs, even if steps 4-5 raised
  7. wait for the cluster to report healthy again before the next run

Runs are strictly sequential, never overlapping: a fault's blast radius
could otherwise contaminate a different run's telemetry window or fight
over the same shared Locust account (see the concurrency caveat in
deployment/load-generator/locustfile.py).

Usage:
  python3 experiments/orchestrate.py \\
      --service ts-travel-service --service ts-price-service \\
      --fault-type network-delay --fault-type stress-cpu \\
      --repeats 3 --duration 60 --settle 30 --baseline 30 \\
      --output-dir data
"""
import argparse
import importlib.util
import json
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHAOS_DIR = REPO_ROOT / "deployment" / "fault-inject-deployment" / "chaos-mesh"
TELEMETRY_DIR = REPO_ROOT / "deployment" / "telemetry-export"
LOAD_GEN_DEPLOY_SCRIPT = REPO_ROOT / "hack" / "load-generator" / "deploy.sh"

OBSERVABILITY_NAMESPACE = "observability"
APP_NAMESPACE = "default"


def _load_module(name, path):
    """Loads a sibling-phase module by file path via importlib, not a
    normal package import: deployment/fault-inject-deployment/chaos-mesh/
    and deployment/telemetry-export/ have hyphens in their directory
    names, which aren't valid in Python package/module names, so
    `from deployment.fault_inject... import ...` was never an option."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# run_writer.py does `from logs_export import export_logs` etc, assuming
# its own directory is on sys.path (true when it's run directly as a
# script, since Python adds the script's directory to sys.path[0] - not
# true when it's dynamically loaded from here, so it has to be added
# explicitly first).
sys.path.insert(0, str(TELEMETRY_DIR))

inject_mod = _load_module("chaos_inject", CHAOS_DIR / "inject.py")
run_writer_mod = _load_module("telemetry_run_writer", TELEMETRY_DIR / "run_writer.py")


class OrchestrationError(RuntimeError):
    pass


class PortForwards:
    """Manages the kubectl port-forwards run_writer.py's exporters need
    (Prometheus/Jaeger/Loki), opened once for the whole sweep rather than
    per-run - a sweep can be many runs, and restarting three kubectl
    processes per run adds real overhead for no benefit."""

    def __init__(self):
        self.procs = []

    def start(self, target, local_port, remote_port, namespace=OBSERVABILITY_NAMESPACE):
        proc = subprocess.Popen(
            ["kubectl", "port-forward", "-n", namespace, target, f"{local_port}:{remote_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        connected = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.5):
                    connected = True
                    break
            except OSError:
                continue
        if not connected:
            raise OrchestrationError(f"port-forward to {target} on :{local_port} did not come up in time")
        # Same check as hack/telemetry-export/export.sh: the connection
        # succeeding only proves *something* is listening on that port -
        # if it's a stray process from an earlier run, not the kubectl we
        # just launched, our PID is already dead and stop_all() won't
        # actually be able to tear down whatever's really serving it.
        if proc.poll() is not None:
            raise OrchestrationError(
                f"port {local_port} was already in use by another process, not the port-forward this script just started"
            )
        self.procs.append(proc)

    def stop_all(self):
        for proc in self.procs:
            proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def cluster_healthy():
    """Lightweight health gate: every pod cluster-wide is Running or
    Succeeded. Deliberately NOT the full verify/ suite - that suite's
    Chaos Mesh check injects its own small test fault, which would
    itself be a confound sitting in the middle of this sweep's timeline.
    Returns (bool, detail_string)."""
    result = subprocess.run(
        [
            "kubectl", "get", "pods", "-A",
            "--field-selector=status.phase!=Running,status.phase!=Succeeded",
            "-o", "json",
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return False, f"kubectl get pods failed: {result.stderr.strip()}"

    items = json.loads(result.stdout).get("items", [])
    if not items:
        return True, ""

    names = [f"{p['metadata']['namespace']}/{p['metadata']['name']} ({p['status']['phase']})" for p in items]
    return False, f"{len(items)} pod(s) not Running/Succeeded: {', '.join(names)}"


def wait_for_recovery(timeout_seconds, poll_interval=5):
    deadline = time.time() + timeout_seconds
    while True:
        healthy, detail = cluster_healthy()
        if healthy:
            return True, ""
        if time.time() >= deadline:
            return False, detail
        time.sleep(poll_interval)


def ensure_load_generator_running():
    result = subprocess.run(
        ["kubectl", "get", "deployment", "load-generator", "-n", APP_NAMESPACE, "-o", "json"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        status = json.loads(result.stdout).get("status", {})
        if status.get("readyReplicas", 0) >= 1:
            return
    print("==> load-generator not running yet, deploying it...")
    subprocess.run([str(LOAD_GEN_DEPLOY_SCRIPT)], check=True, cwd=REPO_ROOT)


def run_single_experiment(service, fault_type, repeat, args):
    print(f"\n{'=' * 70}")
    print(f"service={service}  fault_type={fault_type}  repeat={repeat}")
    print(f"{'=' * 70}")

    healthy, detail = cluster_healthy()
    if not healthy:
        message = f"cluster unhealthy before this run: {detail}"
        if not args.continue_on_failure:
            raise OrchestrationError(message)
        print(f"warning: {message} (continuing anyway, --continue-on-failure set)", file=sys.stderr)

    ensure_load_generator_running()

    window_start = int(time.time()) - args.baseline
    inject_ts = None
    experiment_name = None
    inject_error = None
    try:
        inject_ts = int(time.time())
        experiment_name = inject_mod.inject(
            service, fault_type, duration=f"{args.duration}s", params=args.params.get(fault_type, {})
        )
        print(f"==> injected {experiment_name}; waiting {args.duration}s (fault) + {args.settle}s (settle)...")
        time.sleep(args.duration + args.settle)
    except Exception as e:  # noqa: BLE001 - deliberately broad: must still hit the finally below
        inject_error = str(e)
        print(f"error during injection/wait: {inject_error}", file=sys.stderr)
    finally:
        if experiment_name:
            print(f"==> cleaning up {experiment_name}")
            try:
                inject_mod.cleanup(experiment_name)
            except Exception as e:  # noqa: BLE001
                # A fault stuck active would contaminate every subsequent
                # run - this has to stop the sweep, not just log a warning.
                raise OrchestrationError(f"cleanup({experiment_name}) failed, aborting sweep: {e}") from e

    if inject_error and not args.continue_on_failure:
        raise OrchestrationError(f"aborting sweep after injection failure: {inject_error}")

    window_end = int(time.time())

    manifest = None
    if experiment_name and not inject_error:
        print(f"==> exporting telemetry for window [{window_start}, {window_end}]")
        manifest = run_writer_mod.run(
            service, fault_type, window_start, window_end, args.output_dir,
            prometheus_url="http://localhost:9090",
            jaeger_url="http://localhost:16686",
            loki_url="http://localhost:3100",
            inject_time=inject_ts,
        )

    print(f"==> waiting for cluster to recover (timeout {args.recovery_timeout}s)...")
    recovered, recovery_detail = wait_for_recovery(args.recovery_timeout)
    if not recovered:
        message = f"cluster did not recover within {args.recovery_timeout}s after cleanup: {recovery_detail}"
        if not args.continue_on_failure:
            raise OrchestrationError(message)
        print(f"warning: {message} (continuing anyway, --continue-on-failure set)", file=sys.stderr)
    else:
        print("==> cluster recovered.")

    return {
        "service": service,
        "fault_type": fault_type,
        "repeat": repeat,
        "experiment_name": experiment_name,
        "inject_time": inject_ts,
        "window": [window_start, window_end],
        "inject_error": inject_error,
        "recovered": recovered,
        "manifest": manifest,
    }


def _main():
    parser = argparse.ArgumentParser(description="Orchestrate a sweep of Chaos Mesh fault-injection experiments")
    parser.add_argument("--service", action="append", required=True, dest="services",
                         help="target service (app=<service> label); repeatable")
    parser.add_argument("--fault-type", action="append", required=True, dest="fault_types",
                         choices=sorted(inject_mod.FAULT_TYPES), help="repeatable")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--duration", type=int, default=60, help="fault duration, seconds")
    parser.add_argument("--settle", type=int, default=30,
                         help="extra seconds to keep exporting after the fault ends, before cleanup")
    parser.add_argument("--baseline", type=int, default=30,
                         help="seconds of pre-injection data to include at the start of the export window")
    parser.add_argument("--recovery-timeout", type=int, default=120,
                         help="max seconds to wait for the cluster to report healthy again after cleanup")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--params-json", default=None,
                         help='JSON object: {"fault_type": {"param": "value", ...}, ...} - see inject.py list-faults for valid params per fault type')
    parser.add_argument("--continue-on-failure", action="store_true",
                         help="log and continue past a failed health check/injection instead of aborting the whole sweep")
    args = parser.parse_args()
    args.params = json.loads(args.params_json) if args.params_json else {}

    total = len(args.services) * len(args.fault_types) * args.repeats
    print(f"Sweep: {len(args.services)} service(s) x {len(args.fault_types)} fault type(s) x {args.repeats} repeat(s) = {total} runs")

    pf = PortForwards()
    print("==> opening port-forwards to Prometheus/Jaeger/Loki...")
    pf.start("svc/kube-prometheus-stack-prometheus", 9090, 9090)
    pf.start("svc/jaeger", 16686, 16686)
    pf.start("svc/loki-gateway", 3100, 80)

    sweep_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = []
    aborted_with = None
    n = 0
    try:
        for service in args.services:
            for fault_type in args.fault_types:
                for repeat in range(1, args.repeats + 1):
                    n += 1
                    print(f"\n[{n}/{total}]", end=" ")
                    result = run_single_experiment(service, fault_type, repeat, args)
                    results.append(result)
    except OrchestrationError as e:
        aborted_with = str(e)
        print(f"\nSWEEP ABORTED: {aborted_with}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - must still reach the finally below to clean up port-forwards
        aborted_with = f"{type(e).__name__}: {e}"
        print(f"\nSWEEP ABORTED (unexpected error): {aborted_with}", file=sys.stderr)
        traceback.print_exc()
    finally:
        pf.stop_all()
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / f"sweep_{sweep_id}_summary.json"
        with open(summary_path, "w") as f:
            json.dump({
                "sweep_id": sweep_id,
                "args": {k: v for k, v in vars(args).items()},
                "planned_runs": total,
                "completed_runs": len(results),
                "aborted_with": aborted_with,
                "results": results,
            }, f, indent=2, default=str)
        print(f"\n==> sweep summary: {summary_path} ({len(results)}/{total} runs completed)")

    if aborted_with:
        sys.exit(1)


if __name__ == "__main__":
    _main()
