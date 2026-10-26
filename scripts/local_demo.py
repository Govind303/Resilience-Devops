"""Run the full chaos gate on one machine, with no VMs or cloud account.

It starts the quote dependency and the app jar under a small supervisor that restarts the app
when it dies (standing in for systemd's Restart=always), then runs the resilience gate with the
local fault injectors.

  python scripts/local_demo.py            resilient build: the gate should PASS
  python scripts/local_demo.py --fragile  no auto-restart, no graceful degradation: the gate should FAIL

Build the jar first:  cd app && mvn -B clean verify
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_DIR = os.path.join(ROOT, ".demo")
JAR = os.path.join(ROOT, "app", "target", "resilienceops-app.jar")


def real_java():
    """Path of the actual JVM binary. On Windows the `java` on PATH is often a launcher stub that
    starts the JVM as a child, so killing the stub would leave the real app running."""
    out = subprocess.run(["java", "-XshowSettings:properties", "-version"], capture_output=True, text=True).stderr
    for line in out.splitlines():
        if line.strip().startswith("java.home ="):
            home = line.split("=", 1)[1].strip()
            exe = os.path.join(home, "bin", "java.exe" if os.name == "nt" else "java")
            if os.path.exists(exe):
                return exe
    return shutil.which("java")


def supervise(java, env, self_healing, stop):
    """Keeps the app running the way systemd would: restart 2 s after any exit."""
    while not stop.is_set():
        proc = subprocess.Popen([java, "-jar", JAR], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(os.path.join(DEMO_DIR, "app.pid"), "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
        supervise.current = proc
        proc.wait()
        if stop.is_set():
            break
        if not self_healing:
            print(f"[supervisor] app exited with code {proc.returncode}; self-healing is OFF, not restarting", flush=True)
            break
        print(f"[supervisor] app exited with code {proc.returncode}; restarting in 2 s", flush=True)
        time.sleep(2)


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_app(url, seconds):
    """True once /health answers with this app's JSON (not some other service on the port)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200 and json.loads(r.read()).get("color") == "local-demo":
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fragile", action="store_true", help="disable auto-restart and graceful degradation")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--downstream-port", type=int, default=19090)
    parser.add_argument("--experiment", action="append", help="run only this experiment (repeatable)")
    args = parser.parse_args()

    if not os.path.exists(JAR):
        sys.exit(f"{JAR} not found. Build it first: cd app && mvn -B clean verify")
    if shutil.which("java") is None:
        sys.exit("java is not on PATH")
    for port in (args.port, args.downstream_port):
        if port_in_use(port):
            sys.exit(f"port {port} is already in use; pick another with --port / --downstream-port")
    os.makedirs(DEMO_DIR, exist_ok=True)
    blackhole = os.path.join(DEMO_DIR, "blackhole")
    if os.path.exists(blackhole):
        os.remove(blackhole)

    label = "fragile" if args.fragile else "resilient"
    base_url = f"http://127.0.0.1:{args.port}"
    env = dict(os.environ,
               APP_PORT=str(args.port),
               APP_COLOR="local-demo",
               APP_VERSION=f"demo-{label}",
               RESILIENCE_MODE=label,
               CHECK_INTERVAL_MS="1000",
               DOWNSTREAM_URL=f"http://127.0.0.1:{args.downstream_port}/quote")

    downstream = subprocess.Popen([sys.executable, os.path.join(ROOT, "downstream", "quote_service.py"),
                                   "--port", str(args.downstream_port), "--blackhole-file", blackhole],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stop = threading.Event()
    supervisor = threading.Thread(target=supervise, args=(real_java(), env, not args.fragile, stop), daemon=True)
    supervisor.start()
    try:
        if not wait_for_app(base_url + "/health", 30):
            sys.exit("app did not become healthy within 30 s")
        print(f"[demo] {label} build running at {base_url}", flush=True)
        gate = [sys.executable, os.path.join(ROOT, "monitoring", "resilience_gate.py"),
                "--config", os.path.join(ROOT, "monitoring", "experiments-local.json"),
                "--base-url", base_url,
                "--report-dir", os.path.join(ROOT, "reports", f"local-{label}")]
        for name in args.experiment or []:
            gate += ["--experiment", name]
        rc = subprocess.call(gate, cwd=ROOT)
    finally:
        stop.set()
        proc = getattr(supervise, "current", None)
        if proc and proc.poll() is None:
            proc.terminate()
        downstream.terminate()
    sys.exit(rc)


if __name__ == "__main__":
    main()
