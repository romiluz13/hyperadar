import json
import subprocess
import sys
import unittest
from pathlib import Path

from report_port_workflow_run import report_workflow_node
from setup_port_workflows import build_run_agent_workflow, provision_workflow


class FakePortClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, path, payload=None, headers=None):
        self.calls.append((method, path, payload, headers))
        return next(self.responses)


class RunAgentWorkflowTests(unittest.TestCase):
    def test_dispatches_selected_agent_through_github_ocean(self):
        workflow = build_run_agent_workflow("github-ocean")

        self.assertEqual(workflow["identifier"], "run-hyperadar-agent")
        self.assertEqual(workflow["category"], "Agent Operations")

        trigger, dispatch = workflow["nodes"]
        self.assertEqual(trigger["config"]["type"], "SELF_SERVE_TRIGGER")
        self.assertEqual(trigger["config"]["permissions"], {})
        self.assertEqual(
            trigger["config"]["userInputs"]["properties"]["agent"],
            {
                "type": "string",
                "format": "entity",
                "blueprint": "hyperadar_agent",
                "title": "Agent creator",
                "description": "Choose which source agent should refresh the radar.",
                "dataset": {
                    "combinator": "and",
                    "rules": [
                        {"property": "status", "operator": "=", "value": "active"}
                    ],
                },
            },
        )
        self.assertEqual(
            trigger["config"]["contexts"],
            [{"on": "ENTITY", "userInput": "agent"}],
        )

        self.assertEqual(dispatch["config"]["type"], "INTEGRATION_ACTION")
        self.assertEqual(dispatch["config"]["installationId"], "github-ocean")
        self.assertEqual(dispatch["config"]["integrationProvider"], "github-ocean")
        self.assertEqual(
            dispatch["config"]["integrationInvocationType"], "dispatch_workflow"
        )
        self.assertEqual(
            dispatch["config"]["integrationActionExecutionProperties"],
            {
                "org": "romiluz13",
                "repo": "hyperadar",
                "workflow": "run-hyperadar-agent.yml",
                "workflowInputs": {
                    "agent": "{{ .outputs.trigger.agent }}",
                    "port_node_run_id": "{{ .workflowNodeRun.identifier }}",
                },
                "reportWorkflowStatus": False,
            },
        )
        self.assertEqual(
            dispatch["links"],
            ["https://github.com/romiluz13/hyperadar/actions"],
        )
        self.assertEqual(
            workflow["connections"],
            [
                {
                    "sourceIdentifier": "select-agent",
                    "targetIdentifier": "run-agent",
                }
            ],
        )

    def test_dry_run_prints_the_publishable_workflow_without_credentials(self):
        script = Path(__file__).with_name("setup_port_workflows.py")
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--dry-run",
                "--installation-id",
                "github-ocean",
            ],
            check=True,
            capture_output=True,
            text=True,
            env={},
        )

        workflow = json.loads(result.stdout)
        self.assertEqual(workflow["identifier"], "run-hyperadar-agent")
        self.assertEqual(
            workflow["nodes"][1]["config"]["installationId"], "github-ocean"
        )

    def test_provisioning_creates_the_workflow_when_it_does_not_exist(self):
        workflow = build_run_agent_workflow("github-ocean")
        client = FakePortClient([(404, {}), (201, {"ok": True})])

        outcome = provision_workflow(client, workflow)

        self.assertEqual(outcome, "created")
        self.assertEqual(
            client.calls,
            [
                ("GET", "/workflows/run-hyperadar-agent", None, None),
                ("POST", "/workflows", workflow, None),
            ],
        )

    def test_provisioning_updates_with_the_current_workflow_version(self):
        workflow = build_run_agent_workflow("github-ocean")
        client = FakePortClient(
            [
                (
                    200,
                    {"workflow": {"workflowVersionIdentifier": "wfv_1234567890abcdef"}},
                ),
                (200, {"ok": True}),
            ]
        )

        outcome = provision_workflow(client, workflow)

        self.assertEqual(outcome, "updated")
        self.assertEqual(
            client.calls,
            [
                ("GET", "/workflows/run-hyperadar-agent", None, None),
                (
                    "PUT",
                    "/workflows/run-hyperadar-agent",
                    workflow,
                    {"If-Match": "wfv_1234567890abcdef"},
                ),
            ],
        )

    def test_github_backend_accepts_only_known_agents_and_runs_the_selected_one(self):
        workflow_path = (
            Path(__file__).parents[1]
            / ".github"
            / "workflows"
            / "run-hyperadar-agent.yml"
        )
        contents = workflow_path.read_text()

        for agent in (
            "github-radar",
            "reddit-pulse",
            "youtube-trends",
            "hidden-gems",
            "weekly-digest",
        ):
            self.assertIn(f"- {agent}", contents)
        self.assertIn("run: uv run --frozen python main.py", contents)
        for directory in (
            "github_radar",
            "reddit_pulse",
            "youtube_trends",
            "hidden_gems",
            "weekly_digest",
        ):
            self.assertTrue(
                (
                    Path(__file__).parents[1] / "integrations" / directory / "uv.lock"
                ).is_file()
            )
        self.assertIn("port_node_run_id:", contents)
        self.assertIn("report-to-port:", contents)
        self.assertIn("needs: run-agent", contents)
        self.assertIn("if: always() && inputs.port_node_run_id != ''", contents)
        self.assertIn("JOB_RESULT: ${{ needs.run-agent.result }}", contents)
        self.assertIn("python scripts/report_port_workflow_run.py", contents)
        self.assertNotIn("JOB_STATUS: ${{ job.status }}", contents)
        self.assertIn("BRIGHTDATA_API_KEY: ${{ secrets.BRIGHTDATA_API_KEY }}", contents)
        self.assertIn("@brightdata/cli@0.3.2", contents)
        self.assertIn("yt-dlp==2026.07.04", contents)
        self.assertIn(
            "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10", contents
        )
        self.assertIn("timeout-minutes: 30", contents)
        self.assertNotIn("pull_request_target", contents)


class ReportWorkflowNodeTests(unittest.TestCase):
    def test_reports_each_github_job_result_to_the_port_node(self):
        run_url = "https://github.com/romiluz13/hyperadar/actions/runs/123"

        for job_result, port_result in (
            ("success", "SUCCESS"),
            ("failure", "FAILED"),
            ("cancelled", "CANCELLED"),
            ("skipped", "CANCELLED"),
        ):
            with self.subTest(job_result=job_result):
                client = FakePortClient([(200, {"ok": True})])

                outcome = report_workflow_node(
                    client,
                    "wfnr_1234567890abcdef",
                    job_result,
                    run_url,
                )

                self.assertEqual(outcome, port_result)
                self.assertEqual(
                    client.calls,
                    [
                        (
                            "PATCH",
                            "/workflows/nodes/runs/wfnr_1234567890abcdef",
                            {
                                "status": "COMPLETED",
                                "result": port_result,
                                "output": {"githubRunUrl": run_url},
                                "links": [run_url],
                            },
                            None,
                        )
                    ],
                )

    def test_rejects_an_untrusted_node_run_identifier(self):
        client = FakePortClient([])

        with self.assertRaisesRegex(ValueError, "node run identifier"):
            report_workflow_node(
                client,
                "../../actions",
                "success",
                "https://github.com/romiluz13/hyperadar/actions/runs/123",
            )

        self.assertEqual(client.calls, [])

    def test_surfaces_a_port_update_failure(self):
        client = FakePortClient([(500, {"message": "temporary failure"})])

        with self.assertRaisesRegex(RuntimeError, "temporary failure"):
            report_workflow_node(
                client,
                "wfnr_1234567890abcdef",
                "failure",
                "https://github.com/romiluz13/hyperadar/actions/runs/123",
            )


if __name__ == "__main__":
    unittest.main()
