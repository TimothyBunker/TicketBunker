from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import api, autoagent, commands, layout, project, projectconfig, ticketcommands


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
        self.assertEqual(locator["base_branch"], "ecs")
        self.assertTrue((project.project_config_dir(self.source) / "project.json").exists())
        self.assertTrue((project.tickets_dir(self.source) / ".keep").exists())
        self.assertEqual(layout.bunkers_dir(self.source), project.tickets_dir(self.source))
        self.assertIn("/.tb/", (self.source / ".gitignore").read_text(encoding="utf-8"))
        self.assertTrue(run_git(self.remote, "show-ref", "--verify", "refs/heads/ticketbunker/config"))
        self.assertTrue(run_git(self.remote, "show-ref", "--verify", "refs/heads/ticketbunker/data"))
        mappings = projectconfig.state(self.source)["mappings"]["providers"]
        self.assertEqual(mappings["github"]["fields"]["title"], "title")
        self.assertEqual(mappings["gitlab"]["fields"]["assignee"], "assignees[].username")
        self.assertEqual(mappings["jira"]["fields"]["title"], "fields.summary")

    def test_init_requires_an_existing_remote_before_writing_local_project_state(self) -> None:
        missing = Path(self.tmp.name) / "not-created.git"

        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "project.initialize",
                {"project": "ECS", "bunker_repo": str(missing), "base_branch": "ecs"},
                commands.CommandContext(self.source),
            )

        self.assertEqual(caught.exception.code, "bunker_repo_unreachable")
        self.assertIn("never creates the repository", str(caught.exception))
        self.assertFalse((self.source / project.LOCATOR_FILE).exists())
        self.assertFalse((self.source / project.LOCAL_DIR).exists())
        self.assertFalse((self.source / ".gitignore").exists())

    def test_init_is_create_once_and_setup_is_repeatable(self) -> None:
        self.initialize()
        with self.assertRaises(commands.CommandRejected) as caught:
            self.initialize()
        self.assertEqual(caught.exception.code, "already_initialized")

        first = commands.dispatch("project.setup", {}, commands.CommandContext(self.source))
        second = commands.dispatch("project.setup", {}, commands.CommandContext(self.source))
        self.assertEqual(first["project"], "ECS")
        self.assertEqual(second["project"], "ECS")

    def test_connect_initializes_then_refreshes_from_the_same_command(self) -> None:
        initialized = commands.dispatch(
            "project.connect",
            {"project": "ECS", "bunker_repo": str(self.remote), "base_branch": "ecs"},
            commands.CommandContext(self.source),
        )
        connected = commands.dispatch(
            "project.connect",
            {},
            commands.CommandContext(self.source),
        )

        self.assertEqual(initialized["mode"], "initialized")
        self.assertEqual(connected["mode"], "connected")
        self.assertEqual(connected["project"], "ECS")
        self.assertTrue(autoagent.hook_status(self.source)["ready"])

    def test_connect_finds_locator_branch_from_a_fresh_default_branch_clone(self) -> None:
        source_remote = Path(self.tmp.name) / "source.git"
        subprocess.run(["git", "init", "--bare", str(source_remote)], check=True, capture_output=True)
        run_git(self.source, "branch", "main")
        run_git(self.source, "remote", "add", "origin", str(source_remote))
        run_git(self.source, "push", "origin", "main", "ecs")
        run_git(source_remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.initialize()
        run_git(self.source, "add", project.LOCATOR_FILE, ".gitignore")
        run_git(self.source, "commit", "-m", "Connect TicketBunker on ECS")
        run_git(self.source, "push", "origin", "ecs")
        run_git(self.source, "branch", "bunker/ECS-99-work")
        run_git(self.source, "push", "origin", "bunker/ECS-99-work")

        collaborator = Path(self.tmp.name) / "fresh-clone"
        subprocess.run(["git", "clone", str(source_remote), str(collaborator)], check=True, capture_output=True)
        run_git(collaborator, "config", "user.name", "Ada")
        run_git(collaborator, "config", "user.email", "ada@example.com")
        self.assertEqual(run_git(collaborator, "branch", "--show-current"), "main")
        self.assertFalse((collaborator / project.LOCATOR_FILE).exists())

        connected = commands.dispatch(
            "project.connect",
            {},
            commands.CommandContext(collaborator),
        )

        self.assertEqual(connected["mode"], "connected")
        self.assertEqual(connected["source_branch"], "ecs")
        self.assertEqual(run_git(collaborator, "branch", "--show-current"), "ecs")
        self.assertTrue((collaborator / project.LOCATOR_FILE).exists())
        self.assertTrue((project.data_checkout(collaborator) / ".git").exists())

    def test_init_retries_and_classifies_transient_dns_without_local_state(self) -> None:
        failure = "ssh: Could not resolve hostname github.com: Temporary failure in name resolution"
        with mock.patch.object(project, "git_status", return_value=(failure, 128)) as attempted:
            with mock.patch.object(project.time, "sleep"):
                with self.assertRaises(commands.CommandRejected) as caught:
                    commands.dispatch(
                        "project.connect",
                        {
                            "project": "ECS",
                            "bunker_repo": "git@github.com:example/tickets.git",
                            "base_branch": "ecs",
                        },
                        commands.CommandContext(self.source),
                    )

        self.assertEqual(caught.exception.code, "remote_dns_failed")
        self.assertIn("3 automatic attempts", str(caught.exception))
        self.assertIn("wsl --shutdown", str(caught.exception))
        self.assertIn("tb init", str(caught.exception))
        self.assertEqual(attempted.call_count, 3)
        self.assertFalse((self.source / project.LOCATOR_FILE).exists())
        self.assertFalse((self.source / project.LOCAL_DIR).exists())

    def test_setup_translates_companion_git_failure_into_actionable_rejection(self) -> None:
        self.initialize()
        missing = Path(self.tmp.name) / "moved-companion.git"
        run_git(project.config_checkout(self.source), "remote", "set-url", "origin", str(missing))

        with self.assertRaises(commands.CommandRejected) as caught:
            project.setup(self.source)

        self.assertEqual(caught.exception.code, "companion_refresh_failed")
        self.assertIn("tb init", str(caught.exception))

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

    def test_published_ticket_updates_committed_catalog_index(self) -> None:
        self.initialize()
        commands.dispatch(
            "ticket.create",
            {
                "ticket": {
                    "id": "ECS-4",
                    "key": "ECS-4",
                    "title": "Position system",
                    "description": "Add sparse-set positions.",
                    "type": "task",
                    "status": "todo",
                    "assignee": "Tim",
                    "labels": ["ecs", "core"],
                    "metadata": {"source_url": "https://example.invalid/issues/4"},
                }
            },
            commands.CommandContext(self.source),
        )

        shown = run_git(self.remote, "show", "ticketbunker/data:projects/ECS/tickets/index.json")
        indexed = json.loads(shown)

        self.assertEqual(indexed["project"], {"key": "ECS", "name": "ECS"})
        self.assertEqual(len(indexed["tickets"]), 1)
        entry = indexed["tickets"][0]
        self.assertEqual(entry["id"], "ECS-4")
        self.assertEqual(entry["reference"], "projects/ECS/tickets/ECS-4")
        self.assertEqual(entry["source_url"], "https://example.invalid/issues/4")
        self.assertEqual(len(entry["ticket_digest"]), 64)

    def test_new_setup_lists_index_then_materializes_only_opened_ticket(self) -> None:
        self.initialize()
        for number in (4, 5):
            commands.dispatch(
                "ticket.create",
                {
                    "ticket": {
                        "id": f"ECS-{number}",
                        "key": f"ECS-{number}",
                        "title": f"Ticket {number}",
                        "type": "task",
                        "status": "todo",
                    }
                },
                commands.CommandContext(self.source),
            )

        collaborator = Path(self.tmp.name) / "collaborator"
        collaborator.mkdir()
        run_git(collaborator, "init", "-b", "ecs")
        run_git(collaborator, "config", "user.name", "Ada")
        run_git(collaborator, "config", "user.email", "ada@example.com")
        (collaborator / project.LOCATOR_FILE).write_text(
            (self.source / project.LOCATOR_FILE).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        project.setup(collaborator)

        scope = project.tickets_dir(collaborator)
        self.assertTrue(project.sparse_data_enabled(collaborator))
        self.assertTrue(layout.ticket_index(scope).exists())
        self.assertFalse((scope / "ECS-4").exists())
        self.assertFalse((scope / "ECS-5").exists())
        catalog = api.TicketCatalog(collaborator)
        self.assertEqual([ticket.id for ticket in catalog.list_tickets()], ["ECS-4", "ECS-5"])

        opened = catalog.get_ticket("ECS", "ECS-5")

        self.assertEqual(opened.id, "ECS-5")
        self.assertTrue((scope / "ECS-5" / "ticket.json").exists())
        self.assertFalse((scope / "ECS-4").exists())

    def test_concurrent_ticket_publications_keep_both_index_references(self) -> None:
        self.initialize()

        collaborators = []
        for name in ("Ada", "Lin"):
            source = Path(self.tmp.name) / name.casefold()
            source.mkdir()
            run_git(source, "init", "-b", "ecs")
            run_git(source, "config", "user.name", name)
            run_git(source, "config", "user.email", f"{name.casefold()}@example.com")
            (source / project.LOCATOR_FILE).write_text(
                (self.source / project.LOCATOR_FILE).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            project.setup(source)
            collaborators.append(source)

        for source, number in zip(collaborators, (4, 5), strict=True):
            commands.dispatch(
                "ticket.create",
                {
                    "ticket": {
                        "id": f"ECS-{number}",
                        "key": f"ECS-{number}",
                        "title": f"Ticket {number}",
                        "type": "task",
                        "status": "todo",
                    }
                },
                commands.CommandContext(source),
            )

        shown = run_git(self.remote, "show", "ticketbunker/data:projects/ECS/tickets/index.json")
        self.assertEqual(
            [entry["id"] for entry in json.loads(shown)["tickets"]],
            ["ECS-4", "ECS-5"],
        )

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

    def test_mapping_matrix_replaces_all_rows_atomically(self) -> None:
        self.initialize()
        replaced = commands.dispatch(
            "configuration.mapping.replace",
            {
                "base_revision": projectconfig.revision(self.source),
                "provider": "github",
                "fields": [
                    {"ours": "title", "theirs": "issue.title"},
                    {"ours": "labels", "theirs": "issue.labels[].name"},
                ],
            },
            commands.CommandContext(self.source, transport="http"),
        )
        self.assertEqual(
            replaced["mappings"]["providers"]["github"]["fields"],
            {"title": "issue.title", "labels": "issue.labels[].name"},
        )

        removed = commands.dispatch(
            "configuration.mapping.replace",
            {
                "base_revision": replaced["revision"],
                "provider": "github",
                "fields": [{"ours": "title", "theirs": "issue.title"}],
            },
            commands.CommandContext(self.source, transport="cli"),
        )
        self.assertEqual(
            removed["mappings"]["providers"]["github"]["fields"],
            {"title": "issue.title"},
        )

    def test_custom_integrations_use_the_same_mapping_commands(self) -> None:
        self.initialize()
        added = commands.dispatch(
            "configuration.integration.put",
            {
                "base_revision": projectconfig.revision(self.source),
                "name": "Rally Dev",
            },
            commands.CommandContext(self.source, transport="http"),
        )
        self.assertEqual(added["mappings"]["providers"]["rally-dev"]["label"], "Rally Dev")

        mapped = commands.dispatch(
            "configuration.mapping.replace",
            {
                "base_revision": added["revision"],
                "provider": "rally-dev",
                "fields": {"title": "work_item.name", "status": "work_item.state"},
            },
            commands.CommandContext(self.source, transport="cli"),
        )
        self.assertEqual(
            mapped["mappings"]["providers"]["rally-dev"]["fields"],
            {"title": "work_item.name", "status": "work_item.state"},
        )

        removed = commands.dispatch(
            "configuration.integration.remove",
            {
                "base_revision": mapped["revision"],
                "provider": "rally-dev",
            },
            commands.CommandContext(self.source),
        )
        self.assertNotIn("rally-dev", removed["mappings"]["providers"])

    def test_mapping_matrix_rejects_duplicate_ticketbunker_fields(self) -> None:
        self.initialize()
        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "configuration.mapping.replace",
                {
                    "base_revision": projectconfig.revision(self.source),
                    "provider": "jira",
                    "fields": [
                        {"ours": "title", "theirs": "fields.summary"},
                        {"ours": "title", "theirs": "fields.customfield_10000"},
                    ],
                },
                commands.CommandContext(self.source),
            )
        self.assertEqual(caught.exception.code, "mapping_field_duplicate")

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
