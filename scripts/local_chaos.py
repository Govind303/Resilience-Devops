"""Fault injection for the local demo (the staging pipeline uses the Ansible playbooks instead).

  kill               terminate the app process started by local_demo.py
  cpu --duration N   busy-loop every CPU core for N seconds
  network --duration N   black-hole the quote dependency for N seconds
"""
import argparse
import multiprocessing
import os
import signal
import time

DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".demo")


def burn(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fault", choices=["kill", "cpu", "network"])
    parser.add_argument("--duration", type=float, default=15)
    args = parser.parse_args()

    if args.fault == "kill":
        with open(os.path.join(DEMO_DIR, "app.pid"), encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))  # SIGTERM terminates the process on Windows
        print(f"killed app process {pid}")
    elif args.fault == "cpu":
        workers = [multiprocessing.Process(target=burn, args=(args.duration,)) for _ in range(os.cpu_count() or 2)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        print(f"burned {len(workers)} cores for {args.duration}s")
    else:
        flag = os.path.join(DEMO_DIR, "blackhole")
        open(flag, "w").close()
        try:
            time.sleep(args.duration)
        finally:
            os.remove(flag)
        print(f"quote dependency black-holed for {args.duration}s")


if __name__ == "__main__":
    main()
