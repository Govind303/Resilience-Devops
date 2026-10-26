"""Post-switch check of live production traffic through nginx.

Passes when the expected version is serving and at least --min-success-pct of probes to
/health and /api/quote succeed. A non-zero exit makes Azure Pipelines run the rollback step.
"""
import argparse
import json
import sys
import time
import urllib.request


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status, json.loads(r.read() or b"{}")
    except Exception:
        return None, {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="public URL, e.g. http://20.1.2.3")
    parser.add_argument("--expect-version", required=True)
    parser.add_argument("--probes", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--min-success-pct", type=float, default=100.0)
    args = parser.parse_args()
    base = args.url.rstrip("/")

    ok, versions = 0, set()
    for i in range(args.probes):
        health_code, health = get(base + "/health")
        quote_code, _ = get(base + "/api/quote")
        versions.add(health.get("version"))
        if health_code == 200 and quote_code == 200 and health.get("version") == args.expect_version:
            ok += 1
        time.sleep(args.interval)

    pct = 100 * ok / args.probes
    print(f"{ok}/{args.probes} probes healthy on {args.expect_version} ({pct:.1f}%); versions seen: {sorted(map(str, versions))}")
    if pct < args.min_success_pct:
        sys.exit(f"verification failed: below {args.min_success_pct}%")
    print("production verified")


if __name__ == "__main__":
    main()
