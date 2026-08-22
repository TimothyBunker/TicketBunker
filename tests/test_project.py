from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ticketbunker import commands, layout, project, projectconfig, ticketcommands


def run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


class CompanionProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source = base / "source"
        self.remote = base / "ticketbunker.git"
        self.source.mkdir()
        run_git(self.source, "init", "-b", "ecs")
        run_git(self.source, "config", "user.name", "Tim")
        run_git(self.source, "config", "user.email", "tim@example.com")
        (self.source / "README.md").write_text("voxel\n", encoding="utf-8")
        run_git(self.source, "add", "README.md")
        run_git(self.source, "commit", "-m", "initial")
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def initialize(self) -> dict:
        return commands.dispatch(
            "project.initialize",
            {"project": "ECS", "bunker_repo": str(self.remote), "base_branch": "ecs"},
            commands.CommandContext(self.source),
        )

    def test_companion_init_publishes_config_data_and_locator(self) -> None:
        result = self.initialize()

        self.assertTrue(result["ok"])
        locator = json.loads((self.source / project.LOCATOR_FILE).read_text(encoding="utf-8"))
        self.assertEqual(locator["project"], "ECS")
        self.assertTrue((project.project_config_dir(self.source) / "project.json").exists())
        self.assertTrue((project.tickets_dir(self.source) / ".keep").exists())
        self.assertEqual(layout.bunkers_dir(self.source), project.tickets_dir(self.source))
        self.assertIn("/.tb/", (self.source / ".gitignore").read_text(encoding="utf-8"))
        self.assertTrue(run_git(self.remote, "show-ref", "--verify", "refs/heads/ticketbunker/config"))
        self.assertTrue(run_git(self.remote, "show-ref", "--verify", "refs/heads/ticketbunker/data"))

    def test_init_is_create_once_and_setup_is_repeatable(self) -> None:
        self.initialize()
        with self.assertRaises(commands.CommandRejected) as caught:
            self.initialize()
        self.assertEqual(caught.exception.code, "already_initialized")

        first = commands.dispatch("project.setup", {}, commands.CommandContext(self.source))
        second = commands.dispatch("project.setup", {}, commands.CommandContext(self.source))
        self.assertEqual(first["project"], "ECS")
        self.assertEqual(second["project"], "ECS")

    def test_data_publication_commits_and_pushes_companion_changes(self) -> None:
        self.initialize()
        ticket = project.tickets_dir(self.source) / "ECS-4"
        ticket.mkdir()
        (ticket / "ticket.json").write_text('{"id":"ECS-4"}\n', encoding="utf-8")

        result = project.publish_data(self.source, "Create ECS-4")

        self.assertTrue(result["published"])
        shown = run_git(self.remote, "show", "ticketbunker/data:projects/ECS/tickets/ECS-4/ticket.json")
        self.assertEqual(json.loads(shown)["id"], "ECS-4")

    def test_ticket_creation_is_one_shared_command_and_publishes(self) -> None:
        self.initialize()
        result = commands.dispatch(
            "ticket.create",
            {"ticket": {"id": "ECS-4", "key": "ECS-4", "type": "task", "status": "todo"}},
            commands.CommandContext(self.source, transport="http"),
        )

        self.assertTrue(result["publication"]["published"])
        shown = run_git(self.remote, "show", "ticketbunker/data:projects/ECS/tickets/ECS-4/ticket.json")
        self.assertEqual(json.loads(shown)["id"], "ECS-4")

    def test_ticket_path_is_project_scoped_without_bunker_suffix(self) -> None:
        self.initialize()
        self.assertEqual(
            layout.bunker_path(self.source, "ECS-4"),
            self.source / ".tb" / "data" / "projects" / "ECS" / "tickets" / "ECS-4",
        )

    def test_legacy_database_migrates_only_after_remote_publication(self) -> None:
        legacy = self.source / ".tb"
        bunker = legacy / "ECS-4_bunker"
        (bunker / "reports").mkdir(parents=True)
        (legacy / "config.json").write_text('{"project":"voxel-engine-starter"}\n', encoding="utf-8")
        (bunker / "ticket.json").write_text('{"id":"ECS-4","key":"ECS-4"}\n', encoding="utf-8")
        (bunker / "reports" / "master.md").write_text("ECS report\n", encoding="utf-8")

        result = commands.dispatch(
            "project.migrate",
            {"project": "ECS", "bunker_repo": str(self.remote), "base_branch": "ecs"},
            commands.CommandContext(self.source),
        )

        migrated = project.tickets_dir(self.source) / "ECS-4"
        backup = Path(result["backup"])
        self.assertEqual(result["imported_tickets"], ["ECS-4"])
        self.assertEqual((migrated / "reports" / "master.md").read_text(encoding="utf-8"), "ECS report\n")
        self.assertTrue((backup / "ECS-4_bunker" / "ticket.json").exists())
        self.assertFalse((legacy / "ECS-4_bunker").exists())
        self.assertFalse((legacy / "config.json").exists())
        self.assertTrue((self.source / project.LOCATOR_FILE).exists())

    def test_project_fields_and_provider_mappings_publish_through_commands(self) -> None:
        initialized = self.initialize()
        revision = projectconfig.revision(self.source)
        result = commands.dispatch(
            "configuration.field.put",
            {
                "base_revision": revision,
                "key": "organization.charging_code",
                "label": "Charging",
                "type": "string",
            },
            commands.CommandContext(self.source, transport="http"),
        )
        self.assertNotEqual(result["revision"], initialized.get("revision"))
        self.assertIn("organization.charging_code", {field["key"] for field in result["fields"]})

        mapped = commands.dispatch(
            "configuration.mapping.put",
            {
                "base_revision": result["revision"],
                "provider": "jira",
                "ours": "organization.charging_code",
                "theirs": "fields.customfield_10420",
            },
            commands.CommandContext(self.source, transport="cli"),
        )
        self.assertEqual(
            mapped["mappings"]["providers"]["jira"]["fields"]["organization.charging_code"],
            "fields.customfield_10420",
        )

    def test_empty_ui_enum_input_is_ignored_for_non_enum_fields(self) -> None:
        self.initialize()
        result = commands.dispatch(
            "configuration.field.put",
            {
                "base_revision": projectconfig.revision(self.source),
                "key": "organization.charging_code",
                "label": "Charging",
                "type": "string",
                "values": "",
            },
            commands.CommandContext(self.source, transport="http"),
        )
        field = next(value for value in result["fields"] if value["key"] == "organization.charging_code")
        self.assertNotIn("values", field)

    def test_global_field_change_seeds_new_projects_without_mutating_existing_project(self) -> None:
        self.initialize()
        commands.dispatch(
            "configuration.global.field.put",
            {
                "base_revision": projectconfig.revision(self.source),
                "key": "organization.charging_code",
                "label": "Charging",
                "type": "string",
            },
            commands.CommandContext(self.source, transport="http"),
        )
        self.assertNotIn(
            "organization.charging_code",
            {field["key"] for field in projectconfig.resolved_fields(self.source)},
        )

        second = Path(self.tmp.name) / "second-source"
        second.mkdir()
        run_git(second, "init", "-b", "main")
        run_git(second, "config", "user.name", "Tim")
        run_git(second, "config", "user.email", "tim@example.com")
        run_git(second, "commit", "--allow-empty", "-m", "initial")
        commands.dispatch(
            "project.initialize",
            {"project": "VOX", "bunker_repo": str(self.remote), "base_branch": "main"},
            commands.CommandContext(second),
        )
        self.assertIn(
            "organization.charging_code",
            {field["key"] for field in projectconfig.resolved_fields(second)},
        )

    def test_stale_configuration_revision_is_rejected(self) -> None:
        self.initialize()
        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "configuration.field.put",
                {"base_revision": "deadbeef", "key": "charging", "label": "Charging", "type": "string"},
                commands.CommandContext(self.source),
            )
        self.assertEqual(caught.exception.code, "config_stale")


if __name__ == "__main__":
    unittest.main()
