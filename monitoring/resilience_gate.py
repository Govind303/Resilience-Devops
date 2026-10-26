"""ResilienceOps chaos gate.

For each experiment it:
  1. checks the service is healthy (baseline),
  2. starts the fault-injection command (an Ansible chaos playbook in staging),
  3. probes the service every few hundred milliseconds while the fault is active,
  4. checks the service ends healthy, and
  5. compares what it saw with the experiment's thresholds.

It writes a JSON report and a JUnit XML file per experiment (Jenkins shows the XML as test
results) and exits non-zero if any experiment fails, which blocks promotion to production.
Standard library only, so it runs on any agent with Python 3.8+.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import escape, quoteattr

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def probe(url, timeout):
    """One HTTP GET. Returns (status code or None, parsed JSON body or {}, latency in ms)."""
    start = time.monotonic()
    code, raw = None, ""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            code, raw = response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        code, raw = err.code, err.read().decode("utf-8", "replace")
    except Exception as err:  # connection refused, timeout, reset: all count as "down"
        raw = str(err)
    latency_ms = (time.monotonic() - start) * 1000
    try:
        body = json.loads(raw) if raw.startswith("{") else {}
    except ValueError:
        body = {}
    return code, body, latency_ms


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[index]


def outages(samples):
    """Contiguous runs of failed probes as (start_s, end_s or None if never recovered)."""
    runs, start = [], None
    for s in samples:
        if not s["ok"] and start is None:
            start = s["t"]
        elif s["ok"] and start is not None:
            runs.append((start, s["t"]))
            start = None
    if start is not None:
        runs.append((start, None))
    return runs


def wait_until_healthy(base_url, timeout_s, probe_timeout):
    """Polls /health until it reports UP. Returns the last health body, or None if it never did."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        code, body, _ = probe(base_url + "/health", probe_timeout)
        if code == 200 and body.get("status") == "UP":
            return body
        time.sleep(1)
    return None


def run_experiment(name, spec, settings, args):
    interval = settings["probe_interval_s"]
    timeout = settings["probe_timeout_s"]
    url = args.base_url + spec["probe_path"]
    thresholds = spec.get("thresholds", {})
    print(f"\n=== {name}: {spec['description']}")

    report = {"experiment": name, "description": spec["description"], "probe_url": url,
              "thresholds": thresholds, "checks": []}

    def check(check_name, passed, detail):
        report["checks"].append({"name": check_name, "passed": bool(passed), "detail": detail})
        print(f"  [{'PASS' if passed else 'FAIL'}] {check_name}: {detail}")

    # 1. Baseline: never inject chaos into a service that is already broken.
    baseline = wait_until_healthy(args.base_url, settings["baseline_timeout_s"], timeout)
    if baseline is None:
        check("baseline_healthy", False, "service was not UP before the experiment started")
        return report
    check("baseline_healthy", True, f"version {baseline.get('version')}, uptime {baseline.get('uptimeSeconds')}s")

    # 2. Start the fault in the background and watch the service while it runs.
    command = spec["inject"].format(inventory=args.inventory, base_url=args.base_url,
                                    python=f'"{sys.executable}"')
    log_path = os.path.join(args.report_dir, f"{name}-inject.log")
    print(f"  injecting: {command}")
    with open(log_path, "w", encoding="utf-8") as log:
        started = time.monotonic()
        process = subprocess.Popen(command, shell=True, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT)

        samples = []
        while time.monotonic() - started < spec["observe_s"]:
            tick = time.monotonic()
            code, body, latency = probe(url, timeout)
            samples.append({
                "t": round(tick - started, 2),
                "ok": code == 200,
                "code": code,
                "latency_ms": round(latency, 1),
                "degraded": body.get("degraded") is True or body.get("status") == "DEGRADED",
            })
            time.sleep(max(0.0, interval - (time.monotonic() - tick)))

        try:
            inject_rc = process.wait(timeout=settings["inject_timeout_s"])
        except subprocess.TimeoutExpired:
            process.kill()
            inject_rc = "timeout"

    # 3. After the fault is gone the service must come back to UP on its own.
    final = wait_until_healthy(args.base_url, settings["final_wait_s"], timeout)

    ok_latencies = [s["latency_ms"] for s in samples if s["ok"]]
    runs = outages(samples)
    unrecovered = any(end is None for _, end in runs)
    longest = max((end - start for start, end in runs if end is not None), default=0.0)
    metrics = {
        "probes": len(samples),
        "availability_pct": round(100 * sum(s["ok"] for s in samples) / max(1, len(samples)), 2),
        "outages": len(runs),
        "longest_outage_s": None if unrecovered else round(longest, 2),
        "recovered": not unrecovered,
        "p95_latency_ms": percentile(ok_latencies, 95),
        "degraded_responses": sum(s["degraded"] for s in samples),
        "inject_exit_code": inject_rc,
        "final_health": final,
    }
    report["metrics"] = metrics
    report["samples"] = samples

    # 4. Judge.
    check("fault_injected", inject_rc == 0, f"chaos command exit code {inject_rc} (log: {os.path.basename(log_path)})")

    expect = spec.get("expect")
    if expect == "restart":
        before, after = baseline.get("instanceId"), (final or {}).get("instanceId")
        restarted = before is not None and after is not None and before != after
        check("fault_took_effect", restarted,
              f"instance {before} was replaced by {after}" if restarted
              else f"instance before={before}, after={after}: the process was not replaced")
    elif expect == "degraded":
        check("graceful_degradation", metrics["degraded_responses"] > 0,
              f"{metrics['degraded_responses']} fallback responses served while the dependency was cut off")

    if "max_recovery_s" in thresholds:
        limit = thresholds["max_recovery_s"]
        if unrecovered:
            check("recovery_time", False, f"service did not recover within the {spec['observe_s']}s window")
        else:
            check("recovery_time", longest <= limit, f"longest outage {longest:.1f}s (limit {limit}s)")
    if "min_availability_pct" in thresholds:
        limit = thresholds["min_availability_pct"]
        check("availability", metrics["availability_pct"] >= limit,
              f"{metrics['availability_pct']}% of {len(samples)} probes succeeded (limit {limit}%)")
    if "max_p95_latency_ms" in thresholds:
        limit = thresholds["max_p95_latency_ms"]
        p95 = metrics["p95_latency_ms"]
        check("p95_latency", p95 is not None and p95 <= limit, f"p95 {p95} ms (limit {limit} ms)")
    if thresholds.get("must_end_healthy", True):
        check("ends_healthy", final is not None,
              "service reported UP after the fault" if final else f"service not UP {settings['final_wait_s']}s after the fault")
    return report


def write_reports(report, report_dir):
    name = report["experiment"]
    with open(os.path.join(report_dir, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    failures = sum(not c["passed"] for c in report["checks"])
    cases = []
    for c in report["checks"]:
        case = f'  <testcase classname={quoteattr("resilience." + name)} name={quoteattr(c["name"])}>'
        if not c["passed"]:
            case += f'<failure message={quoteattr(c["detail"])}>{escape(c["detail"])}</failure>'
        cases.append(case + "</testcase>")
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<testsuite name={quoteattr("chaos-gate." + name)} tests="{len(report["checks"])}" failures="{failures}">\n'
           + "\n".join(cases) + "\n</testsuite>\n")
    with open(os.path.join(report_dir, f"TEST-{name}.xml"), "w", encoding="utf-8") as f:
        f.write(xml)


def main():
    parser = argparse.ArgumentParser(description="Run chaos experiments and gate the pipeline on the result.")
    parser.add_argument("--config", default=os.path.join(REPO_ROOT, "monitoring", "experiments.json"))
    parser.add_argument("--base-url", required=True, help="service under test, e.g. http://10.0.0.5:8080")
    parser.add_argument("--experiment", action="append", help="run only this experiment (repeatable)")
    parser.add_argument("--inventory", default="ansible/inventories/staging.yml")
    parser.add_argument("--report-dir", default=os.path.join(REPO_ROOT, "reports", "chaos"))
    args = parser.parse_args()
    args.base_url = args.base_url.rstrip("/")

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    settings, experiments = config["settings"], config["experiments"]
    selected = args.experiment or list(experiments)
    unknown = [e for e in selected if e not in experiments]
    if unknown:
        parser.error(f"unknown experiment(s): {', '.join(unknown)}; available: {', '.join(experiments)}")
    os.makedirs(args.report_dir, exist_ok=True)

    results = []
    for name in selected:
        report = run_experiment(name, experiments[name], settings, args)
        write_reports(report, args.report_dir)
        results.append((name, all(c["passed"] for c in report["checks"]), report.get("metrics", {})))

    def show(value, unit):
        return "n/a" if value is None else f"{value}{unit}"

    print("\n=== Resilience gate summary")
    for name, passed, m in results:
        outage = "never recovered" if m and not m.get("recovered", True) else show(m.get("longest_outage_s"), "s")
        print(f"  {'PASS' if passed else 'FAIL'}  {name:<18} availability={show(m.get('availability_pct'), '%')}  "
              f"longest_outage={outage}  p95={show(m.get('p95_latency_ms'), 'ms')}")
    gate_passed = all(p for _, p, _ in results)
    print(f"\nGATE {'PASSED: promotion allowed' if gate_passed else 'FAILED: promotion blocked'}")
    sys.exit(0 if gate_passed else 1)


if __name__ == "__main__":
    main()
