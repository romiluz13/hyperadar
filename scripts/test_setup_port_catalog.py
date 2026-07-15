import json
import subprocess
import sys
import unittest
from pathlib import Path

from setup_port_catalog import (
    build_agent_entities,
    build_catalog_blueprints,
    provision_blueprint,
    provision_entity,
    retire_legacy_port_assets,
)
from test_setup_port_workflows import FakePortClient


class PortCatalogSetupTests(unittest.TestCase):
    def test_catalog_contains_only_the_three_runtime_blueprints(self):
        blueprints = build_catalog_blueprints()

        self.assertEqual(
            [blueprint["identifier"] for blueprint in blueprints],
            ["hyperadar_agent", "hyperadar_project", "hyperadar_post"],
        )
        post = blueprints[-1]
        self.assertEqual(post["relations"]["agent"]["target"], "hyperadar_agent")
        self.assertTrue(post["relations"]["agent"]["required"])
        self.assertEqual(post["relations"]["project"]["target"], "hyperadar_project")
        self.assertTrue(post["relations"]["project"]["required"])

    def test_dry_run_prints_blueprints_without_credentials(self):
        script = Path(__file__).with_name("setup_port_catalog.py")

        result = subprocess.run(
            [sys.executable, str(script), "--dry-run"],
            check=True,
            capture_output=True,
            text=True,
            env={},
        )

        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["blueprints"]), 3)
        self.assertEqual(len(payload["agents"]), 5)
        reddit = next(
            agent
            for agent in payload["agents"]
            if agent["identifier"] == "reddit-pulse"
        )
        self.assertEqual(reddit["properties"]["status"], "muted")

    def test_agent_seed_contains_every_creator_without_invented_health_metrics(self):
        entities = build_agent_entities(brightdata_api_key="real-secret")

        self.assertEqual(
            [entity["identifier"] for entity in entities],
            [
                "github-radar",
                "reddit-pulse",
                "youtube-trends",
                "hidden-gems",
                "weekly-digest",
            ],
        )
        self.assertTrue(
            all(entity["properties"]["status"] == "active" for entity in entities)
        )
        self.assertTrue(
            all(
                not {"lastRunAt", "runCount", "successRate"}
                & entity["properties"].keys()
                for entity in entities
            )
        )

    def test_reddit_seed_is_muted_without_a_real_bright_data_key(self):
        entities = build_agent_entities(brightdata_api_key="")
        reddit = next(
            entity for entity in entities if entity["identifier"] == "reddit-pulse"
        )

        self.assertEqual(reddit["properties"]["status"], "muted")

        placeholder_entities = build_agent_entities(
            brightdata_api_key="your_bright_data_api_key"
        )
        placeholder_reddit = next(
            entity
            for entity in placeholder_entities
            if entity["identifier"] == "reddit-pulse"
        )
        self.assertEqual(placeholder_reddit["properties"]["status"], "muted")

    def test_provisioning_creates_a_missing_blueprint(self):
        blueprint = build_catalog_blueprints()[0]
        client = FakePortClient([(404, {}), (201, {"ok": True})])

        outcome = provision_blueprint(client, blueprint)

        self.assertEqual(outcome, "created")
        self.assertEqual(
            client.calls,
            [
                ("GET", "/blueprints/hyperadar_agent", None, None),
                ("POST", "/blueprints", blueprint, None),
            ],
        )

    def test_provisioning_updates_an_existing_blueprint(self):
        blueprint = build_catalog_blueprints()[1]
        client = FakePortClient([(200, {"ok": True}), (200, {"ok": True})])

        outcome = provision_blueprint(client, blueprint)

        self.assertEqual(outcome, "updated")
        self.assertEqual(
            client.calls,
            [
                ("GET", "/blueprints/hyperadar_project", None, None),
                (
                    "PUT",
                    "/blueprints/hyperadar_project",
                    blueprint,
                    None,
                ),
            ],
        )

    def test_provisioning_surfaces_lookup_failures(self):
        blueprint = build_catalog_blueprints()[0]
        client = FakePortClient([(503, {"message": "unavailable"})])

        with self.assertRaisesRegex(RuntimeError, "lookup status: 503"):
            provision_blueprint(client, blueprint)

    def test_entity_seed_updates_existing_agent(self):
        entity = build_agent_entities("")[0]
        client = FakePortClient([(200, {"ok": True})])

        outcome = provision_entity(client, "hyperadar_agent", entity)

        self.assertEqual(outcome, "updated")
        self.assertEqual(
            client.calls,
            [
                (
                    "PATCH",
                    "/blueprints/hyperadar_agent/entities/github-radar",
                    {
                        **entity,
                        "properties": {
                            key: value
                            for key, value in entity["properties"].items()
                            if key != "status"
                        },
                    },
                    None,
                )
            ],
        )

    def test_existing_agent_patch_preserves_operator_owned_status(self):
        entity = build_agent_entities("")[0]
        client = FakePortClient([(200, {"ok": True})])

        provision_entity(client, "hyperadar_agent", entity)

        patch_payload = client.calls[0][2]
        self.assertNotIn("status", patch_payload["properties"])
        self.assertEqual(entity["properties"]["status"], "active")

    def test_existing_reddit_agent_is_muted_when_its_required_key_is_missing(self):
        entity = next(
            agent
            for agent in build_agent_entities("")
            if agent["identifier"] == "reddit-pulse"
        )
        client = FakePortClient([(200, {"ok": True})])

        provision_entity(client, "hyperadar_agent", entity)

        patch_payload = client.calls[0][2]
        self.assertEqual(patch_payload["properties"]["status"], "muted")

    def test_entity_seed_creates_only_after_not_found(self):
        entity = build_agent_entities("")[0]
        client = FakePortClient([(404, {}), (201, {"ok": True})])

        outcome = provision_entity(client, "hyperadar_agent", entity)

        self.assertEqual(outcome, "created")

    def test_catalog_setup_removes_retired_webhook_actions_and_scorecards(self):
        client = FakePortClient([(200, {"ok": True})] * 6 + [(200, {"ok": True})] * 3)

        outcomes = retire_legacy_port_assets(client)

        self.assertEqual(outcomes, {"actions": 6, "scorecards": 3})
        self.assertEqual(
            [call[1] for call in client.calls[:6]],
            [
                "/actions/run_agent_now",
                "/actions/track_project",
                "/actions/boost_post",
                "/actions/mute_agent",
                "/actions/retire_agent",
                "/actions/generate_digest",
            ],
        )

    def test_retired_port_assets_treat_missing_as_already_clean(self):
        client = FakePortClient([(404, {})] * 9)

        self.assertEqual(
            retire_legacy_port_assets(client), {"actions": 0, "scorecards": 0}
        )


if __name__ == "__main__":
    unittest.main()
