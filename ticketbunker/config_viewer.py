"""Local configuration editor backed by TicketBunker's command registry.

The browser is only a transport. Every mutation posts a command envelope to
the same handlers used by the CLI, and canonical configuration changes are
published through the companion repository's protected config branch.
"""

from __future__ import annotations

import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import commands, doctor, project, projectconfig, types


_PAGES = ("identity", "repository", "fields", "integrations")
_ALLOWED_COMMANDS = {
    "project.connect",
    "identity.update",
    "configuration.field.put",
    "configuration.field.remove",
    "configuration.global.field.put",
    "configuration.global.field.remove",
    "configuration.integration.put",
    "configuration.integration.remove",
    "configuration.mapping.put",
    "configuration.mapping.replace",
    "credentials.update",
}


_STYLE = """
:root {
  color-scheme: light;
  --bg: #fff;
  --panel: #f7f8fa;
  --fg: #172b4d;
  --body: #344563;
  --muted: #6b778c;
  --line: #dfe1e6;
  --line-strong: #c1c7d0;
  --accent: #0c66e4;
  --accent-soft: #e9f2ff;
  --success: #216e4e;
  --success-soft: #dcfff1;
  --danger: #ae2e24;
  --danger-soft: #ffeceb;
  --radius: 5px;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0;
  color: var(--body);
  background: var(--bg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  text-rendering: optimizeLegibility;
}
button, input, select { font: inherit; }
button, input, select { min-height: 44px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible {
  outline: 3px solid rgba(12, 102, 228, .28);
  outline-offset: 2px;
}
.shell { min-height: 100vh; display: grid; grid-template-columns: 226px minmax(0, 1fr); }
.side { padding: 28px 22px; border-right: 1px solid var(--line); }
.brand { margin: 0 10px 27px; color: var(--fg); font-weight: 700; letter-spacing: -.01em; }
.project-mark { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; font-weight: 500; }
.nav { display: grid; gap: 3px; }
.nav a { min-height: 40px; padding: 9px 10px; border-radius: var(--radius); color: var(--body); font-weight: 600; }
.nav a:hover { background: var(--panel); text-decoration: none; }
.nav a[aria-current="page"] { color: #0055cc; background: var(--accent-soft); }
.content { width: 100%; max-width: 920px; padding: 53px 64px 80px; }
.page-head { padding-bottom: 24px; border-bottom: 1px solid var(--line); }
h1, h2, h3 { color: var(--fg); }
h1 { margin: 0 0 5px; font-size: 26px; line-height: 1.25; letter-spacing: -.018em; }
h2 { margin: 0; font-size: 16px; line-height: 1.35; }
h3 { margin: 0; font-size: 14px; line-height: 1.35; }
p { margin: 0; }
.lede { color: var(--muted); }
.section { padding: 27px 0; border-bottom: 1px solid var(--line); }
.section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 17px; }
.section-head .meta { color: var(--muted); font-size: 12px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 20px; }
.span-2 { grid-column: 1 / -1; }
label { display: grid; gap: 6px; color: var(--fg); font-size: 12px; font-weight: 650; }
input, select {
  width: 100%; padding: 9px 11px; border: 1px solid var(--line-strong); border-radius: var(--radius);
  color: var(--fg); background: var(--bg);
}
input::placeholder { color: #8993a4; }
input:disabled, select:disabled { color: var(--muted); background: var(--panel); cursor: not-allowed; }
.check { display: flex; align-items: center; gap: 9px; min-height: 44px; }
.check input { width: 18px; min-height: 18px; height: 18px; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 20px; }
button {
  padding: 9px 15px; border: 1px solid transparent; border-radius: var(--radius);
  color: #fff; background: var(--accent); font-weight: 650; cursor: pointer;
}
button:hover { background: #0055cc; }
button.secondary { color: var(--body); background: var(--bg); border-color: var(--line-strong); }
button.secondary:hover { background: var(--panel); }
button.danger { color: var(--danger); background: var(--bg); border-color: var(--line-strong); }
button.danger:hover { background: var(--danger-soft); border-color: var(--danger); }
button:disabled { opacity: .55; cursor: wait; }
.status { min-height: 21px; color: var(--muted); font-size: 12px; }
.status.error { color: var(--danger); }
.status.success { color: var(--success); }
.readonly { display: grid; grid-template-columns: 155px minmax(0, 1fr); gap: 10px 18px; margin: 0; }
.readonly dt { color: var(--muted); }
.readonly dd { min-width: 0; margin: 0; color: var(--fg); overflow-wrap: anywhere; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .93em; }
.badge { display: inline-flex; padding: 2px 7px; border-radius: 3px; color: var(--body); background: var(--panel); font-size: 11px; font-weight: 650; }
.badge.good { color: var(--success); background: var(--success-soft); }
.field-list { border-top: 1px solid var(--line); }
.field-row { display: grid; grid-template-columns: minmax(0, 1fr) 120px 92px auto; gap: 14px; align-items: center; padding: 13px 2px; border-bottom: 1px solid var(--line); }
.field-name { min-width: 0; color: var(--fg); font-weight: 650; }
.field-key { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.field-type { color: var(--body); }
.tabs { display: flex; gap: 18px; margin: 0 0 24px; border-bottom: 1px solid var(--line); }
.tabs button { min-height: 44px; padding: 8px 1px; border: 0; border-bottom: 2px solid transparent; border-radius: 0; color: var(--muted); background: transparent; }
.tabs button:hover { color: var(--fg); background: transparent; }
.tabs button[aria-selected="true"] { color: var(--accent); border-color: var(--accent); }
.provider[hidden], .field-scope[hidden] { display: none; }
.mapping-matrix { border-top: 1px solid var(--line); }
.mapping-head, .mapping-entry {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 28px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: end;
}
.mapping-head { padding: 8px 2px; color: var(--muted); font-size: 12px; }
.mapping-entry { padding: 10px 2px; border-top: 1px solid var(--line); }
.mapping-entry:first-child { border-top: 0; }
.mapping-entry label { min-width: 0; }
.mapping-entry label > span { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.mapping-entry button { min-height: 44px; }
.mapping-arrow { color: var(--muted); text-align: center; }
.custom-integration { max-width: 600px; }
.notice { margin: 20px 0 0; padding: 12px 14px; border-left: 3px solid var(--accent); background: var(--accent-soft); color: var(--fg); }
.setup-steps, .readiness { margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--line); }
.setup-steps li, .readiness li { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; padding: 12px 2px; border-bottom: 1px solid var(--line); }
.step-number, .readiness-mark { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; color: var(--muted); background: var(--panel); font-size: 11px; font-weight: 700; }
.readiness-mark.pass { color: var(--success); background: var(--success-soft); }
.readiness-mark.fail { color: var(--danger); background: var(--danger-soft); }
.readiness-mark.warn { color: #7f5f01; background: #fff7d6; }
.readiness-label { display: block; color: var(--fg); font-weight: 650; }
.readiness-summary { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }
.readiness-action { display: block; margin-top: 5px; color: var(--fg); font-size: 12px; }
.empty { color: var(--muted); }
@media (max-width: 760px) {
  .shell { display: block; }
  .side { padding: 17px 18px 0; border-right: 0; border-bottom: 1px solid var(--line); }
  .brand { margin: 0 0 13px; }
  .project-mark { display: inline; margin-left: 7px; }
  .nav { display: flex; gap: 5px; overflow-x: auto; }
  .nav a { flex: 0 0 auto; }
  .content { padding: 31px 18px 60px; }
  h1 { font-size: 23px; }
  .form-grid { grid-template-columns: 1fr; }
  .span-2 { grid-column: auto; }
  .readonly { grid-template-columns: 1fr; gap: 2px; }
  .readonly dd { margin-bottom: 11px; }
  .field-row { grid-template-columns: minmax(0, 1fr) auto; }
  .field-type, .field-source { grid-row: 2; font-size: 12px; }
  .field-row button { grid-column: 2; grid-row: 1 / span 2; }
  .tabs { overflow-x: auto; }
  .tabs button { flex: 0 0 auto; }
  .mapping-head { display: none; }
  .mapping-entry { grid-template-columns: minmax(0, 1fr) auto; align-items: end; }
  .mapping-entry label { grid-column: 1; }
  .mapping-entry label > span { position: static; width: auto; height: auto; margin: 0; overflow: visible; clip: auto; white-space: normal; }
  .mapping-entry .mapping-arrow { display: none; }
  .mapping-entry button { grid-column: 2; grid-row: 1 / span 2; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _initial_state(root: Path) -> dict[str, Any]:
    locator = project.load_locator(root)
    if locator is not None:
        return {
            "initialized": True,
            **projectconfig.state(root),
            "diagnostics": doctor.report(root, check_remote=False),
        }
    fields = []
    for field_set in project.STANDARD_FIELD_SETS.values():
        fields.extend({**field, "source": field_set["id"]} for field in field_set["fields"])
    return {
        "initialized": False,
        "project": {},
        "locator": {},
        "revision": "",
        "identity": projectconfig.identity_state(root),
        "credentials_file": str(projectconfig.credential_file()),
        "credentials": projectconfig.credential_state(),
        "fields": fields,
        "global_fields": fields,
        "mappings": {"providers": {}},
        "diagnostics": doctor.report(root, check_remote=False),
    }


def _current_branch(root: Path) -> str:
    from .adapters.gitrun import git
    from .ledger import Ledger

    return git(root, ["branch", "--show-current"], Ledger(root / ".git" / "ticketbunker-config.jsonl"), "config_read", check=False).strip()


def _identity_page(state: dict[str, Any]) -> str:
    identity = state["identity"]
    return f"""
    <header class="page-head"><h1>Identity</h1><p class="lede">Used for commits and branch names.</p></header>
    <section class="section">
      <form data-command="identity.update">
        <div class="form-grid">
          <label>Name<input name="name" autocomplete="name" value="{_e(identity.get('name', ''))}" required></label>
          <label>Email<input name="email" type="email" autocomplete="email" value="{_e(identity.get('email', ''))}" required></label>
          <label>Branch identifier<input name="branch_user" value="{_e(identity.get('branch_user', ''))}" required></label>
        </div>
        <div class="actions"><button type="submit">Save identity</button><output class="status" aria-live="polite"></output></div>
      </form>
    </section>"""


def _repository_page(state: dict[str, Any], root: Path) -> str:
    if state["initialized"]:
        locator = state["locator"]
        details = state["project"]
        diagnostic_rows = []
        for value in state["diagnostics"]["checks"]:
            if value["status"] == "info":
                continue
            mark = {"pass": "✓", "warn": "!", "fail": "×"}[value["status"]]
            action = (
                f'<code class="readiness-action">{_e(value["action"])}</code>'
                if value.get("action")
                else ""
            )
            diagnostic_rows.append(
                f'<li><span class="readiness-mark {_e(value["status"])}">{mark}</span><span>'
                f'<span class="readiness-label">{_e(value["label"])}</span>'
                f'<span class="readiness-summary">{_e(value["summary"])}</span>{action}</span></li>'
            )
        readiness = (
            "Needs connection"
            if not state["diagnostics"]["ready"]
            else "Ready with notes"
            if state["diagnostics"]["warnings"]
            else "Ready"
        )
        return f"""
        <header class="page-head"><h1>Repository</h1><p class="lede">Canonical TicketBunker storage for this project.</p></header>
        <section class="section">
          <div class="section-head"><h2>{readiness}</h2><span class="meta">Run <span class="mono">tb doctor</span> for remote access</span></div>
          <ul class="readiness">{''.join(diagnostic_rows)}</ul>
        </section>
        <section class="section">
          <dl class="readonly">
            <dt>Project</dt><dd>{_e(details.get('key'))} · {_e(details.get('name'))}</dd>
            <dt>Companion repository</dt><dd class="mono">{_e(locator.get('bunker_repository'))}</dd>
            <dt>Configuration branch</dt><dd class="mono">{_e(locator.get('config_branch'))}</dd>
            <dt>Data branch</dt><dd class="mono">{_e(locator.get('data_branch'))}</dd>
            <dt>Source base branch</dt><dd class="mono">{_e(details.get('base_branch'))}</dd>
            <dt>Configuration revision</dt><dd class="mono">{_e(state.get('revision'))}</dd>
          </dl>
        </section>"""
    branch = _current_branch(root)
    return f"""
    <header class="page-head"><h1>Connect TicketBunker</h1><p class="lede">Create a Git repository wherever you want, then connect it here.</p></header>
    <section class="section">
      <ol class="setup-steps">
        <li><span class="step-number">1</span><span><span class="readiness-label">Create an empty companion repository</span><span class="readiness-summary">Use any Git host or local bare repository.</span></span></li>
        <li><span class="step-number">2</span><span><span class="readiness-label">Connect it below</span><span class="readiness-summary">TicketBunker publishes only its configuration and data branches.</span></span></li>
        <li><span class="step-number">3</span><span><span class="readiness-label">Commit the locator</span><span class="readiness-summary">Then run <span class="mono">tb doctor</span>.</span></span></li>
      </ol>
    </section>
    <section class="section">
      <form data-command="project.connect" data-reload="repository">
        <div class="form-grid">
          <label>Project key<input name="project" placeholder="ECS" autocapitalize="characters" required></label>
          <label>Project name<input name="project_name" placeholder="Entity Component System"></label>
          <label class="span-2">Repository URL<input name="bunker_repo" placeholder="git@github.com:team/project-ticketbunker.git" required></label>
          <label>Configuration branch<input name="config_branch" value="{_e(project.DEFAULT_CONFIG_BRANCH)}" required></label>
          <label>Data branch<input name="data_branch" value="{_e(project.DEFAULT_DATA_BRANCH)}" required></label>
          <label>Source base branch<input name="base_branch" value="{_e(branch)}" required></label>
        </div>
        <div class="actions"><button type="submit">Connect repository</button><output class="status" aria-live="polite"></output></div>
      </form>
      <p class="notice">TicketBunker never creates this repository. It connects to the URL above, then publishes its configuration and data branches.</p>
    </section>"""


def _field_row(field: dict[str, Any], remove_command: str | None) -> str:
    source = str(field.get("source") or "project")
    remove = ""
    if remove_command:
        remove = (
            f'<button class="danger" type="button" data-remove-command="{_e(remove_command)}" '
            f'data-field-key="{_e(field.get("key"))}">Remove</button>'
        )
    return f"""
    <div class="field-row">
      <div class="field-name">{_e(field.get('label'))}<span class="field-key mono">{_e(field.get('key'))}</span></div>
      <span class="field-type">{_e(field.get('type'))}</span>
      <span class="field-source badge">{_e(source)}</span>
      {remove}
    </div>"""


def _field_form(state: dict[str, Any], command: str, scope: str) -> str:
    options = "".join(f'<option value="{_e(value)}">{_e(value)}</option>' for value in types.FIELD_TYPES)
    return f"""
    <section class="section">
      <div class="section-head"><h2>Add or modify a field</h2></div>
      <form data-command="{_e(command)}">
        <input type="hidden" name="base_revision" value="{_e(state['revision'])}">
        <div class="form-grid">
          <label>{_e(scope)} field key<input name="key" placeholder="organization.charging_code" required></label>
          <label>Label<input name="label" placeholder="Charging" required></label>
          <label>Type<select name="type" data-field-type>{options}</select></label>
          <label>Enum values<input name="values" data-enum-values placeholder="one, two, three" disabled></label>
          <label class="check"><input name="required" type="checkbox"> Required</label>
        </div>
        <div class="actions"><button type="submit">Publish field</button><output class="status" aria-live="polite"></output></div>
      </form>
    </section>"""


def _fields_page(state: dict[str, Any]) -> str:
    if not state["initialized"]:
        rows = "".join(_field_row(field, None) for field in state["fields"])
        return f"""
        <header class="page-head"><h1>Ticket fields</h1><p class="lede">Compose the fields tickets can use.</p></header>
        <section class="section">
          <div class="section-head"><h2>Default fields</h2><span class="meta">{len(state['fields'])} fields</span></div>
          <div class="field-list">{rows}</div>
          <p class="notice">Initialize the project to change ticket fields.</p>
        </section>"""
    project_rows = "".join(
        _field_row(field, "configuration.field.remove") for field in state["fields"]
    )
    global_rows = "".join(
        _field_row(field, "configuration.global.field.remove") for field in state["global_fields"]
    )
    key = state.get("project", {}).get("key") or "Project"
    return f"""
    <header class="page-head"><h1>Ticket fields</h1><p class="lede">Compose the fields tickets can use.</p></header>
    <div class="tabs" role="tablist">
      <button type="button" role="tab" data-field-tab="project" aria-selected="true">{_e(key)}</button>
      <button type="button" role="tab" data-field-tab="global" aria-selected="false">Global defaults</button>
    </div>
    <div class="field-scope" data-field-scope="project">
      <section class="section">
        <div class="section-head"><h2>Project composition</h2><span class="meta">{len(state['fields'])} fields</span></div>
        <div class="field-list">{project_rows}</div>
      </section>
      {_field_form(state, 'configuration.field.put', 'Project')}
    </div>
    <div class="field-scope" data-field-scope="global" hidden>
      <section class="section">
        <div class="section-head"><h2>New-project defaults</h2><span class="meta">{len(state['global_fields'])} fields</span></div>
        <div class="field-list">{global_rows}</div>
      </section>
      {_field_form(state, 'configuration.global.field.put', 'Global')}
    </div>"""


def _integration_settings(provider: str, state: dict[str, Any]) -> dict[str, Any]:
    stored = state.get("mappings", {}).get("providers", {}).get(provider, {})
    stored = stored if isinstance(stored, dict) else {}
    preset = project.STANDARD_INTEGRATIONS.get(provider, {})
    settings = {**preset, **stored}
    if preset and not stored.get("template_applied"):
        fields = dict(preset.get("fields", {}))
        fields.update(stored.get("fields", {}))
        settings["fields"] = fields
    else:
        settings["fields"] = dict(stored.get("fields", preset.get("fields", {})))
    return settings


def _field_options(fields: list[dict[str, Any]], selected: str = "") -> str:
    return "".join(
        f'<option value="{_e(field["key"])}"{' selected' if field['key'] == selected else ''}>'
        f'{_e(field["label"])} · {_e(field["key"])}</option>'
        for field in fields
    )


def _mapping_entry(fields: list[dict[str, Any]], ours: str = "", theirs: str = "") -> str:
    return f"""
      <div class="mapping-entry" data-mapping-entry>
        <label><span>TicketBunker field</span><select data-mapping-ours required>{_field_options(fields, ours)}</select></label>
        <span class="mapping-arrow" aria-hidden="true">↔</span>
        <label><span>Integration field</span><input data-mapping-theirs value="{_e(theirs)}" placeholder="payload.field.path" required></label>
        <button class="secondary" type="button" data-remove-mapping>Remove</button>
      </div>"""


def _provider_panel(provider: str, state: dict[str, Any], selected: bool) -> str:
    settings = _integration_settings(provider, state)
    label = str(settings.get("label") or provider)
    configured = bool(state["credentials"].get(provider))
    badge = '<span class="badge good">Token saved locally</span>' if configured else '<span class="badge">No local token</span>'
    available = {field["key"] for field in state["fields"]}
    mapping_fields = {
        ours: theirs
        for ours, theirs in settings.get("fields", {}).items()
        if ours in available
    }
    mapping_rows = "".join(
        _mapping_entry(state["fields"], ours, theirs)
        for ours, theirs in mapping_fields.items()
    )
    first_field = str(state["fields"][0]["key"]) if state["fields"] else ""
    mapping_form = ""
    if state["initialized"]:
        mapping_form = f"""
        <form data-command="configuration.mapping.replace" data-mapping-form>
          <input type="hidden" name="base_revision" value="{_e(state['revision'])}">
          <input type="hidden" name="provider" value="{_e(provider)}">
          <div class="mapping-matrix">
            <div class="mapping-head"><span>TicketBunker field</span><span></span><span>{_e(label)} field</span><span></span></div>
            <div data-mapping-rows>{mapping_rows}</div>
          </div>
          <template data-mapping-template>{_mapping_entry(state['fields'], first_field)}</template>
          <div class="actions">
            <button class="secondary" type="button" data-add-mapping{' disabled' if not state['fields'] else ''}>Add row</button>
            <button type="submit">Publish mappings</button>
            <output class="status" aria-live="polite"></output>
          </div>
        </form>"""
    else:
        readonly_rows = "".join(
            f'<div class="mapping-entry"><span class="mono">{_e(ours)}</span><span class="mapping-arrow">↔</span><span class="mono">{_e(theirs)}</span><span></span></div>'
            for ours, theirs in mapping_fields.items()
        )
        mapping_form = (
            f'<div class="mapping-matrix">{readonly_rows}</div>'
            '<p class="notice">Connect a repository to publish field mappings.</p>'
        )
    custom_remove = ""
    if provider not in project.STANDARD_INTEGRATIONS and state["initialized"]:
        custom_remove = (
            f'<button class="danger" type="button" data-remove-integration="{_e(provider)}" '
            f'data-base-revision="{_e(state["revision"])}">Remove integration</button>'
        )
    return f"""
    <div class="provider" id="integration-{_e(provider)}" role="tabpanel" aria-labelledby="integration-tab-{_e(provider)}" data-provider="{_e(provider)}"{' hidden' if not selected else ''}>
      <section class="section">
        <div class="section-head"><h2>Access token</h2>{badge}</div>
        <form data-command="credentials.update">
          <input type="hidden" name="provider" value="{_e(provider)}">
          <div class="form-grid"><label class="span-2">{_e(label)} token<input name="token" type="password" autocomplete="off" required></label></div>
          <div class="actions"><button type="submit">Save locally</button><output class="status" aria-live="polite"></output></div>
        </form>
      </section>
      <section class="section">
        <div class="section-head"><h2>Field mappings</h2><span class="meta">{_e(settings.get('template', 'custom'))}</span></div>
        {mapping_form}
        {custom_remove}
      </section>
    </div>"""


def _integrations_page(state: dict[str, Any]) -> str:
    configured = state.get("mappings", {}).get("providers", {})
    custom = sorted(provider for provider in configured if provider not in project.STANDARD_INTEGRATIONS)
    providers = [*project.STANDARD_INTEGRATIONS, *custom]
    tabs = "".join(
        f'<button id="integration-tab-{_e(provider)}" type="button" role="tab" data-provider-tab="{_e(provider)}" '
        f'aria-controls="integration-{_e(provider)}" aria-selected="{"true" if index == 0 else "false"}"'
        f'{"" if index == 0 else " tabindex=\"-1\""}>{_e(_integration_settings(provider, state).get("label") or provider)}</button>'
        for index, provider in enumerate(providers)
    )
    panels = "".join(
        _provider_panel(provider, state, index == 0)
        for index, provider in enumerate(providers)
    )
    add_integration = '<p class="notice">Connect a repository to add integrations.</p>'
    if state["initialized"]:
        add_integration = f"""
        <section class="section custom-integration">
          <h2>Add integration</h2>
          <form data-command="configuration.integration.put">
            <input type="hidden" name="base_revision" value="{_e(state['revision'])}">
            <div class="form-grid"><label class="span-2">Integration name<input name="name" placeholder="Linear" required maxlength="80"></label></div>
            <div class="actions"><button type="submit">Add integration</button><output class="status" aria-live="polite"></output></div>
          </form>
        </section>"""
    return f"""
    <header class="page-head"><h1>Integrations</h1><p class="lede">Credentials and field mappings.</p></header>
    <div class="tabs" role="tablist" aria-label="Integrations">{tabs}</div>
    {panels}
    {add_integration}"""


def _render(root: Path, page: str, token: str) -> bytes:
    state = _initial_state(root)
    pages = {
        "identity": _identity_page,
        "repository": lambda value: _repository_page(value, root),
        "fields": _fields_page,
        "integrations": _integrations_page,
    }
    nav = "".join(
        f'<a href="/{name}"' + (' aria-current="page"' if name == page else '') + f'>{name.title()}</a>'
        for name in _PAGES
    )
    project_name = state.get("project", {}).get("key") or "Not initialized"
    script_state = json.dumps({"token": token}, separators=(",", ":")).replace("<", "\\u003c")
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><link rel="icon" href="data:,">
<title>{_e(page.title())} · TicketBunker</title><style>{_STYLE}</style></head>
<body><div class="shell"><aside class="side"><div class="brand">TicketBunker<span class="project-mark">{_e(project_name)}</span></div>
<nav class="nav" aria-label="Configuration">{nav}</nav></aside>
<main class="content">{pages[page](state)}</main></div>
<script>window.TICKETBUNKER={script_state};</script><script>{_SCRIPT}</script></body></html>"""
    return body.encode("utf-8")


_SCRIPT = r"""
function formPayload(form) {
  const data = Object.fromEntries(new FormData(form));
  form.querySelectorAll('input[type="checkbox"]').forEach(input => data[input.name] = input.checked);
  return data;
}
async function send(command, payload, status) {
  status.className = 'status'; status.textContent = 'Working…';
  const buttons = [...document.querySelectorAll('button')]; buttons.forEach(button => button.disabled = true);
  try {
    const response = await fetch('/api/commands', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-TicketBunker-Token': window.TICKETBUNKER.token},
      body: JSON.stringify({command, payload})
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || 'Command failed.');
    status.className = 'status success'; status.textContent = 'Saved.';
    window.setTimeout(() => location.reload(), 220);
  } catch (error) {
    status.className = 'status error'; status.textContent = error.message;
    buttons.forEach(button => button.disabled = false);
  }
}
document.querySelectorAll('form[data-command]:not([data-mapping-form])').forEach(form => form.addEventListener('submit', event => {
  event.preventDefault(); send(form.dataset.command, formPayload(form), form.querySelector('.status'));
}));
document.querySelectorAll('form[data-mapping-form]').forEach(form => form.addEventListener('submit', event => {
  event.preventDefault();
  const fields = [...form.querySelectorAll('[data-mapping-entry]')].map(row => ({
    ours: row.querySelector('[data-mapping-ours]').value,
    theirs: row.querySelector('[data-mapping-theirs]').value
  }));
  send(form.dataset.command, {
    provider: form.elements.provider.value,
    base_revision: form.elements.base_revision.value,
    fields
  }, form.querySelector('.status'));
}));
document.querySelectorAll('[data-add-mapping]').forEach(button => button.addEventListener('click', () => {
  const form = button.closest('form');
  const template = form.querySelector('[data-mapping-template]');
  const row = template.content.firstElementChild.cloneNode(true);
  const used = new Set([...form.querySelectorAll('[data-mapping-ours]')].map(select => select.value));
  const select = row.querySelector('[data-mapping-ours]');
  const available = [...select.options].find(option => !used.has(option.value));
  if (!available) {
    const status = form.querySelector('.status');
    status.className = 'status error'; status.textContent = 'Every TicketBunker field already has a row.';
    return;
  }
  select.value = available.value;
  form.querySelector('[data-mapping-rows]').append(row);
  row.querySelector('[data-mapping-theirs]').focus();
}));
document.addEventListener('click', event => {
  const button = event.target.closest('[data-remove-mapping]');
  if (button) button.closest('[data-mapping-entry]').remove();
});
document.querySelectorAll('[data-remove-command]').forEach(button => button.addEventListener('click', () => {
  if (!button.dataset.confirmed) {
    button.dataset.confirmed = 'true'; button.textContent = 'Confirm remove';
    window.setTimeout(() => { delete button.dataset.confirmed; button.textContent = 'Remove'; }, 4000);
    return;
  }
  const status = document.createElement('output'); status.className = 'status'; button.parentElement.after(status);
  const revision = button.closest('[data-field-scope]')?.querySelector('[name="base_revision"]')?.value || '';
  send(button.dataset.removeCommand, {key: button.dataset.fieldKey, base_revision: revision}, status);
}));
document.querySelectorAll('[data-remove-integration]').forEach(button => button.addEventListener('click', () => {
  if (!button.dataset.confirmed) {
    button.dataset.confirmed = 'true'; button.textContent = 'Confirm removal';
    window.setTimeout(() => { delete button.dataset.confirmed; button.textContent = 'Remove integration'; }, 4000);
    return;
  }
  const status = document.createElement('output'); status.className = 'status'; button.after(status);
  send('configuration.integration.remove', {
    provider: button.dataset.removeIntegration,
    base_revision: button.dataset.baseRevision
  }, status);
}));
const providerTabs = [...document.querySelectorAll('[data-provider-tab]')];
function activateProvider(tab, moveFocus = false) {
  providerTabs.forEach(value => {
    const selected = value === tab;
    value.setAttribute('aria-selected', String(selected));
    value.tabIndex = selected ? 0 : -1;
  });
  document.querySelectorAll('[data-provider]').forEach(panel => panel.hidden = panel.dataset.provider !== tab.dataset.providerTab);
  if (moveFocus) tab.focus();
}
providerTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateProvider(tab));
  tab.addEventListener('keydown', event => {
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % providerTabs.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + providerTabs.length) % providerTabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = providerTabs.length - 1;
    if (next !== null) { event.preventDefault(); activateProvider(providerTabs[next], true); }
  });
});
document.querySelectorAll('[data-field-tab]').forEach(tab => tab.addEventListener('click', () => {
  document.querySelectorAll('[data-field-tab]').forEach(value => value.setAttribute('aria-selected', value === tab ? 'true' : 'false'));
  document.querySelectorAll('[data-field-scope]').forEach(panel => panel.hidden = panel.dataset.fieldScope !== tab.dataset.fieldTab);
}));
document.querySelectorAll('[data-field-type]').forEach(select => {
  const values = select.form.querySelector('[data-enum-values]');
  const sync = () => { values.disabled = select.value !== 'enum'; };
  select.addEventListener('change', sync); sync();
});
"""


class _Handler(BaseHTTPRequestHandler):
    root: Path
    token: str

    def do_GET(self) -> None:  # noqa: N802
        page = self.path.split("?", 1)[0].strip("/") or "identity"
        if page not in _PAGES:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        try:
            body = _render(self.root, page, self.token)
        except commands.CommandRejected as exc:
            body = (
                "<!doctype html><meta charset=utf-8><title>TicketBunker configuration error</title>"
                f"<h1>Configuration unavailable</h1><p>{_e(exc.message)}</p>"
            ).encode("utf-8")
            self._send(409, body)
            return
        self._send(200, body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/commands":
            self._json(404, {"ok": False, "code": "not_found", "message": "Not found."})
            return
        if self.headers.get("X-TicketBunker-Token") != self.token:
            self._json(403, {"ok": False, "code": "forbidden", "message": "Reload the editor and try again."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            self._json(400, {"ok": False, "code": "body_invalid", "message": "Invalid request body."})
            return
        try:
            envelope = json.loads(self.rfile.read(length))
            command = envelope.get("command")
            payload = envelope.get("payload")
            if command not in _ALLOWED_COMMANDS or not isinstance(payload, dict):
                raise commands.CommandRejected("command_invalid", "That configuration command is not available.")
            result = commands.dispatch(command, payload, commands.CommandContext(self.root, transport="http"))
        except commands.CommandRejected as exc:
            self._json(409, exc.as_dict())
            return
        except (ValueError, AttributeError):
            self._json(400, {"ok": False, "code": "json_invalid", "message": "Request must be one JSON object."})
            return
        self._json(200, result)

    def _json(self, status: int, value: dict[str, Any]) -> None:
        self._send(status, json.dumps(value).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, TimeoutError):
            pass

    def log_message(self, *_args: object) -> None:
        pass


def serve(root: Path, port: int = 8378) -> ThreadingHTTPServer:
    """Bind the configuration transport to localhost only."""
    handler = type(
        "BoundConfigHandler",
        (_Handler,),
        {"root": Path(root).resolve(), "token": secrets.token_urlsafe(32)},
    )
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
