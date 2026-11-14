#!/usr/bin/env python3
"""Tests for collect-failures.sh against a stubbed gh CLI (no GitHub calls)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DATE = "2026-11-12"
ORG = "fullsend-ai"
WF_PATH = ".github/workflows/fullsend.yaml"

# Answers each `gh api ENDPOINT [--paginate] [--jq FILTER]` from a JSON map of
# endpoint -> response; {"__error__": msg} fails like gh does. An endpoint
# missing from the map is an error, so the tests catch unexpected calls.
# A `gh api graphql` call is looked up as "graphql:ids=ID,ID,..." (the
# `ids[]` fields) or "graphql:id=ID" (the `id` field).
GH_STUB = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, subprocess, sys
    args = sys.argv[1:]
    assert args[0] == "api", args
    endpoint, jq_filter, paginate, rest = None, ".", False, args[1:]
    ids, node_id = [], None
    while rest:
        arg = rest.pop(0)
        if arg == "--jq":
            jq_filter = rest.pop(0)
        elif arg == "--paginate":
            paginate = True
        elif arg in ("-f", "-F"):
            name, _, value = rest.pop(0).partition("=")
            if name == "ids[]":
                ids.append(value)
            elif name == "id":
                node_id = value
        elif arg.startswith("-"):
            continue
        elif endpoint is None:
            endpoint = arg
    if endpoint == "graphql":
        endpoint = "graphql:ids=" + ",".join(ids) if ids else "graphql:id=" + str(node_id)
    stub_dir = os.environ["GH_STUB_DIR"]
    with open(os.path.join(stub_dir, "calls.log"), "a") as log:
        log.write(endpoint + "\\n")
    with open(os.path.join(stub_dir, "responses.json")) as f:
        responses = json.load(f)
    response = responses.get(endpoint, {"__error__": "gh: Not Found (HTTP 404)"})
    # {"__pages__": [...]} models a multi-page response: it is only served to a
    # --paginate request (jq runs on each page, as in gh); without --paginate
    # only the first page comes back.
    if isinstance(response, dict) and "__pages__" in response:
        pages = response["__pages__"] if paginate else response["__pages__"][:1]
    else:
        pages = [response]
    for page in pages:
        if isinstance(page, dict) and "__error__" in page:
            print(page["__error__"], file=sys.stderr)
            sys.exit(1)
        result = subprocess.run(["jq", "-r", jq_filter], input=json.dumps(page),
                                capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode:
            sys.exit(result.returncode)
    """
)


def repo_list(*names):
    return [{"name": n, "visibility": "public", "archived": False, "fork": False} for n in names]


def workflows(path=WF_PATH, wf_id=7):
    return {"workflows": [{"id": 1, "path": ".github/workflows/ci.yml"}, {"id": wf_id, "path": path}]}


def run(run_id, conclusion, repo="fullsend"):
    return {"id": run_id, "conclusion": conclusion,
            "html_url": f"https://github.com/{ORG}/{repo}/actions/runs/{run_id}",
            "check_suite_node_id": suite_id(run_id)}


def suite_id(run_id):
    return f"CS_{run_id}"


def check_run(name, conclusion):
    return {"name": name, "status": "COMPLETED", "conclusion": conclusion}


def jobs(*active):
    """Check runs of one dispatch run; `active` maps role job -> conclusion."""
    names = ["Route", "Harness dispatch", "Code", "Fix", "Review", "Triage", "Retro", "Prioritize"]
    result = [check_run("stop-fix", "SKIPPED"),
              check_run("dispatch / Harness run (${{ matrix.agent }})", "SKIPPED")]
    active = dict(active)
    for name in names:
        default = "SUCCESS" if name in ("Route", "Harness dispatch") else "SKIPPED"
        result.append(check_run(f"dispatch / {name}", active.pop(name, default).upper()))
    for name, conclusion in active.items():
        result.append(check_run(f"dispatch / {name}", conclusion.upper()))
    return result


def paged(*pages):
    """Check runs of a suite whose `checkRuns` span several pages."""
    return {"__paged__": list(pages)}


def runs_endpoint(repo, wf_id=7, per_page=100, window=DATE):
    return (f"/repos/{ORG}/{repo}/actions/workflows/{wf_id}/runs"
            f"?per_page={per_page}&status=completed&created={window}")


def batch_key(run_ids):
    return "graphql:ids=" + ",".join(suite_id(r) for r in run_ids)


def check_runs_page(nodes, has_next=False):
    return {"pageInfo": {"hasNextPage": has_next, "endCursor": "c"}, "nodes": nodes}


def repo_responses(repo, run_list, job_map, wf_id=7, total=None):
    """Responses for one repo. `job_map` maps each started run's id to its check
    runs (a list), paged(...), None (suite not found) or {"__error__": msg}."""
    started = [r["id"] for r in run_list if r["conclusion"] not in ("skipped", "action_required")]
    responses = {
        f"/repos/{ORG}/{repo}/actions/workflows": workflows(wf_id=wf_id),
        runs_endpoint(repo, wf_id, per_page=1): {"total_count": len(run_list) if total is None else total},
        runs_endpoint(repo, wf_id): {"workflow_runs": run_list},
    }
    for start in range(0, len(started), 100):
        batch = started[start:start + 100]
        errors = [job_map[r] for r in batch if isinstance(job_map[r], dict) and "__error__" in job_map[r]]
        if errors:
            responses[batch_key(batch)] = errors[0]
            continue
        nodes = []
        for rid in batch:
            value = job_map[rid]
            if value is None:
                nodes.append(None)
            elif isinstance(value, dict):  # paged: the batch holds only the first page
                nodes.append({"checkRuns": check_runs_page(value["__paged__"][0], has_next=True)})
                pages = [{"__error__": p["__error__"]} if isinstance(p, dict) else
                         {"data": {"node": {"checkRuns": check_runs_page(p, has_next=i < len(value["__paged__"]) - 1)}}}
                         for i, p in enumerate(value["__paged__"])]
                responses[f"graphql:id={suite_id(rid)}"] = {"__pages__": pages}
            else:
                nodes.append({"checkRuns": check_runs_page(value)})
        responses[batch_key(batch)] = {"data": {"nodes": nodes}}
    return responses


class CollectFailuresTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        self.work = self.tmp / "work"
        (self.work / "docs").mkdir(parents=True)
        self.env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                        GH_STUB_DIR=str(self.tmp), GH_RETRY_SLEEP="0")

    def collect(self, responses, script="collect-failures.sh", *args):
        (self.tmp / "responses.json").write_text(json.dumps(responses))
        return subprocess.run(["bash", str(SCRIPTS / script), *(args or (DATE,))], cwd=self.work,
                              env=self.env, capture_output=True, text=True, check=False)

    def csv(self, name):
        path = self.work / "docs" / name
        return path.read_text().splitlines() if path.exists() else []

    def calls(self):
        log = self.tmp / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def test_counts_agent_jobs_per_role_across_repos(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend", "agents", "no-agents")}
        responses.update(repo_responses("fullsend", [
            run(1, "success"), run(2, "failure"), run(3, "success"), run(4, "skipped"),
            run(5, "action_required"), run(6, "success"),
        ], {
            1: jobs(("Code", "success")),
            2: jobs(("Code", "failure")),
            3: jobs(),  # routed to no stage
            6: jobs(("Harness run (fix-judge)", "timed_out")),
        }))
        responses.update(repo_responses("agents", [run(10, "success", "agents"), run(11, "cancelled", "agents")], {
            10: jobs(("Triage", "success")),
            11: jobs(("Code", "cancelled")),
        }, wf_id=9))
        responses[f"/repos/{ORG}/no-agents/actions/workflows"] = {"workflows": []}

        result = self.collect(responses)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.csv("failures.csv"), [
            "date,workflow,runs,failures,failure_rate",
            f"{DATE},Code,2,1,0.500",
            f"{DATE},Triage,1,0,0.000",
            f"{DATE},fix-judge,1,1,1.000",
        ])
        self.assertEqual(sorted(self.csv("failure-details.csv")[1:]), sorted([
            f"{DATE},Code,fullsend,1,success,https://github.com/{ORG}/fullsend/actions/runs/1",
            f"{DATE},Code,fullsend,2,failure,https://github.com/{ORG}/fullsend/actions/runs/2",
            f"{DATE},fix-judge,fullsend,6,failure,https://github.com/{ORG}/fullsend/actions/runs/6",
            f"{DATE},Triage,agents,10,success,https://github.com/{ORG}/agents/actions/runs/10",
        ]))
        # One query per repo; skipped and approval-pending runs never started a
        # job, so their suites are not asked for. A cancelled agent job
        # (superseded run) is neither a success nor a failure.
        self.assertEqual([c for c in self.calls() if c.startswith("graphql")],
                         [batch_key([1, 2, 3, 6]), batch_key([10, 11])])

    def test_warns_and_writes_nothing_when_no_agent_job_ran(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        responses.update(repo_responses("fullsend", [run(1, "success"), run(2, "skipped")], {1: jobs()}))

        result = self.collect(responses)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning::No agent jobs ran", result.stdout)
        self.assertEqual(self.csv("failures.csv"), ["date,workflow,runs,failures,failure_rate"])
        self.assertEqual(self.csv("failure-details.csv"), ["date,workflow,repo,run_id,status,url"])

    def test_api_error_fails_and_writes_nothing(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend", "agents")}
        responses.update(repo_responses("fullsend", [run(1, "success")], {1: jobs(("Code", "failure"))}))
        responses.update(repo_responses("agents", [run(10, "success", "agents")], {
            10: {"__error__": "gh: Server Error (HTTP 502)"},
        }))

        result = self.collect(responses)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("::error::Could not collect agent runs", result.stdout)
        self.assertIn("HTTP 502", result.stderr)
        self.assertEqual(self.csv("failures.csv"), ["date,workflow,runs,failures,failure_rate"])
        self.assertEqual(self.csv("failure-details.csv"), ["date,workflow,repo,run_id,status,url"])
        # Retried before giving up.
        self.assertEqual(self.calls().count(batch_key([10])), 3)

    def test_counts_jobs_on_later_pages(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        responses.update(repo_responses("fullsend", [run(1, "failure"), run(2, "success")], {
            1: paged(jobs(("Code", "success")), [check_run("dispatch / Review", "FAILURE")]),
            2: jobs(("Code", "success")),
        }))

        result = self.collect(responses)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.csv("failures.csv")[1:], [
            f"{DATE},Code,2,0,0.000",
            f"{DATE},Review,1,1,1.000",
        ])
        # Run 1 comes from the paginated query only, so its first page is not counted twice.
        self.assertEqual(self.calls().count(f"graphql:id={suite_id(1)}"), 1)

    def test_later_page_error_exhausts_retries_and_writes_nothing(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        responses.update(repo_responses("fullsend", [run(1, "success")], {
            1: paged(jobs(("Code", "failure")), {"__error__": "gh: Server Error (HTTP 502)"}),
        }))

        result = self.collect(responses)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("HTTP 502", result.stderr)
        self.assertEqual(self.csv("failures.csv"), ["date,workflow,runs,failures,failure_rate"])
        self.assertEqual(self.csv("failure-details.csv"), ["date,workflow,repo,run_id,status,url"])
        self.assertEqual(self.calls().count(f"graphql:id={suite_id(1)}"), 3)

    def test_runs_are_looked_up_in_batches_of_100(self):
        run_list = [run(i, "success") for i in range(1, 102)]
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        responses.update(repo_responses("fullsend", run_list, {i: jobs(("Code", "success")) for i in range(1, 102)}))

        result = self.collect(responses)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.csv("failures.csv")[1:], [f"{DATE},Code,101,0,0.000"])
        self.assertEqual([c for c in self.calls() if c.startswith("graphql")],
                         [batch_key(range(1, 101)), batch_key([101])])

    def test_missing_check_suite_fails_and_writes_nothing(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        responses.update(repo_responses("fullsend", [run(1, "success"), run(2, "failure")], {
            1: jobs(("Code", "success")),
            2: None,
        }))

        result = self.collect(responses)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("check suites missing", result.stderr)
        self.assertEqual(self.csv("failures.csv"), ["date,workflow,runs,failures,failure_rate"])
        self.assertEqual(self.csv("failure-details.csv"), ["date,workflow,repo,run_id,status,url"])

    def test_failed_attempt_before_a_successful_rerun_is_counted(self):
        # With checkType ALL the check suite lists every attempt of a rerun job.
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        responses.update(repo_responses("fullsend", [run(1, "success")], {
            1: [check_run("dispatch / Code", "FAILURE"), check_run("dispatch / Code", "SUCCESS")],
        }))

        result = self.collect(responses)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.csv("failures.csv")[1:], [f"{DATE},Code,2,1,0.500"])

    def test_fails_when_no_repo_has_the_dispatch_workflow(self):
        responses = {
            f"/orgs/{ORG}/repos": repo_list(".fullsend"),
            f"/repos/{ORG}/.fullsend/actions/workflows": workflows(path=".github/workflows/sync.yml"),
        }

        result = self.collect(responses)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"no {ORG} repo has {WF_PATH}", result.stderr)
        self.assertEqual(self.csv("failures.csv"), ["date,workflow,runs,failures,failure_rate"])

    def test_fails_when_not_all_runs_could_be_listed(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        responses.update(repo_responses("fullsend", [run(1, "success")], {1: jobs(("Code", "success"))}, total=2))

        result = self.collect(responses)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("listed 1 of 2 fullsend runs", result.stderr)
        self.assertEqual(self.csv("failures.csv"), ["date,workflow,runs,failures,failure_rate"])

    def test_busy_day_is_listed_in_hourly_windows(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        hourly = {}
        for hour in range(24):
            window = f"{DATE}T{hour:02d}:00:00Z..{DATE}T{hour:02d}:59:59Z"
            hourly[window] = [run(1, "success")] + [run(100 + hour * 50 + i, "skipped") for i in range(41)] \
                if hour == 0 else [run(100 + hour * 50 + i, "skipped") for i in range(42)]
        all_runs = [r for runs in hourly.values() for r in runs]
        responses.update(repo_responses("fullsend", all_runs, {1: jobs(("Review", "success"))}, total=1001))
        del responses[runs_endpoint("fullsend")]
        for window, runs in hourly.items():
            responses[runs_endpoint("fullsend", window=window)] = {"workflow_runs": runs}

        result = self.collect(responses)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.csv("failures.csv")[1:], [f"{DATE},Review,1,0,0.000"])

    def test_existing_date_is_skipped_without_api_calls(self):
        (self.work / "docs" / "failures.csv").write_text(
            f"date,workflow,runs,failures,failure_rate\n{DATE},Code,1,0,0.000\n")

        result = self.collect({})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already exists", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_backfill_details_uses_dispatch_workflow(self):
        responses = {f"/orgs/{ORG}/repos": repo_list("fullsend")}
        responses.update(repo_responses("fullsend", [run(1, "failure")], {1: jobs(("Fix", "failure"))}))

        result = self.collect(responses, "backfill-failure-details.sh", DATE, DATE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.csv("failure-details.csv")[1:], [
            f"{DATE},Fix,fullsend,1,failure,https://github.com/{ORG}/fullsend/actions/runs/1",
        ])


class AgentRoleForJobTest(unittest.TestCase):
    def role(self, job_name):
        result = subprocess.run(
            ["bash", "-c", f'source "{SCRIPTS}/lib.sh"; agent_role_for_job "$1"', "_", job_name],
            capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def test_roles(self):
        self.assertEqual(self.role("dispatch / Code"), "Code")
        self.assertEqual(self.role("dispatch / Prioritize"), "Prioritize")
        self.assertEqual(self.role("dispatch / Harness run (fix-judge)"), "fix-judge")

    def test_non_agent_jobs(self):
        self.assertIsNone(self.role("dispatch / Route"))
        self.assertIsNone(self.role("dispatch / Harness dispatch"))
        self.assertIsNone(self.role("dispatch / Harness run (${{ matrix.agent }})"))
        self.assertIsNone(self.role("stop-fix"))


if __name__ == "__main__":
    unittest.main()
