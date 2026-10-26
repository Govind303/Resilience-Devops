"""Start the Azure Pipelines blue-green release for a build that passed the chaos gate.

Uses the Azure DevOps "Runs - Run Pipeline" REST API. Needs these environment variables:
  AZDO_ORG          organisation name (dev.azure.com/<org>)
  AZDO_PROJECT      project name
  AZDO_PIPELINE_ID  numeric ID of the pipeline created from azure-pipelines.yml
  AZDO_PAT          personal access token with Build (read & execute) scope
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="app version that passed the gate")
    parser.add_argument("--commit", default="", help="git commit Jenkins built")
    parser.add_argument("--branch", default="refs/heads/main")
    args = parser.parse_args()

    missing = [v for v in ("AZDO_ORG", "AZDO_PROJECT", "AZDO_PIPELINE_ID", "AZDO_PAT") if not os.environ.get(v)]
    if missing:
        sys.exit(f"cannot trigger Azure Pipelines: set {', '.join(missing)}")

    org, project = os.environ["AZDO_ORG"], urllib.parse.quote(os.environ["AZDO_PROJECT"])
    url = (f"https://dev.azure.com/{org}/{project}/_apis/pipelines/"
           f"{os.environ['AZDO_PIPELINE_ID']}/runs?api-version=7.1")
    repo = {"refName": args.branch}
    if args.commit:
        repo["version"] = args.commit
    body = {
        "resources": {"repositories": {"self": repo}},
        "templateParameters": {"appVersion": args.version, "sourceCommit": args.commit},
    }
    token = base64.b64encode(f":{os.environ['AZDO_PAT']}".encode()).decode()
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            run = json.load(response)
    except urllib.error.HTTPError as err:
        sys.exit(f"Azure DevOps refused the request (HTTP {err.code}): {err.read().decode(errors='replace')}")
    web = run.get("_links", {}).get("web", {}).get("href", "")
    print(f"Azure Pipelines run {run.get('id')} started for {args.version}: {web}")


if __name__ == "__main__":
    main()
