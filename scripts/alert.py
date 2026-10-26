"""Send a pipeline alert to a chat webhook (Slack, Microsoft Teams, Discord-compatible).

Set ALERT_WEBHOOK_URL to an incoming-webhook URL. Without it the alert is only printed.
Never fails the pipeline: a broken webhook should not hide the real result.
"""
import argparse
import json
import os
import urllib.request

ICONS = {"info": "[OK]", "warning": "[WARN]", "critical": "[ALERT]"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--severity", choices=list(ICONS), default="info")
    parser.add_argument("--link", default="")
    args = parser.parse_args()

    text = f"{ICONS[args.severity]} {args.title}\n{args.message}"
    if args.link:
        text += f"\n{args.link}"
    print(text)

    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not url or url.startswith("$("):  # unset, or an Azure variable that was never defined
        print("(ALERT_WEBHOOK_URL not set: alert printed only)")
        return
    payload = json.dumps({"text": text, "content": text}).encode()
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(f"alert sent (HTTP {response.status})")
    except Exception as err:
        print(f"warning: could not send alert: {err}")


if __name__ == "__main__":
    main()
