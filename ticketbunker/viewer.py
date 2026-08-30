"""The localhost ticket viewer and explicit local Work action (DESIGN.md §3.5).

Every request renders a fresh :class:`ticketbunker.api.TicketSnapshot` from
the repository-owned ``.tb/`` database. Ticket and Agent views remain read-only;
the isolated Work control dispatches the same guarded command as the CLI.
"""

from __future__ import annotations

import datetime
import hashlib
import html
import json
import re
import secrets
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

from . import __version__, autoagent, commands, config, coworker, editor, layout, truth, workflow
from .api import (
    BranchSnapshot,
    ReportSnapshot,
    TicketCatalog,
    TicketDatabase,
    TicketFacets,
    TicketPage,
    TicketQuery,
    TicketSnapshot,
    TicketSummary,
)


_STYLE = """
:root {
  color-scheme: light;
  --bg: oklch(99.4% .004 252);
  --panel: oklch(97.2% .006 252);
  --fg: oklch(28% .055 255);
  --body: oklch(40% .043 255);
  --muted: oklch(54% .03 255);
  --line: oklch(90% .012 252);
  --line-strong: oklch(79% .02 252);
  --accent: oklch(54% .205 255);
  --accent-strong: oklch(47% .19 255);
  --accent-soft: oklch(95% .025 255);
  --success: oklch(43% .105 155);
  --success-soft: oklch(96% .034 155);
  --warning: oklch(47% .105 85);
  --warning-soft: oklch(96.5% .045 85);
  --danger: oklch(48% .16 28);
  --danger-soft: oklch(96% .035 28);
  --on-accent: oklch(98.5% .006 252);
  --radius: 5px;
  --ease-out-quart: cubic-bezier(.25, 1, .5, 1);
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
button, input, select, textarea { font: inherit; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, summary:focus-visible {
  outline: 3px solid color-mix(in oklch, var(--accent) 30%, transparent);
  outline-offset: 3px;
  border-radius: 2px;
}
main { width: 100%; min-width: 0; max-width: 1320px; margin: 0 auto; padding: 30px 34px 72px; }
.crumbs { color: var(--muted); font-size: 12px; margin: 0 0 17px; }
.crumbs a { color: inherit; }
.ticket-head { padding-bottom: 18px; }
.ticket-key { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; color: var(--muted); font-size: 12px; font-weight: 600; letter-spacing: .02em; }
.ticket-project { color: var(--body); }
h1, h2, h3 { color: var(--fg); }
h1 { min-width: 0; max-width: 920px; margin: 5px 0 15px; font-size: 26px; line-height: 1.25; letter-spacing: -.018em; overflow-wrap: anywhere; }
h2 { font-size: 16px; line-height: 1.3; margin: 0 0 14px; }
h3 { font-size: 13px; line-height: 1.35; margin: 0 0 8px; }
p { margin: 0 0 12px; }
.ticket-title-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.ticket-title-row h1 { flex: 1 1 auto; }
.head-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; margin: 0; }
.head-facts div { display: flex; align-items: center; gap: 7px; }
.head-badges { flex-wrap: wrap; }
.head-updated { color: var(--muted); font-size: 12px; }
.truth-strip {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  gap: 3px;
  margin: 7px 0 0;
}
.truth-item {
  position: relative;
  color: var(--body);
  font-size: 12px;
}
.truth-item[hidden] { display: none; }
.truth-trigger {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  gap: 6px;
  padding: 4px 7px;
  border: 1px solid transparent;
  border-radius: 999px;
  color: var(--muted);
  background: transparent;
  cursor: pointer;
  list-style: none;
  white-space: nowrap;
}
.truth-trigger::-webkit-details-marker { display: none; }
.truth-trigger:hover, .truth-item[open] .truth-trigger { color: var(--fg); background: var(--panel); }
.truth-item.truth-synthesis .truth-trigger { border-color: var(--line); }
.truth-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  border: 1px solid var(--muted);
  border-radius: 50%;
  background: var(--bg);
}
.truth-item[data-state="current"] .truth-dot,
.truth-item[data-state="local"] .truth-dot { border-color: var(--success); background: var(--success); }
.truth-item[data-state="refreshing"] .truth-dot,
.truth-item[data-state="updating"] .truth-dot { border-color: var(--accent); background: var(--accent); }
.truth-item[data-state="cached"] .truth-dot,
.truth-item[data-state="outdated"] .truth-dot { border-color: var(--warning); background: var(--warning); }
.truth-item[data-state="failed"] .truth-dot,
.truth-item[data-state="stopped"] .truth-dot,
.truth-item[data-state="missing"] .truth-dot { border-color: var(--danger); background: var(--danger); }
.truth-compact { color: inherit; font-weight: 600; }
.truth-popover {
  position: absolute;
  z-index: 450;
  top: calc(100% + 6px);
  right: 0;
  display: none;
  width: min(300px, calc(100vw - 36px));
  padding: 10px 11px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  background: var(--bg);
  box-shadow: 0 5px 16px color-mix(in oklch, var(--fg) 12%, transparent);
}
.truth-item:hover .truth-popover,
.truth-item:focus-within .truth-popover,
.truth-item[open] .truth-popover { display: block; }
.truth-label { display: block; color: var(--fg); font-weight: 650; }
.truth-detail { display: block; min-width: 0; margin-top: 1px; color: var(--muted); overflow-wrap: anywhere; }
.truth-action {
  min-height: 32px;
  margin: 4px 0 -4px;
  padding: 3px 0;
  border: 0;
  color: var(--accent);
  background: transparent;
  cursor: pointer;
  font-weight: 650;
  text-align: left;
}
.truth-log {
  display: inline-block; margin-top: 4px; margin-left: 10px; color: var(--muted);
  font-size: .72rem; font-weight: 650; text-decoration: underline;
  text-underline-offset: 2px;
}
.truth-log[hidden] { display: none; }
.truth-action[hidden] { display: none; }
.truth-action:disabled { color: var(--muted); cursor: wait; }
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border-radius: 3px;
  color: var(--fg);
  background: var(--panel);
  font-size: 12px;
  font-weight: 650;
  line-height: 1.25;
}
.badge.status { color: var(--accent-strong); background: var(--accent-soft); }
.badge.label { font-weight: 500; }
.badge.done, .badge.closed, .badge.verified, .badge.merged { color: var(--success); background: var(--success-soft); }
.badge.stale, .badge.open { color: var(--warning); background: var(--warning-soft); }
.badge.unverified, .badge.missing { color: var(--danger); background: var(--danger-soft); }
.ticket-layout { display: grid; grid-template-columns: minmax(0, 1fr) 310px; gap: 0; }
.ticket-view-stack { min-width: 0; }
.ticket-main { min-width: 0; padding: 31px 42px 0 0; }
.ticket-page { max-width: 78ch; }
.ticket-rail { min-width: 0; padding: 31px 0 0 30px; border-left: 1px solid var(--line); }
.view-tabs { display: flex; gap: 20px; margin: 0; border-bottom: 1px solid var(--line); }
.view-tab {
  min-height: 44px;
  padding: 9px 2px 8px;
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  color: var(--muted);
  background: transparent;
  font-weight: 650;
  cursor: pointer;
}
.view-tab:hover { color: var(--fg); }
.view-tab[aria-selected="true"] { color: var(--accent); border-color: var(--accent); }
.view-panel[hidden], .report-panel[hidden] { display: none; }
.section { margin: 0 0 34px; }
.problem-card { max-width: 680px; margin: 56px auto 0; padding: 26px 28px; border: 1px solid var(--line); border-radius: var(--radius); }
.problem-card h1 { margin: 0 0 10px; }
.problem-card > p { max-width: 65ch; }
.problem-state { display: grid; gap: 15px; margin: 24px 0; }
.problem-state div { padding-top: 14px; border-top: 1px solid var(--line); }
.problem-state strong { display: block; margin-bottom: 4px; color: var(--fg); font-size: 12px; }
.problem-state code { overflow-wrap: anywhere; white-space: normal; }
.problem-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px; }
.problem-actions .primary { display: inline-flex; padding: 7px 12px; border-radius: 3px; color: var(--on-accent); background: var(--accent); font-weight: 650; }
.problem-actions .primary:hover { text-decoration: none; background: var(--accent-strong); }
.problem-id { margin-top: 20px; color: var(--muted); font-size: 11px; }
.description, .issue-activity { max-width: 75ch; }
.rich-text > :last-child { margin-bottom: 0; }
.rich-text h3 { margin: 22px 0 8px; font-size: 15px; }
.rich-text h4 { margin: 18px 0 7px; font-size: 13px; }
.rich-text pre {
  margin: 10px 0 14px;
  padding: 12px 14px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}
.rich-text pre code { padding: 0; background: transparent; white-space: pre; }
.criteria { margin: 0; padding-left: 21px; }
.criteria li { padding: 3px 0; }
.section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 13px; }
.section-heading h2 { margin: 0; }
.section-heading a { min-width: 0; flex: 0 1 auto; font-size: 12px; overflow-wrap: anywhere; text-align: right; }
.report-picker { display: flex; align-items: center; gap: 9px; color: var(--muted); font-size: 12px; font-weight: 600; }
.report-picker select {
  min-width: min(290px, 42vw);
  min-height: 40px;
  padding: 7px 30px 7px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--fg);
  background: var(--bg);
}
.report-context { margin: -4px 0 16px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.agent-generation { color: var(--muted); }
.agent-work { max-width: 78ch; }
.work-category { padding: 19px 0 20px; border-top: 1px solid var(--line); }
.work-category:first-of-type { padding-top: 0; border-top: 0; }
.work-category-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 7px; }
.work-category h3 { margin: 0; font-size: 15px; }
.coworker-inline {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  color: var(--muted);
  background: transparent;
  cursor: pointer;
}
.coworker-inline:hover { color: var(--accent); background: var(--accent-soft); }
.chat-icon { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.8; }
.brief-claims { min-width: 0; }
.brief-claims p { margin: 0 0 7px; }
.brief-claims p:last-child { margin-bottom: 0; }
.master-branch-summary { padding: 12px 2px; }
.master-branch-summary:first-child { padding-top: 1px; }
.master-branch-summary + .master-branch-summary { border-top: 1px solid var(--line-strong); }
.master-branch-label { margin-bottom: 7px; color: var(--fg); font-weight: 650; }
.master-branch-label span { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; font-weight: 400; }
.category-trail { margin-top: 10px; }
.category-trail > summary { display: list-item; cursor: pointer; padding: 5px 2px; color: var(--accent); font-size: 12px; font-weight: 600; }
.trail-count { float: right; margin-left: 16px; color: var(--muted); font-weight: 400; text-align: right; }
.trail-body { margin: 6px 0 0 17px; border-top: 1px solid var(--line); }
.trail-report { padding: 13px 2px 10px; border-bottom: 1px solid var(--line); }
.trail-report + .trail-report { border-top: 1px solid var(--line-strong); }
.trail-report-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px; align-items: start; margin-bottom: 7px; }
.trail-report-name { min-width: 0; color: var(--fg); font-weight: 650; }
.trail-report-meta { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; font-weight: 400; }
.trail-report-state { white-space: nowrap; }
.trail-copy ul { margin-bottom: 0; padding-left: 20px; }
.trail-copy li { margin: 6px 0; }
.trail-copy p:last-child { margin-bottom: 0; }
.report-state-trail { padding-top: 2px; border-top: 1px solid var(--line); }
.comments { border-top: 1px solid var(--line); }
.comment { padding: 14px 2px; border-bottom: 1px solid var(--line); }
.comment-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 5px; margin-bottom: 5px; color: var(--fg); font-weight: 650; }
.activity-kind { color: var(--muted); font-size: 12px; font-weight: 600; }
.comment-meta { margin-left: 6px; color: var(--muted); font-size: 12px; font-weight: 400; }
.comment-text { color: var(--body); }
.lifecycle { margin: 0; padding: 0; list-style: none; }
.lifecycle li { padding: 6px 10px; border-left: 1px solid var(--line); color: var(--muted); }
.lifecycle li.current { border-color: var(--accent); color: var(--fg); background: var(--accent-soft); font-weight: 650; }
.branch-list { border-top: 1px solid var(--line); }
.branch-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.branch-heading h2 { margin: 0; }
.branch-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  min-width: 36px;
  height: 36px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--accent);
  background: var(--bg);
  cursor: pointer;
  font-size: 20px;
  line-height: 1;
}
.branch-add:hover { background: var(--accent-soft); }
.work-panel { margin: 12px 0 16px; padding: 14px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); }
.work-panel[hidden] { display: none; }
.work-panel h3 { margin-bottom: 11px; }
.work-form { display: grid; gap: 11px; }
.work-field { display: grid; gap: 4px; color: var(--muted); font-size: 11px; font-weight: 650; }
.work-field input, .work-field select {
  width: 100%;
  min-height: 42px;
  padding: 7px 9px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--fg);
  background: var(--bg);
}
.work-submit { min-height: 42px; border: 1px solid var(--accent); border-radius: var(--radius); color: var(--on-accent); background: var(--accent); font-weight: 650; cursor: pointer; }
.work-submit:disabled { border-color: var(--line-strong); color: var(--muted); background: var(--line); cursor: not-allowed; }
.work-state { min-height: 20px; margin: 0; color: var(--muted); font-size: 12px; }
.work-state.error { color: var(--danger); }
.work-state.success { color: var(--success); }
.branch-row { display: block; padding: 11px 2px; border-bottom: 1px solid var(--line); color: inherit; }
.branch-row:hover { background: var(--panel); text-decoration: none; }
.branch-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.branch-name { min-width: 0; color: var(--fg); font-weight: 650; }
.branch-meta { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
.issue-details { margin: 6px 0 34px; padding-top: 28px; border-top: 1px solid var(--line); }
.details-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.details-heading h2 { margin: 0; }
.issue-details dl {
  display: grid;
  grid-template-columns: 144px minmax(0, 1fr);
  gap: 12px 14px;
  margin: 0;
  padding-top: 3px;
  font-size: 13px;
}
.issue-details dt { color: var(--muted); }
.issue-details dd { min-width: 0; margin: 0; color: var(--fg); overflow-wrap: anywhere; }
.metadata-disclosure { position: relative; flex: 0 0 auto; }
.metadata-disclosure > summary {
  min-height: 44px;
  margin: -11px 0;
  padding: 11px 2px;
  color: var(--accent);
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
}
.metadata-menu {
  position: absolute;
  z-index: 100;
  top: calc(100% + 8px);
  right: 0;
  width: min(480px, calc(100vw - 48px));
  max-height: min(620px, 70vh);
  padding: 16px;
  overflow: auto;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  background: var(--bg);
  box-shadow: 0 8px 24px color-mix(in oklch, var(--fg) 14%, transparent);
}
.metadata-menu dl { grid-template-columns: 170px minmax(0, 1fr); padding: 0; }
.field-values { display: flex; flex-wrap: wrap; gap: 5px; }
.field-token {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  color: var(--fg);
  background: var(--panel);
  font-size: 12px;
}
.validation { padding: 11px 13px; margin-bottom: 18px; border: 1px solid var(--danger); border-radius: var(--radius); background: var(--danger-soft); }
.validation strong { color: var(--danger); }
.validation ul { margin: 5px 0 0; padding-left: 18px; }
.empty { color: var(--muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .94em; overflow-wrap: anywhere; }
.tag { display: inline-block; margin: 0 1px 0 0; padding: 0 4px; border: 1px solid currentColor; border-radius: 3px; font: 10px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; vertical-align: .08em; }
.tag.obs { color: var(--accent-strong); }
.tag.real { color: var(--success); }
.tag.spec { color: var(--warning); }
code { padding: 1px 3px; border-radius: 3px; background: var(--panel); color: var(--fg); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9em; overflow-wrap: anywhere; }
hr { border: 0; border-top: 1px solid var(--line); margin: 22px 0; }
ul, ol { margin: 6px 0 12px; padding-left: 21px; }
li { margin: 3px 0; }
li > ul, li > ol { margin: 3px 0 2px; }
.catalog { max-width: 1120px; }
.catalog-nav { display: flex; gap: 22px; margin: 0 0 26px; border-bottom: 1px solid var(--line); }
.catalog-nav a {
  min-height: 44px;
  padding: 11px 2px 9px;
  border-bottom: 2px solid transparent;
  color: var(--muted);
  font-weight: 650;
}
.catalog-nav a:hover { color: var(--fg); text-decoration: none; }
.catalog-nav a[aria-current="page"] { color: var(--accent); border-color: var(--accent); }
.catalog-title { display: flex; align-items: baseline; justify-content: space-between; gap: 20px; }
.catalog-title h1 { margin-bottom: 18px; }
.catalog-count { color: var(--muted); font-size: 12px; white-space: nowrap; }
.catalog-tools {
  position: sticky;
  top: 0;
  z-index: 5;
  margin: 0 0 28px;
  padding: 13px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  background: color-mix(in oklch, var(--bg) 97%, transparent);
}
.ticket-filters {
  display: grid;
  grid-template-columns: minmax(220px, 1.7fr) repeat(4, minmax(118px, 1fr)) auto auto;
  align-items: end;
  gap: 10px;
}
.ticket-filters.with-project { grid-template-columns: minmax(180px, 1.4fr) 132px 166px 132px 132px 132px auto auto; }
.filter-field { display: grid; gap: 4px; min-width: 0; color: var(--muted); font-size: 11px; font-weight: 650; }
.filter-field input, .filter-field select {
  width: 100%;
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--fg);
  background: var(--bg);
}
.filter-field input::placeholder { color: var(--muted); font-weight: 400; }
.filter-action, .filter-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 8px 13px;
  border-radius: var(--radius);
  font-weight: 650;
}
.filter-action { border: 1px solid var(--accent); color: var(--on-accent); background: var(--accent); cursor: pointer; }
.filter-clear { border: 1px solid transparent; color: var(--muted); }
.filter-clear:hover { color: var(--fg); text-decoration: none; }
.ticket-group { margin-bottom: 32px; }
.ticket-group-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 2px; }
.ticket-group-head h2 { margin: 0; }
.ticket-group-count { color: var(--muted); font-size: 12px; }
.ticket-link { display: grid; grid-template-columns: 94px minmax(0, 1fr) minmax(150px, auto); gap: 14px; padding: 13px 8px 13px 2px; border-bottom: 1px solid var(--line); color: inherit; }
.ticket-link:hover { background: var(--panel); text-decoration: none; }
.ticket-id { color: var(--accent); font-weight: 650; }
.ticket-summary { min-width: 0; }
.ticket-title { display: block; color: var(--fg); font-weight: 600; }
.ticket-context { display: flex; flex-wrap: wrap; gap: 5px 11px; margin-top: 4px; color: var(--muted); font-size: 12px; }
.ticket-label { padding: 0 5px; border-radius: 3px; color: var(--body); background: var(--panel); }
.ticket-link-meta { align-self: center; color: var(--muted); font-size: 12px; text-align: right; }
.project-list { margin-top: 4px; border-top: 1px solid var(--line); }
.project-link { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 20px; min-height: 62px; padding: 12px 4px; border-bottom: 1px solid var(--line); color: inherit; }
.project-link:hover { background: var(--panel); text-decoration: none; }
.project-name { display: block; color: var(--fg); font-size: 15px; font-weight: 650; }
.project-key, .project-meta { color: var(--muted); font-size: 12px; }
.project-meta { align-self: center; text-align: right; }
.pagination { display: flex; align-items: center; justify-content: flex-end; gap: 6px; margin-top: 26px; }
.pagination a, .pagination span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  min-height: 44px;
  padding: 7px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
}
.pagination a:hover { background: var(--panel); text-decoration: none; }
.pagination .page-position { min-width: auto; border-color: transparent; color: var(--muted); }
.pagination .disabled { color: var(--line-strong); }
.coworker-launcher {
  position: fixed;
  z-index: 500;
  right: max(20px, env(safe-area-inset-right));
  bottom: max(20px, env(safe-area-inset-bottom));
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border: 1px solid var(--accent);
  border-radius: 999px;
  color: var(--on-accent);
  background: var(--accent);
  box-shadow: 0 8px 22px color-mix(in oklch, var(--fg) 18%, transparent);
  cursor: pointer;
  transition: transform 160ms var(--ease-out-quart), background 160ms var(--ease-out-quart);
}
.coworker-launcher[hidden] { display: none; }
.coworker-launcher:hover { background: var(--accent-strong); transform: translateY(-1px); }
.coworker-launcher[aria-expanded="true"] { transform: scale(.92); }
.coworker-panel {
  position: fixed;
  z-index: 400;
  top: 0;
  right: 0;
  bottom: 0;
  display: grid;
  grid-template-rows: auto auto auto minmax(120px, 1fr) auto;
  width: min(430px, calc(100vw - 32px));
  max-height: 100dvh;
  overflow: hidden;
  border: 0;
  border-left: 1px solid var(--line-strong);
  background: var(--bg);
  box-shadow: -8px 0 28px color-mix(in oklch, var(--fg) 14%, transparent);
  transform-origin: center right;
  transition: opacity 180ms var(--ease-out-quart), transform 180ms var(--ease-out-quart);
}
.coworker-panel[hidden] { display: none; }
.coworker-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 48px;
  padding: 8px 10px 8px 14px;
  border-bottom: 1px solid var(--line);
}
.coworker-title { color: var(--fg); font-size: 15px; font-weight: 700; }
.coworker-collapse {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 0;
  color: var(--muted);
  background: transparent;
  cursor: pointer;
  font-size: 20px;
}
.coworker-collapse:hover { color: var(--fg); background: var(--panel); }
.coworker-tabs { display: flex; padding: 0 10px; border-bottom: 1px solid var(--line); }
.coworker-tab {
  min-height: 44px;
  padding: 9px 10px 8px;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--muted);
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  font-weight: 650;
}
.coworker-tab[aria-selected="true"] { color: var(--accent); border-color: var(--accent); }
.coworker-scope {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.coworker-scope select {
  min-width: 0;
  max-width: 260px;
  min-height: 36px;
  padding: 5px 28px 5px 8px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--fg);
  background: var(--bg);
}
.coworker-messages { min-height: 150px; padding: 12px 14px 4px; overflow-y: auto; }
.coworker-empty { margin: 6px 0 14px; color: var(--muted); }
.chat-message { padding: 8px 0; }
.chat-message + .chat-message { border-top: 1px solid var(--line); }
.chat-role { display: block; margin-bottom: 3px; color: var(--fg); font-size: 12px; font-weight: 700; }
.chat-copy { margin: 0; color: var(--body); white-space: pre-wrap; overflow-wrap: anywhere; }
.chat-reference {
  margin: 4px 0 7px;
  padding-left: 9px;
  border-left: 2px solid var(--line-strong);
  color: var(--muted);
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.coworker-proposal { margin: 10px 0 14px; padding: 12px; border: 1px solid var(--line-strong); border-radius: var(--radius); }
.coworker-proposal h3 { margin-bottom: 4px; }
.proposal-note { color: var(--muted); font-size: 12px; }
.proposal-change { padding: 10px 0; border-top: 1px solid var(--line); }
.proposal-branch { margin-bottom: 8px; color: var(--fg); font-weight: 650; overflow-wrap: anywhere; }
.proposal-label { display: block; margin: 7px 0 2px; color: var(--muted); font-size: 11px; font-weight: 650; }
.proposal-value { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.proposal-edit {
  width: 100%;
  min-height: 112px;
  margin-top: 5px;
  padding: 8px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--fg);
  background: var(--bg);
  resize: vertical;
}
.proposal-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.proposal-actions button {
  min-height: 38px;
  padding: 6px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--fg);
  background: var(--bg);
  cursor: pointer;
  font-weight: 650;
}
.proposal-actions .primary { border-color: var(--accent); color: var(--on-accent); background: var(--accent); }
.proposal-actions button:disabled { color: var(--muted); background: var(--panel); cursor: wait; }
.coworker-compose { padding: 10px 14px 14px; border-top: 1px solid var(--line); }
.coworker-compose label { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
.coworker-reference {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2px 8px;
  margin-bottom: 9px;
  padding: 7px 8px 7px 10px;
  border-left: 2px solid var(--accent);
  background: var(--panel);
}
.coworker-reference[hidden] { display: none; }
.coworker-reference-meta { color: var(--muted); font-size: 11px; font-weight: 650; }
.coworker-reference-copy { grid-column: 1 / -1; margin: 0; color: var(--body); font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.coworker-reference-remove {
  width: 28px;
  height: 28px;
  margin: -5px -5px -2px 0;
  border: 0;
  color: var(--muted);
  background: transparent;
  cursor: pointer;
  font-size: 17px;
}
.coworker-compose textarea {
  width: 100%;
  min-height: 72px;
  max-height: 180px;
  padding: 9px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--fg);
  background: var(--bg);
  resize: vertical;
}
.coworker-compose-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 8px; }
.coworker-status { min-width: 0; margin: 0; color: var(--muted); font-size: 12px; }
.coworker-status.error { color: var(--danger); }
.coworker-status.success { color: var(--success); }
.coworker-send {
  flex: 0 0 auto;
  min-height: 40px;
  padding: 7px 13px;
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  color: var(--on-accent);
  background: var(--accent);
  cursor: pointer;
  font-weight: 650;
}
.coworker-send:disabled { border-color: var(--line-strong); color: var(--muted); background: var(--panel); cursor: wait; }
.selection-ask {
  position: fixed;
  z-index: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  color: var(--accent);
  background: var(--bg);
  box-shadow: 0 5px 16px color-mix(in oklch, var(--fg) 16%, transparent);
  cursor: pointer;
}
.selection-ask[hidden] { display: none; }
.selection-ask:hover { background: var(--accent-soft); }
.source-link {
  display: inline;
  margin: 0;
  padding: 0;
  border: 0;
  color: var(--accent);
  background: transparent;
  cursor: pointer;
  font: .9em ui-monospace, SFMono-Regular, Consolas, monospace;
  text-decoration: underline;
  text-decoration-color: color-mix(in oklch, var(--accent) 35%, transparent);
  text-underline-offset: 2px;
}
.source-link:hover { text-decoration-color: currentColor; }
.source-reference { display: inline; }
.source-copy {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 19px;
  height: 19px;
  margin: 0 1px 0 3px;
  padding: 0;
  border: 0;
  border-radius: 3px;
  color: var(--muted);
  background: transparent;
  cursor: pointer;
  vertical-align: text-bottom;
}
.source-copy:hover { color: var(--accent); background: var(--accent-soft); }
.source-copy:disabled { color: var(--line-strong); cursor: wait; }
.copy-icon { position: relative; width: 11px; height: 11px; }
.copy-icon::before, .copy-icon::after {
  content: "";
  position: absolute;
  width: 7px;
  height: 7px;
  border: 1px solid currentColor;
  border-radius: 1px;
}
.copy-icon::before { top: 0; right: 0; }
.copy-icon::after { bottom: 0; left: 0; background: var(--bg); }
.source-status {
  position: fixed;
  z-index: 650;
  right: max(20px, env(safe-area-inset-right));
  bottom: max(78px, calc(env(safe-area-inset-bottom) + 78px));
  max-width: min(390px, calc(100vw - 32px));
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--body);
  background: var(--bg);
  box-shadow: 0 5px 16px color-mix(in oklch, var(--fg) 14%, transparent);
  font-size: 12px;
}
.source-status[hidden] { display: none; }
.source-status.error { color: var(--danger); border-color: var(--danger); }
.source-status.success { color: var(--success); border-color: var(--success); }
.source-body { min-width: 320px; overflow: hidden; }
.source-shell {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  width: 100vw;
  max-width: none;
  height: 100vh;
  margin: 0;
  padding: 0;
}
@media (min-width: 1001px) {
  .source-body.coworker-open .source-shell { width: calc(100vw - 430px); }
}
.source-toolbar {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  min-height: 58px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--bg);
}
.source-back {
  display: inline-flex;
  align-items: center;
  min-height: 40px;
  padding: 7px 9px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--body);
  font-weight: 650;
}
.source-back:hover { background: var(--panel); text-decoration: none; }
.source-file { min-width: 0; }
.source-path {
  display: block;
  overflow: hidden;
  color: var(--fg);
  font: 650 13px/1.35 ui-monospace, SFMono-Regular, Consolas, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.source-branch { display: block; color: var(--muted); font-size: 11px; }
.source-actions { display: flex; align-items: center; gap: 10px; }
.source-save-state { color: var(--muted); font-size: 12px; white-space: nowrap; }
.source-save-state.dirty { color: var(--warning); }
.source-save-state.error {
  position: fixed;
  z-index: 50;
  right: 14px;
  bottom: 42px;
  max-width: min(520px, calc(100vw - 28px));
  padding: 8px 10px;
  border: 1px solid var(--danger);
  border-radius: var(--radius);
  color: var(--danger);
  background: var(--bg);
  white-space: normal;
}
.source-save {
  min-height: 40px;
  padding: 7px 14px;
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  color: var(--on-accent);
  background: var(--accent);
  cursor: pointer;
  font-weight: 650;
}
.source-save:hover { background: var(--accent-strong); }
.source-save:disabled { border-color: var(--line-strong); color: var(--muted); background: var(--panel); cursor: default; }
.source-workspace {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  min-height: 0;
  background: oklch(98.6% .005 252);
}
.source-gutter {
  min-width: 54px;
  height: 100%;
  margin: 0;
  padding: 14px 11px 80px 8px;
  overflow: hidden;
  border-right: 1px solid var(--line);
  color: var(--muted);
  background: var(--panel);
  font: 13px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace;
  text-align: right;
  user-select: none;
}
.source-input {
  width: 100%;
  height: 100%;
  min-height: 0;
  margin: 0;
  padding: 14px 18px 80px;
  resize: none;
  overflow: auto;
  border: 0;
  border-radius: 0;
  outline: 0;
  color: oklch(25% .035 255);
  background: transparent;
  font: 13px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace;
  caret-color: var(--accent-strong);
}
.source-input:focus-visible { outline: 0; }
.source-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 30px;
  padding: 5px 14px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  background: var(--panel);
  font-size: 11px;
}
.source-footer-tools { display: flex; align-items: center; gap: 12px; }
.source-selection-ask {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  gap: 5px;
  padding: 2px 6px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--body);
  background: var(--bg);
  cursor: pointer;
  font-size: 11px;
  font-weight: 650;
}
.source-selection-ask[hidden] { display: none; }
.source-selection-ask:hover { color: var(--accent); border-color: var(--accent); }
.source-selection-ask:disabled { color: var(--muted); border-color: var(--line); cursor: default; }
.source-selection-ask .chat-icon { width: 14px; height: 14px; }
.source-position { color: var(--body); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
@media (min-width: 761px) and (max-width: 1000px) {
  .ticket-layout { grid-template-columns: minmax(0, 1fr) 260px; }
  .ticket-main { padding-right: 24px; }
  .ticket-rail { padding-left: 20px; }
  .report-picker select { min-width: min(210px, 30vw); }
  .ticket-filters, .ticket-filters.with-project { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .filter-field.search { grid-column: span 2; }
}
@media (max-width: 760px) {
  main { padding: 20px 18px 55px; }
  h1 { font-size: 22px; }
  .ticket-layout { display: block; }
  .ticket-main { padding: 25px 0 0; }
  .ticket-rail { padding: 28px 0 0; border-left: 0; border-top: 1px solid var(--line); }
  .issue-details dl { grid-template-columns: 104px minmax(0, 1fr); }
  .section-heading { align-items: flex-start; }
  .section-heading h2 { flex: 0 0 auto; }
  .section-heading a { flex: 1 1 auto; }
  .agent-heading { display: grid; grid-template-columns: 1fr; }
  .report-picker { display: grid; grid-template-columns: auto minmax(0, 1fr); width: 100%; }
  .report-picker select { min-width: 0; width: 100%; min-height: 44px; }
  .trail-body { margin-left: 0; }
  .trail-report-head { grid-template-columns: 1fr; gap: 6px; }
  .trail-report-state { justify-self: start; }
  .metadata-menu { width: calc(100vw - 36px); }
  .metadata-menu dl { grid-template-columns: 112px minmax(0, 1fr); }
  .catalog-title { display: block; }
  .catalog-count { display: block; margin: -10px 0 16px; }
  .catalog-tools { position: static; }
  .ticket-filters { grid-template-columns: 1fr 1fr; }
  .ticket-filters.with-project { grid-template-columns: 1fr 1fr; }
  .filter-field.search { grid-column: 1 / -1; }
  .filter-field.status, .filter-field.assignee, .filter-field.project { grid-column: 1 / -1; }
  .filter-action, .filter-clear { width: 100%; }
  .ticket-link { grid-template-columns: 74px minmax(0, 1fr); padding-right: 2px; }
  .ticket-link-meta { grid-column: 2; text-align: left; }
  .project-link { grid-template-columns: 1fr; gap: 3px; }
  .project-meta { text-align: left; }
  .pagination { justify-content: space-between; }
  .ticket-title-row { display: block; }
  .truth-strip { justify-content: flex-start; margin: -6px 0 12px; }
  .truth-popover { right: auto; left: 0; }
  .coworker-panel {
    width: calc(100vw - env(safe-area-inset-left) - env(safe-area-inset-right));
    max-height: 100dvh;
  }
  .coworker-launcher { right: max(14px, env(safe-area-inset-right)); bottom: max(14px, env(safe-area-inset-bottom)); }
  .coworker-scope { align-items: stretch; flex-direction: column; gap: 4px; }
  .coworker-scope select { width: 100%; max-width: none; min-height: 44px; }
  .coworker-compose textarea { min-height: 88px; }
  .source-toolbar { grid-template-columns: auto minmax(0, 1fr); gap: 9px; }
  .source-actions { grid-column: 1 / -1; justify-content: flex-end; }
  .source-toolbar { padding: 8px 10px; }
  .source-gutter { min-width: 43px; padding-right: 7px; }
  .source-input { padding-left: 11px; }
  .source-footer span:first-child { display: none; }
}
@media (pointer: coarse) {
  .coworker-inline, .coworker-tab, .truth-action, .truth-trigger, .source-copy { min-height: 44px; }
  .source-copy { width: 32px; }
}
@media (prefers-reduced-motion: reduce) {
  * { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; }
}
"""


_PRESENCE_JS = """
const viewerToken = document.documentElement.dataset.viewerToken || '';
const viewerSession = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;

async function viewerPresence() {
  if (!viewerToken) return;
  try {
    await fetch('/_viewer/presence', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-TicketBunker-Token': viewerToken},
      body: JSON.stringify({session: viewerSession}),
      keepalive: true
    });
  } catch (error) { /* The local viewer may already be closing. */ }
}

viewerPresence();
window.addEventListener('pagehide', () => {
  if (!viewerToken) return;
  const body = new Blob(
    [JSON.stringify({_token: viewerToken, session: viewerSession})],
    {type: 'application/json'}
  );
  navigator.sendBeacon('/_viewer/close', body);
});
"""


_POLL_JS = _PRESENCE_JS + r"""
const fp = document.documentElement.dataset.fp;
setInterval(viewerPresence, 5000);
const viewTabs = [...document.querySelectorAll('[data-view-tab]')];
const viewPanels = [...document.querySelectorAll('[data-view-panel]')];
const reportSelect = document.querySelector('[data-report-select]');
const reportPanels = [...document.querySelectorAll('[data-report-panel]')];

function activateView(name, moveFocus = false) {
  viewTabs.forEach((tab) => {
    const selected = tab.dataset.viewTab === name;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && moveFocus) tab.focus();
  });
  viewPanels.forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== name; });
}

function selectReport(value, updateHash = false) {
  if (!reportSelect || !reportPanels.some((panel) => panel.dataset.reportPanel === value)) return;
  reportSelect.value = value;
  reportPanels.forEach((panel) => { panel.hidden = panel.dataset.reportPanel !== value; });
  if (updateHash) {
    const selected = reportPanels.find((panel) => panel.dataset.reportPanel === value);
    history.replaceState(null, '', `#${selected.id}`);
  }
}

function openLinkedReport(hash) {
  if (!hash || !hash.startsWith('#report-')) return false;
  const target = document.querySelector(hash);
  if (!target || !target.dataset.reportPanel) return false;
  activateView('agent');
  selectReport(target.dataset.reportPanel);
  return true;
}

viewTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => {
    activateView(tab.dataset.viewTab);
    if (tab.dataset.viewTab === 'ticket') history.replaceState(null, '', '#ticket');
    else if (reportSelect) selectReport(reportSelect.value, true);
    else history.replaceState(null, '', '#agent');
  });
  tab.addEventListener('keydown', (event) => {
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % viewTabs.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + viewTabs.length) % viewTabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = viewTabs.length - 1;
    if (next !== null) {
      event.preventDefault();
      activateView(viewTabs[next].dataset.viewTab, true);
    }
  });
});

if (reportSelect) reportSelect.addEventListener('change', () => selectReport(reportSelect.value, true));
if (location.hash === '#ticket') activateView('ticket');
else if (location.hash === '#agent') activateView('agent');
else openLinkedReport(location.hash);
document.addEventListener('click', (event) => {
  const link = event.target.closest('a[href^="#report-"]');
  if (link && openLinkedReport(link.getAttribute('href'))) {
    event.preventDefault();
    history.replaceState(null, '', link.getAttribute('href'));
    document.querySelector(link.getAttribute('href')).scrollIntoView({block: 'start'});
  }
});

const workToggle = document.querySelector('[data-work-toggle]');
const workPanel = document.querySelector('[data-work-panel]');
const workForm = document.querySelector('[data-work-form]');
const workBase = document.querySelector('[data-work-base]');
const workStatus = document.querySelector('[data-work-status]');
const workSubmit = document.querySelector('[data-work-submit]');

function setWorkStatus(message, kind = '') {
  if (!workStatus) return;
  workStatus.textContent = message;
  workStatus.className = `work-state ${kind}`.trim();
}

function workError(value, fallback) {
  return [
    value.message || fallback,
    value.state || '',
    value.action ? `Next: ${value.action}` : ''
  ].filter(Boolean).join(' ');
}

async function loadWorkState() {
  if (!workForm || workForm.dataset.loaded === 'true') return;
  setWorkStatus('Checking the local repository…');
  workSubmit.disabled = true;
  try {
    const response = await fetch(`/work/state/${encodeURIComponent(workForm.dataset.ticket)}`);
    const value = await response.json();
    if (!response.ok) throw new Error(workError(value, 'Repository preflight failed.'));
    workBase.replaceChildren(...value.base_branches.map((branch) => {
      const option = document.createElement('option');
      option.value = branch;
      option.textContent = branch;
      option.selected = branch === value.base_branch;
      return option;
    }));
    workForm.dataset.loaded = 'true';
    if (value.clean) {
      setWorkStatus(`Ready on ${value.current_branch || 'detached HEAD'}.`);
      workSubmit.disabled = value.base_branches.length === 0;
    } else {
      setWorkStatus(`Commit or stash first: ${value.dirty_paths.join(', ')}`, 'error');
    }
  } catch (error) {
    setWorkStatus(error.message, 'error');
  }
}

if (workToggle && workPanel) workToggle.addEventListener('click', () => {
  workPanel.hidden = !workPanel.hidden;
  workToggle.setAttribute('aria-expanded', String(!workPanel.hidden));
  history.replaceState(null, '', workPanel.hidden ? '#agent' : '#work');
  if (!workPanel.hidden) {
    loadWorkState();
    workForm.querySelector('input[name="name"]').focus();
  }
});

if (location.hash === '#work' && workToggle && workPanel) {
  activateView('agent');
  workPanel.hidden = false;
  workToggle.setAttribute('aria-expanded', 'true');
  loadWorkState();
}

if (workForm) workForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  workSubmit.disabled = true;
  setWorkStatus('Refreshing the base and creating the branch…');
  try {
    const response = await fetch('/work', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-TicketBunker-Token': workForm.dataset.token},
      body: JSON.stringify({
        ticket: workForm.dataset.ticket,
        name: new FormData(workForm).get('name'),
        base: new FormData(workForm).get('base')
      })
    });
    const value = await response.json();
    if (!response.ok) throw new Error(workError(value, 'Branch creation failed.'));
    setWorkStatus(`Created and switched to ${value.branch}.`, 'success');
    window.setTimeout(() => location.reload(), 900);
  } catch (error) {
    setWorkStatus(error.message, 'error');
    workSubmit.disabled = false;
  }
});

const truthStrip = document.querySelector('[data-truth]');

function requestError(value, fallback) {
  return [value.message || fallback, value.state || '', value.action ? `Next: ${value.action}` : '']
    .filter(Boolean).join(' ');
}

async function jsonRequest(path, options = {}) {
  const response = await fetch(path, options);
  let value = {};
  try { value = await response.json(); } catch (error) { value = {}; }
  if (!response.ok) throw new Error(requestError(value, 'TicketBunker could not finish this request.'));
  return value;
}

function updateTruth(value) {
  if (!truthStrip) return;
  ['companion', 'git', 'synthesis'].forEach((key) => {
    const item = truthStrip.querySelector(`[data-truth-item="${key}"]`);
    const current = value[key] || {};
    if (!item) return;
    const status = current.status || 'cached';
    item.dataset.state = status;
    item.hidden = key !== 'synthesis' && (status === 'current' || status === 'local');
    if (item.hidden) item.open = false;
    item.querySelector('.truth-label').textContent = current.label || key;
    item.querySelector('.truth-detail').textContent = current.detail || '';
    const compact = item.querySelector('.truth-compact');
    if (compact) compact.textContent = current.compact || current.label || 'Synthesis';
    const trigger = item.querySelector('.truth-trigger');
    if (trigger) {
      const label = current.label || key;
      trigger.title = label;
      trigger.setAttribute('aria-label', current.detail ? `${label}. ${current.detail}` : label);
    }
    const action = item.querySelector('[data-truth-action]');
    if (action) {
      action.textContent = current.action || 'Refresh';
      action.hidden = !current.action;
      action.dataset.truthCommand = current.command || 'refresh';
      action.disabled = value.refresh === 'running' || current.status === 'updating';
    }
    const log = item.querySelector('[data-truth-log]');
    if (log) {
      log.href = current.log_url || '/truth/agent-log';
      log.hidden = !current.log_url;
    }
  });
}

function preserveTicketUi() {
  const coworkerPanel = document.querySelector('[data-coworker]');
  if (!coworkerPanel) return;
  const key = `tb-coworker:${coworkerPanel.dataset.project || 'local'}:${coworkerPanel.dataset.ticket}`;
  sessionStorage.setItem(key, JSON.stringify({
    expanded: !coworkerPanel.hidden,
    category: coworkerPanel.dataset.category || 'what',
    scope: coworkerPanel.querySelector('[data-coworker-scope]')?.value || 'combined',
    draft: coworkerPanel.querySelector('textarea[name="message"]')?.value || '',
    reference: coworkerReference
  }));
}

async function pollTruth() {
  if (!truthStrip) return;
  const query = new URLSearchParams({
    project: truthStrip.dataset.project || '',
    ticket: truthStrip.dataset.ticket
  });
  try {
    const value = await jsonRequest(`/truth/state?${query}`);
    updateTruth(value);
    if (value.content_revision && value.content_revision !== truthStrip.dataset.revision) {
      if (coworkerBusy) return;
      preserveTicketUi();
      location.reload();
    }
  } catch (error) {
    const item = truthStrip.querySelector('[data-truth-item="companion"]');
    const detail = item?.querySelector('.truth-detail');
    if (item) {
      item.hidden = false;
      item.dataset.state = 'cached';
    }
    if (detail) detail.textContent = 'The viewer kept the ticket already on screen.';
  }
}

truthStrip?.addEventListener('click', async (event) => {
  const action = event.target.closest('[data-truth-action]');
  if (!action) return;
  truthStrip.querySelectorAll('[data-truth-action]').forEach((button) => { button.disabled = true; });
  try {
    const endpoint = action.dataset.truthCommand === 'synthesize'
      ? '/truth/synthesize'
      : '/truth/refresh';
    const value = await jsonRequest(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-TicketBunker-Token': truthStrip.dataset.token},
      body: JSON.stringify({project: truthStrip.dataset.project || '', ticket: truthStrip.dataset.ticket})
    });
    updateTruth(value);
  } catch (error) {
    const detail = action.closest('.truth-item').querySelector('.truth-detail');
    detail.textContent = error.message;
  } finally {
    truthStrip.querySelectorAll('[data-truth-action]').forEach((button) => { button.disabled = false; });
  }
});

const coworkerPanel = document.querySelector('[data-coworker]');
const coworkerLauncher = document.querySelector('[data-coworker-launcher]');
const coworkerCollapse = document.querySelector('[data-coworker-collapse]');
const coworkerTabs = [...document.querySelectorAll('[data-coworker-tab]')];
const coworkerScope = document.querySelector('[data-coworker-scope]');
const coworkerMessages = document.querySelector('[data-coworker-messages]');
const coworkerForm = document.querySelector('[data-coworker-form]');
const coworkerStatus = document.querySelector('[data-coworker-status]');
const coworkerSend = document.querySelector('[data-coworker-send]');
const coworkerReferenceBox = document.querySelector('[data-coworker-reference]');
const coworkerReferenceMeta = document.querySelector('[data-coworker-reference-meta]');
const coworkerReferenceCopy = document.querySelector('[data-coworker-reference-copy]');
const coworkerReferenceRemove = document.querySelector('[data-coworker-reference-remove]');
const selectionAsk = document.querySelector('[data-selection-ask]');
const sourceStatus = document.querySelector('[data-source-status]');
let coworkerCategory = 'what';
let coworkerBusy = false;
let coworkerReference = null;
let pendingSelection = null;

function setCoworkerStatus(message, kind = '') {
  if (!coworkerStatus) return;
  coworkerStatus.textContent = message;
  coworkerStatus.className = `coworker-status ${kind}`.trim();
}

function setCoworkerCategory(category, moveFocus = false) {
  coworkerCategory = category;
  if (coworkerPanel) coworkerPanel.dataset.category = category;
  coworkerMessages?.setAttribute('aria-labelledby', `coworker-tab-${category}`);
  coworkerTabs.forEach((tab) => {
    const selected = tab.dataset.coworkerTab === category;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && moveFocus) tab.focus();
  });
}

function selectedPageScope() {
  if (!reportSelect) return 'combined';
  return reportSelect.value === 'master' ? 'combined' : reportSelect.value;
}

function coworkerPageScope() {
  if (!reportSelect) return coworkerScope?.value || 'combined';
  const selected = selectedPageScope();
  return [...(coworkerScope?.options || [])].some((option) => option.value === selected)
    ? selected
    : 'combined';
}

function referenceLabel(reference) {
  if (reference.kind === 'source') {
    const lines = reference.line_start === reference.line_end
      ? `line ${reference.line_start}`
      : `lines ${reference.line_start}-${reference.line_end}`;
    return `${reference.path} · ${lines}${reference.unsaved ? ' · unsaved draft' : ''}`;
  }
  const place = reference.branch || (reference.scope === 'combined' ? 'Combined' : 'this branch');
  return `${reference.category[0].toUpperCase()}${reference.category.slice(1)} · ${place}`;
}

function setCoworkerReference(reference) {
  coworkerReference = reference?.text ? reference : null;
  if (!coworkerReferenceBox) return;
  coworkerReferenceBox.hidden = !coworkerReference;
  if (coworkerReference) {
    coworkerReferenceMeta.textContent = referenceLabel(coworkerReference);
    coworkerReferenceCopy.textContent = coworkerReference.text;
  } else {
    coworkerReferenceMeta.textContent = '';
    coworkerReferenceCopy.textContent = '';
  }
}

function selectionElement(node) {
  return node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
}

function selectedSynthesis() {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const anchor = selectionElement(selection.anchorNode);
  const focus = selectionElement(selection.focusNode);
  const start = anchor?.closest('[data-synthesis-copy]');
  const end = focus?.closest('[data-synthesis-copy]');
  if (!start || start !== end) return null;
  const text = selection.toString().trim();
  if (!text || text.length > 2400) return null;
  const category = start.closest('[data-synthesis-category]')?.dataset.synthesisCategory;
  const panel = start.closest('[data-report-panel]');
  if (!category || !panel || panel.hidden) return null;
  const scope = panel.dataset.reportPanel === 'master' ? 'combined' : panel.dataset.reportPanel;
  const branch = anchor.closest('[data-synthesis-branch]')?.dataset.synthesisBranch
    || panel.dataset.reportBranch
    || '';
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  if (!rect.width && !rect.height) return null;
  return {category, scope, branch, text, rect};
}

function hideSelectionAsk() {
  if (selectionAsk) selectionAsk.hidden = true;
}

function showSelectionAsk() {
  const reference = selectedSynthesis();
  if (!selectionAsk || !reference) {
    pendingSelection = null;
    hideSelectionAsk();
    return;
  }
  pendingSelection = {
    category: reference.category,
    scope: reference.scope,
    branch: reference.branch,
    text: reference.text
  };
  selectionAsk.style.left = `${Math.max(8, Math.min(window.innerWidth - 44, reference.rect.left + reference.rect.width / 2 - 18))}px`;
  selectionAsk.style.top = `${Math.max(8, reference.rect.top - 44)}px`;
  selectionAsk.hidden = false;
}

function openCoworker(category = coworkerCategory, focusMessage = true) {
  if (!coworkerPanel || !coworkerLauncher) return;
  setCoworkerCategory(category);
  if (coworkerScope) coworkerScope.value = coworkerPageScope();
  coworkerPanel.hidden = false;
  document.body.classList.add('coworker-open');
  coworkerLauncher.hidden = true;
  coworkerLauncher.setAttribute('aria-expanded', 'true');
  loadCoworker();
  if (focusMessage) coworkerForm?.querySelector('textarea')?.focus();
}

function collapseCoworker() {
  if (!coworkerPanel || !coworkerLauncher) return;
  coworkerPanel.hidden = true;
  document.body.classList.remove('coworker-open');
  coworkerLauncher.hidden = false;
  coworkerLauncher.setAttribute('aria-expanded', 'false');
  preserveTicketUi();
  coworkerLauncher.focus();
}

function textElement(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value;
  return node;
}

function appendSourceText(node, value) {
  const pattern = /@\[([^\]\n]+)\]:(\d+)/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    node.append(document.createTextNode(value.slice(cursor, match.index)));
    const link = textElement('button', 'source-link', match[0]);
    link.type = 'button';
    link.dataset.sourcePath = match[1];
    link.dataset.sourceLine = match[2];
    link.setAttribute('aria-label', `Open ${match[1]} at line ${match[2]}`);
    const copy = textElement('button', 'source-copy', '');
    copy.type = 'button';
    copy.dataset.sourcePath = match[1];
    copy.dataset.sourceLine = match[2];
    copy.dataset.sourceCopy = '';
    copy.title = 'Copy absolute path';
    copy.setAttribute('aria-label', `Copy absolute path for ${match[1]} at line ${match[2]}`);
    copy.append(textElement('span', 'copy-icon', ''));
    const controls = textElement('span', 'source-reference', '');
    controls.append(link, copy);
    node.append(controls);
    cursor = match.index + match[0].length;
  }
  node.append(document.createTextNode(value.slice(cursor)));
}

function setSourceStatus(message, kind = '') {
  if (!sourceStatus) return;
  sourceStatus.textContent = message;
  sourceStatus.className = `source-status ${kind}`.trim();
  sourceStatus.hidden = !message;
}

async function writeClipboard(value) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch (error) { /* Use the local fallback below. */ }
  const field = document.createElement('textarea');
  field.value = value;
  field.setAttribute('readonly', '');
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.append(field);
  field.select();
  const copied = document.execCommand('copy');
  field.remove();
  if (!copied) throw new Error(`Copy was blocked. ${value}`);
}

function renderProposal(proposal) {
  const article = document.createElement('article');
  article.className = 'coworker-proposal';
  article.dataset.proposalId = proposal.id;
  article.append(textElement('h3', '', proposal.status === 'pending' ? 'Proposed report update' : 'Report update'));
  article.append(textElement(
    'p', 'proposal-note',
    proposal.status === 'pending'
      ? 'Accepting updates the named child reports through the report gate. Combined is regenerated.'
      : `This proposal is ${proposal.status}.`
  ));
  proposal.changes.forEach((change) => {
    const section = document.createElement('section');
    section.className = 'proposal-change';
    section.append(textElement('div', 'proposal-branch', change.branch));
    section.append(textElement('span', 'proposal-label', 'Current synthesis'));
    section.append(textElement('p', 'proposal-value', change.current_synthesis || 'No current synthesis.'));
    section.append(textElement('span', 'proposal-label', 'Proposed synthesis'));
    const proposed = textElement('p', 'proposal-value', change.proposed_synthesis);
    proposed.dataset.proposalCopy = change.branch;
    section.append(proposed);
    const edit = document.createElement('textarea');
    edit.className = 'proposal-edit';
    edit.dataset.proposalEdit = change.branch;
    edit.value = change.proposed_synthesis;
    edit.hidden = true;
    edit.setAttribute('aria-label', `Edit proposed ${proposal.category} synthesis for ${change.branch}`);
    section.append(edit);
    article.append(section);
  });
  if (proposal.status === 'pending') {
    const actions = document.createElement('div');
    actions.className = 'proposal-actions';
    const accept = textElement('button', 'primary', 'Accept update');
    accept.type = 'button';
    accept.dataset.proposalAccept = proposal.id;
    const edit = textElement('button', '', 'Edit proposal');
    edit.type = 'button';
    edit.dataset.proposalEditToggle = proposal.id;
    const dismiss = textElement('button', '', 'Dismiss');
    dismiss.type = 'button';
    dismiss.dataset.proposalDismiss = proposal.id;
    actions.append(accept, edit, dismiss);
    article.append(actions);
  }
  return article;
}

function renderCoworker(value) {
  if (!coworkerMessages || !coworkerScope) return;
  const priorScope = coworkerScope.value;
  coworkerScope.replaceChildren(...value.scopes.map((scope) => {
    const option = document.createElement('option');
    option.value = scope.value;
    option.textContent = scope.label;
    return option;
  }));
  coworkerScope.value = value.scopes.some((scope) => scope.value === priorScope) ? priorScope : value.scope;
  coworkerMessages.replaceChildren();
  if (!value.messages.length) {
    coworkerMessages.append(textElement('p', 'coworker-empty', 'Ask about this ticket or correct its synthesis.'));
  }
  value.messages.forEach((message) => {
    const article = document.createElement('article');
    article.className = 'chat-message';
    article.append(textElement('span', 'chat-role', message.role === 'user' ? 'You' : 'Coworker'));
    if (message.reference?.text) {
      const reference = textElement('blockquote', 'chat-reference', '');
      reference.append(textElement('strong', '', `${referenceLabel(message.reference)}\n`));
      appendSourceText(reference, message.reference.text);
      article.append(reference);
    }
    const copy = textElement('p', 'chat-copy', '');
    appendSourceText(copy, message.text);
    article.append(copy);
    coworkerMessages.append(article);
    if (message.proposal) {
      const proposal = value.proposals.find((item) => item.id === message.proposal);
      if (proposal) coworkerMessages.append(renderProposal(proposal));
    }
  });
  coworkerSend.disabled = !value.ready;
  if (!value.ready) setCoworkerStatus([value.readiness.summary, value.readiness.action].filter(Boolean).join(' Next: '), 'error');
  else setCoworkerStatus('');
  coworkerMessages.scrollTop = coworkerMessages.scrollHeight;
}

async function loadCoworker() {
  if (!coworkerPanel || !coworkerScope) return;
  setCoworkerStatus('Loading this ticket conversation…');
  const query = new URLSearchParams({
    project: coworkerPanel.dataset.project || '',
    ticket: coworkerPanel.dataset.ticket,
    category: coworkerCategory,
    scope: coworkerScope.value || 'combined'
  });
  try {
    renderCoworker(await jsonRequest(`/coworker/state?${query}`));
  } catch (error) {
    setCoworkerStatus(error.message, 'error');
  }
}

coworkerLauncher?.addEventListener('click', () => openCoworker());
coworkerCollapse?.addEventListener('click', collapseCoworker);
document.querySelectorAll('[data-coworker-category]').forEach((button) => {
  button.addEventListener('click', () => {
    setCoworkerReference(null);
    openCoworker(button.dataset.coworkerCategory);
  });
});
coworkerTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => {
    setCoworkerReference(null);
    setCoworkerCategory(tab.dataset.coworkerTab);
    loadCoworker();
  });
  tab.addEventListener('keydown', (event) => {
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % coworkerTabs.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + coworkerTabs.length) % coworkerTabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = coworkerTabs.length - 1;
    if (next !== null) {
      event.preventDefault();
      setCoworkerReference(null);
      setCoworkerCategory(coworkerTabs[next].dataset.coworkerTab, true);
      loadCoworker();
    }
  });
});
coworkerScope?.addEventListener('change', () => { setCoworkerReference(null); loadCoworker(); });
reportSelect?.addEventListener('change', () => {
  if (coworkerScope && coworkerPanel && !coworkerPanel.hidden) {
    setCoworkerReference(null);
    coworkerScope.value = coworkerPageScope();
    loadCoworker();
  }
});
coworkerReferenceRemove?.addEventListener('click', () => {
  setCoworkerReference(null);
  coworkerForm?.querySelector('textarea')?.focus();
});
selectionAsk?.addEventListener('pointerdown', (event) => event.preventDefault());
selectionAsk?.addEventListener('click', () => {
  if (!pendingSelection) return;
  setCoworkerReference(pendingSelection);
  const category = pendingSelection.category;
  hideSelectionAsk();
  window.getSelection()?.removeAllRanges();
  openCoworker(category);
});
document.addEventListener('mouseup', showSelectionAsk);
document.addEventListener('keyup', (event) => {
  if (event.key === 'Shift' || event.key.startsWith('Arrow')) showSelectionAsk();
});
document.addEventListener('touchend', () => window.setTimeout(showSelectionAsk, 0));
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && coworkerPanel && !coworkerPanel.hidden) collapseCoworker();
});
document.addEventListener('click', async (event) => {
  const control = event.target.closest('[data-source-path][data-source-line]');
  if (!control) return;
  const token = viewerToken || coworkerPanel?.dataset.token || truthStrip?.dataset.token || '';
  if (!token) return;
  control.disabled = true;
  const payload = {path: control.dataset.sourcePath, line: control.dataset.sourceLine};
  const copying = control.hasAttribute('data-source-copy');
  setSourceStatus(
    copying
      ? `Copying ${control.dataset.sourcePath}:${control.dataset.sourceLine}…`
      : `Opening ${control.dataset.sourcePath}:${control.dataset.sourceLine}…`
  );
  try {
    const value = await jsonRequest(copying ? '/source/resolve' : '/source/open', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-TicketBunker-Token': token},
      body: JSON.stringify(payload)
    });
    if (copying) {
      await writeClipboard(value.reference);
      setSourceStatus('Absolute path copied.', 'success');
      window.setTimeout(() => setSourceStatus(''), 2200);
      return;
    }
    if (value.mode === 'builtin') {
      const query = new URLSearchParams({
        path: value.path,
        line: String(value.line),
        return: document.documentElement.dataset.ticketReturn
          || `${location.pathname}${location.search}${location.hash}`
      });
      preserveTicketUi();
      location.assign(`/source?${query}`);
      return;
    }
    setSourceStatus(`Opened ${value.path}:${value.line} in ${value.editor}.`);
    window.setTimeout(() => setSourceStatus(''), 2600);
  } catch (error) {
    setSourceStatus(error.message, 'error');
  } finally {
    control.disabled = false;
  }
});

coworkerForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = new FormData(coworkerForm).get('message').trim();
  if (!message) return;
  coworkerBusy = true;
  coworkerSend.disabled = true;
  setCoworkerStatus(`Coworker is reading ${coworkerScope.value === 'combined' ? 'Combined' : 'this branch'}…`);
  try {
    const value = await jsonRequest('/coworker/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-TicketBunker-Token': coworkerPanel.dataset.token},
      body: JSON.stringify({
        project: coworkerPanel.dataset.project || '',
        ticket: coworkerPanel.dataset.ticket,
        category: coworkerCategory,
        scope: coworkerScope.value,
        message,
        reference: coworkerReference
      })
    });
    coworkerForm.reset();
    setCoworkerReference(null);
    renderCoworker(value);
  } catch (error) {
    setCoworkerStatus(error.message, 'error');
    coworkerSend.disabled = false;
  } finally {
    coworkerBusy = false;
  }
});

coworkerMessages?.addEventListener('click', async (event) => {
  const toggle = event.target.closest('[data-proposal-edit-toggle]');
  if (toggle) {
    const proposal = toggle.closest('[data-proposal-id]');
    proposal.querySelectorAll('[data-proposal-edit]').forEach((field) => { field.hidden = !field.hidden; });
    proposal.querySelectorAll('[data-proposal-copy]').forEach((copy) => { copy.hidden = !copy.hidden; });
    toggle.textContent = toggle.textContent === 'Edit proposal' ? 'Keep proposal' : 'Edit proposal';
    proposal.querySelector('[data-proposal-edit]:not([hidden])')?.focus();
    return;
  }
  const accept = event.target.closest('[data-proposal-accept]');
  const dismiss = event.target.closest('[data-proposal-dismiss]');
  if (!accept && !dismiss) return;
  const proposalId = (accept || dismiss).dataset.proposalAccept || (accept || dismiss).dataset.proposalDismiss;
  const article = (accept || dismiss).closest('[data-proposal-id]');
  article.querySelectorAll('button').forEach((button) => { button.disabled = true; });
  const action = accept ? 'accept' : 'dismiss';
  coworkerBusy = true;
  setCoworkerStatus(accept ? 'Checking the child report gate…' : 'Dismissing proposal…');
  const edits = {};
  article.querySelectorAll('[data-proposal-edit]').forEach((field) => { edits[field.dataset.proposalEdit] = field.value; });
  try {
    const result = await jsonRequest(`/coworker/proposal/${action}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-TicketBunker-Token': coworkerPanel.dataset.token},
      body: JSON.stringify({
        project: coworkerPanel.dataset.project || '',
        ticket: coworkerPanel.dataset.ticket,
        proposal: proposalId,
        edits
      })
    });
    if (accept) {
      setCoworkerStatus(
        result.publication_warning || 'Child report updated. Combined was regenerated.',
        result.publication_warning ? 'error' : 'success'
      );
      preserveTicketUi();
      window.setTimeout(() => location.reload(), result.publication_warning ? 2200 : 700);
    } else {
      await loadCoworker();
    }
  } catch (error) {
    setCoworkerStatus(error.message, 'error');
    article.querySelectorAll('button').forEach((button) => { button.disabled = false; });
  } finally {
    coworkerBusy = false;
  }
});

if (coworkerPanel) {
  try {
    const key = `tb-coworker:${coworkerPanel.dataset.project || 'local'}:${coworkerPanel.dataset.ticket}`;
    const saved = JSON.parse(sessionStorage.getItem(key) || '{}');
    if (saved.category && coworkerTabs.some((tab) => tab.dataset.coworkerTab === saved.category)) {
      setCoworkerCategory(saved.category);
    }
    if (saved.scope && coworkerScope?.querySelector(`option[value="${CSS.escape(saved.scope)}"]`)) {
      coworkerScope.value = saved.scope;
    }
    if (typeof saved.draft === 'string' && coworkerForm?.querySelector('textarea[name="message"]')) {
      coworkerForm.querySelector('textarea[name="message"]').value = saved.draft;
    }
    if (saved.reference?.text) setCoworkerReference(saved.reference);
    if (saved.expanded || saved.open) openCoworker(coworkerCategory, false);
  } catch (error) { /* Ignore malformed local UI state. */ }
}

setInterval(async () => {
  if (truthStrip) {
    await pollTruth();
    return;
  }
  if (!fp) return;
  try {
    const response = await fetch('/poll');
    if ((await response.text()).trim() !== fp) location.reload();
  } catch (error) { /* Keep the current repository snapshot if the server exits. */ }
}, 2000);
"""


_TAG_RE = re.compile(r"^\[(observation|real|speculation)\]\s*")
_TAG_CLASS = {"observation": "obs", "real": "real", "speculation": "spec"}
_CHAT_ICON = (
    '<svg class="chat-icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M7.5 18.5 4 20l1-4.1A7.5 7.5 0 1 1 7.5 18.5Z"/>'
    '<path d="M8 9.5h8M8 13h5"/></svg>'
)
_STATUS_ORDER = ("backlog", "open", "todo", "in-progress", "in-review", "done", "closed")
_STATUS_LABELS = {
    "backlog": "Backlog",
    "open": "Open",
    "todo": "To do",
    "in-progress": "In progress",
    "in-review": "In review",
    "done": "Done",
    "closed": "Closed",
}


def fingerprint(root: Path) -> str:
    """A cheap data-file fingerprint used only to trigger browser reloads."""
    digest = hashlib.sha256()
    patterns = ("ticket.json", "ledger.jsonl", "observations/*.json", "reports/*.md", "reports/branches/*.md")
    try:
        catalog = TicketCatalog(root)
        bases = [catalog.database(key).bunkers for key in catalog.project_keys()]
    except (FileNotFoundError, KeyError, ValueError):
        bases = [layout.bunkers_dir(root)]
    for base in bases:
        for bunker in layout.ticket_dirs(base):
            for pattern in patterns:
                for path in sorted(bunker.glob(pattern)):
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    digest.update(f"{path}:{stat.st_mtime_ns}:{stat.st_size};".encode())
    return digest.hexdigest()[:16]


def _inline(escaped: str) -> str:
    source_references: list[str] = []

    def source_reference(match: re.Match[str]) -> str:
        path = html.unescape(match.group(1)).strip()
        line = match.group(2)
        label = f"@[{path}]:{line}" if match.group(0).startswith("@[") else f"{path}:{line}"
        placeholder = f"\x1e{len(source_references)}\x1f"
        source_references.append(
            '<span class="source-reference"><button class="source-link" type="button" '
            f'data-source-path="{html.escape(path, quote=True)}" data-source-line="{line}" '
            f'aria-label="Open {html.escape(path, quote=True)} at line {line}">'
            f'{html.escape(label)}</button>'
            '<button class="source-copy" type="button" data-source-copy '
            f'data-source-path="{html.escape(path, quote=True)}" data-source-line="{line}" '
            f'aria-label="Copy absolute path for {html.escape(path, quote=True)} at line {line}" '
            'title="Copy absolute path"><span class="copy-icon" aria-hidden="true"></span>'
            '</button></span>'
        )
        return placeholder

    escaped = re.sub(r"@\[([^\]\n]+)\]:(\d+)", source_reference, escaped)
    escaped = re.sub(
        r"`([A-Za-z0-9_.-]*/[A-Za-z0-9_./-]+|[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}):(\d+)`",
        source_reference,
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*", r"<em>\1</em>", escaped)

    def link(match: re.Match[str]) -> str:
        url = html.unescape(match.group(2))
        if not url.startswith(("https://", "http://")):
            return match.group(0)
        return (
            f'<a href="{match.group(2)}" rel="noreferrer">{match.group(1)}</a>'
        )

    rendered = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, escaped)
    for index, source in enumerate(source_references):
        rendered = rendered.replace(f"\x1e{index}\x1f", source)
    return rendered


def _claim(escaped: str) -> str:
    match = _TAG_RE.match(escaped)
    if not match:
        return _inline(escaped)
    tag = match.group(1)
    label = "speculation" if tag == "speculation" else tag
    return (
        f'<span class="tag {_TAG_CLASS[tag]}">{label}</span> '
        + _inline(escaped[match.end():])
    )


def _render_markdown(text: str, *, claims: bool, issue_copy: bool = False) -> str:
    """Render TicketBunker's safe markdown subset after escaping input."""
    output: list[str] = []
    list_stack: list[dict[str, Any]] = []
    code_lines: list[str] | None = None

    def close_list() -> None:
        while list_stack:
            current = list_stack.pop()
            if current["item_open"]:
                output[-1] += "</li>"
            output.append(f'</{current["tag"]}>')

    def inline(value: str) -> str:
        return _claim(value) if claims else _inline(value)

    def list_item(indent: int, tag: str, value: str) -> None:
        while list_stack and indent < list_stack[-1]["indent"]:
            current = list_stack.pop()
            if current["item_open"]:
                output[-1] += "</li>"
            output.append(f'</{current["tag"]}>')
        if list_stack and indent == list_stack[-1]["indent"]:
            if list_stack[-1]["tag"] == tag:
                if list_stack[-1]["item_open"]:
                    output[-1] += "</li>"
            else:
                current = list_stack.pop()
                if current["item_open"]:
                    output[-1] += "</li>"
                output.append(f'</{current["tag"]}>')
                output.append(f"<{tag}>")
                list_stack.append({"indent": indent, "tag": tag, "item_open": False})
        elif not list_stack or indent > list_stack[-1]["indent"]:
            output.append(f"<{tag}>")
            list_stack.append({"indent": indent, "tag": tag, "item_open": False})
        output.append(f"<li>{inline(value)}")
        list_stack[-1]["item_open"] = True

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            close_list()
            if code_lines is None:
                code_lines = []
            else:
                code = "\n".join(html.escape(value) for value in code_lines)
                output.append(f"<pre><code>{code}</code></pre>")
                code_lines = None
            continue
        if code_lines is not None:
            code_lines.append(raw)
            continue
        if line.startswith("<!--"):
            continue
        escaped = html.escape(line)
        item = re.match(r"^([ \t]*)([-*+]|\d+\.)\s+(.*)$", line)
        if not line.strip():
            close_list()
        elif line.startswith("### "):
            close_list()
            tag = "h4" if issue_copy else "h3"
            output.append(f"<{tag}>{_inline(escaped[4:])}</{tag}>")
        elif line.startswith("## "):
            close_list()
            tag = "h3" if issue_copy else "h2"
            output.append(f"<{tag}>{_inline(escaped[3:])}</{tag}>")
        elif line.startswith("# "):
            close_list()
            tag = "h3" if issue_copy else "h2"
            output.append(f"<{tag}>{_inline(escaped[2:])}</{tag}>")
        elif line.strip() == "---":
            close_list()
            output.append("<hr>")
        elif item:
            indent = len(item.group(1).expandtabs(4))
            tag = "ol" if item.group(2)[0].isdigit() else "ul"
            list_item(indent, tag, html.escape(item.group(3)))
        else:
            close_list()
            output.append(f"<p>{inline(escaped)}</p>")
    if code_lines is not None:
        code = "\n".join(html.escape(value) for value in code_lines)
        output.append(f"<pre><code>{code}</code></pre>")
    close_list()
    return "\n".join(output)


def render_report(text: str) -> str:
    """Render report markdown with provenance claim chips."""
    return _render_markdown(text, claims=True)


def render_ticket_text(text: str) -> str:
    """Render tracker-authored rich text without treating claim words as provenance."""
    return _render_markdown(text, claims=False, issue_copy=True)


def _page(title: str, body: str, fp: str) -> bytes:
    document = (
        f'<!doctype html><html lang="en" data-fp="{fp}" data-viewer-token=""><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<link rel="icon" href="data:,">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main><script>{_POLL_JS}</script></body></html>"
    )
    return document.encode("utf-8")


_SOURCE_JS = r"""
const source = document.querySelector('[data-source-input]');
const gutter = document.querySelector('[data-source-gutter]');
const save = document.querySelector('[data-source-save]');
const status = document.querySelector('[data-source-save-state]');
const position = document.querySelector('[data-source-position]');
const sourceSelectionAsk = document.querySelector('[data-source-selection-ask]');
let digest = source.dataset.digest;
let savedContent = source.value;
let saving = false;

function lineCount() { return Math.max(1, source.value.split('\n').length); }
function updateGutter() {
  gutter.textContent = Array.from({length: lineCount()}, (_, index) => index + 1).join('\n');
}
function updatePosition() {
  const before = source.value.slice(0, source.selectionStart);
  const lines = before.split('\n');
  position.textContent = `Ln ${lines.length}, Col ${lines.at(-1).length + 1}`;
}
function selectedSource() {
  const start = source.selectionStart;
  const end = source.selectionEnd;
  if (start === end) return null;
  const text = source.value.slice(start, end);
  const lineStart = source.value.slice(0, start).split('\n').length;
  const lineEnd = source.value.slice(0, Math.max(start, end - 1)).split('\n').length;
  return {text, lineStart, lineEnd};
}
function updateSourceSelection() {
  if (!sourceSelectionAsk) return;
  const selection = selectedSource();
  sourceSelectionAsk.hidden = !selection;
  if (!selection) return;
  const tooLarge = selection.text.trim().length > Number(sourceSelectionAsk.dataset.maxChars || 2400);
  sourceSelectionAsk.disabled = tooLarge;
  sourceSelectionAsk.title = tooLarge
    ? 'Select fewer than 2,400 characters'
    : `Ask the ticket coworker about ${selection.lineStart === selection.lineEnd ? 'this line' : 'these lines'}`;
}
function updateDirty() {
  const dirty = source.value !== savedContent;
  status.textContent = dirty ? 'Unsaved' : 'Saved';
  status.className = `source-save-state${dirty ? ' dirty' : ''}`;
  save.disabled = !dirty || saving;
  updateGutter(); updatePosition();
}
function requestMessage(value, fallback) {
  return [value.message || fallback, value.state || '', value.action ? `Next: ${value.action}` : '']
    .filter(Boolean).join(' ');
}
function selectedLines() {
  const start = source.selectionStart;
  const end = source.selectionEnd;
  const blockStart = source.value.lastIndexOf('\n', Math.max(0, start - 1)) + 1;
  let blockEnd = source.value.indexOf('\n', end);
  if (blockEnd < 0) blockEnd = source.value.length;
  return {start, end, blockStart, blockEnd};
}
function handleTab(event) {
  if (event.key !== 'Tab') return;
  event.preventDefault();
  const tabSize = Number(source.dataset.tabSize || 4);
  const unit = source.dataset.indentStyle === 'tabs' ? '\t' : ' '.repeat(tabSize);
  const range = selectedLines();
  if (range.start !== range.end || source.value.slice(range.blockStart, range.blockEnd).includes('\n')) {
    const block = source.value.slice(range.blockStart, range.blockEnd);
    const lines = block.split('\n');
    let changed;
    if (event.shiftKey) {
      changed = lines.map(line => line.startsWith('\t') ? line.slice(1) : line.replace(new RegExp(`^ {1,${tabSize}}`), '')).join('\n');
    } else {
      changed = lines.map(line => unit + line).join('\n');
    }
    source.setRangeText(changed, range.blockStart, range.blockEnd, 'select');
    source.selectionStart = range.blockStart;
    source.selectionEnd = range.blockStart + changed.length;
  } else if (event.shiftKey) {
    const prefix = source.value.slice(range.blockStart, range.start);
    const removable = prefix.endsWith('\t') ? 1 : Math.min(tabSize, (prefix.match(/ +$/) || [''])[0].length);
    if (removable) source.setRangeText('', range.start - removable, range.start, 'end');
  } else {
    const column = range.start - range.blockStart;
    const inserted = source.dataset.indentStyle === 'tabs' ? '\t' : ' '.repeat(tabSize - (column % tabSize));
    source.setRangeText(inserted, range.start, range.end, 'end');
  }
  updateDirty();
}
async function saveSource() {
  if (saving || source.value === savedContent) return;
  saving = true; save.disabled = true;
  status.textContent = 'Saving…'; status.className = 'source-save-state';
  try {
    const response = await fetch('/source/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-TicketBunker-Token': viewerToken},
      body: JSON.stringify({path: source.dataset.path, content: source.value, expected_digest: digest})
    });
    let value = {};
    try { value = await response.json(); } catch (error) { value = {}; }
    if (!response.ok) throw new Error(requestMessage(value, 'The file could not be saved.'));
    source.value = value.content;
    digest = value.digest;
    savedContent = value.content;
    status.textContent = value.saved ? 'Saved' : 'No changes';
    status.className = 'source-save-state';
    updateGutter(); updatePosition();
  } catch (error) {
    status.textContent = error.message;
    status.className = 'source-save-state error';
  } finally {
    saving = false;
    save.disabled = source.value === savedContent;
  }
}

source.addEventListener('input', updateDirty);
source.addEventListener('click', updatePosition);
source.addEventListener('click', updateSourceSelection);
source.addEventListener('keyup', () => { updatePosition(); updateSourceSelection(); });
source.addEventListener('select', updateSourceSelection);
source.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
    event.preventDefault(); saveSource(); return;
  }
  handleTab(event);
});
source.addEventListener('scroll', () => { gutter.scrollTop = source.scrollTop; });
save.addEventListener('click', saveSource);
sourceSelectionAsk?.addEventListener('click', () => {
  const selection = selectedSource();
  if (!selection || !coworkerPanel) return;
  setCoworkerReference({
    kind: 'source',
    category: coworkerCategory,
    scope: coworkerScope?.value || 'combined',
    branch: '',
    path: source.dataset.path,
    line_start: selection.lineStart,
    line_end: selection.lineEnd,
    unsaved: source.value !== savedContent,
    text: selection.text
  });
  openCoworker(coworkerCategory);
  preserveTicketUi();
});
window.addEventListener('beforeunload', event => {
  if (source.value === savedContent) return;
  event.preventDefault(); event.returnValue = '';
});

updateGutter(); updateDirty(); updateSourceSelection();
const targetLine = Math.max(1, Number(source.dataset.line || 1));
let offset = 0;
for (let line = 1; line < targetLine; line += 1) {
  const next = source.value.indexOf('\n', offset);
  if (next < 0) break;
  offset = next + 1;
}
source.focus(); source.setSelectionRange(offset, offset);
requestAnimationFrame(() => {
  const lineHeight = parseFloat(getComputedStyle(source).lineHeight) || 20;
  source.scrollTop = Math.max(0, (targetLine - 3) * lineHeight);
  gutter.scrollTop = source.scrollTop;
  updatePosition();
});
"""


def _source_ticket_context(
    root: Path, return_to: str
) -> tuple[TicketSnapshot | None, str]:
    path = urlsplit(return_to).path
    project_match = re.fullmatch(
        r"/project/([A-Z][A-Z0-9]*)/ticket/([A-Za-z0-9._-]+)", path
    )
    ticket_match = re.fullmatch(r"/ticket/([A-Za-z0-9._-]+)", path)
    try:
        if project_match:
            project_key, ticket_id = project_match.groups()
            return TicketCatalog(root).get_ticket(project_key, ticket_id), project_key
        if ticket_match:
            return TicketDatabase(root).get_ticket(ticket_match.group(1)), ""
    except (commands.CommandRejected, FileNotFoundError, KeyError, ValueError):
        pass
    return None, ""


def page_source(
    root: Path,
    path: str,
    line: int,
    return_to: str = "/",
    work_token: str = "",
) -> bytes:
    value = commands.dispatch(
        "editor.read",
        {"path": path, "line": line},
        commands.CommandContext(root, transport="http"),
    )
    settings = value["settings"]
    back = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/"
    if back.startswith("/source"):
        back = "/"
    ticket, project_key = _source_ticket_context(root, back)
    coworker_html = (
        _coworker_surface(root, ticket, project_key, work_token)
        if ticket is not None and work_token
        else ""
    )
    if ticket is not None:
        back_label = f"Back to {ticket.id}"
    else:
        back_label = "Back to tickets"
    selection_action = (
        '<button class="source-selection-ask" type="button" data-source-selection-ask '
        f'data-max-chars="{coworker.MAX_REFERENCE_CHARS}" hidden>'
        f'{_CHAT_ICON}<span>Ask about selection</span></button>'
        if coworker_html
        else ""
    )
    wrap = "soft" if settings["line_wrap"] else "off"
    indent = "Tabs" if settings["indent_style"] == "tabs" else f'{settings["tab_size"]} spaces'
    cleanup = []
    if settings["trim_trailing_whitespace"]:
        cleanup.append("trim whitespace")
    if settings["ensure_final_newline"]:
        cleanup.append("final newline")
    cleanup_label = ", ".join(cleanup) if cleanup else "no save cleanup"
    document = (
        '<!doctype html><html lang="en" data-viewer-token="" '
        f'data-ticket-return="{html.escape(back, quote=True)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<link rel="icon" href="data:,">'
        f'<title>{html.escape(value["path"])} | TicketBunker</title><style>{_STYLE}</style></head>'
        '<body class="source-body"><main class="source-shell">'
        '<header class="source-toolbar">'
        f'<a class="source-back" href="{html.escape(back, quote=True)}">'
        f'<span aria-hidden="true">←</span> {html.escape(back_label)}</a>'
        '<div class="source-file">'
        f'<span class="source-path">{html.escape(value["path"])}</span>'
        f'<span class="source-branch">{html.escape(value["branch"] or "Detached checkout")}</span></div>'
        '<div class="source-actions">'
        '<span class="source-save-state" data-source-save-state role="status" aria-live="polite">Saved</span>'
        '<button class="source-save" type="button" data-source-save disabled>Save</button></div></header>'
        '<div class="source-workspace">'
        '<pre class="source-gutter" data-source-gutter aria-hidden="true"></pre>'
        f'<textarea class="source-input" data-source-input data-path="{html.escape(value["path"], quote=True)}" '
        f'data-line="{value["line"]}" data-digest="{value["digest"]}" '
        f'data-indent-style="{html.escape(settings["indent_style"], quote=True)}" '
        f'data-tab-size="{settings["tab_size"]}" style="tab-size:{settings["tab_size"]}" '
        f'wrap="{wrap}" spellcheck="false" '
        f'aria-label="Edit {html.escape(value["path"], quote=True)}">{html.escape(value["content"])}</textarea></div>'
        '<footer class="source-footer">'
        '<div class="source-footer-tools">'
        f'<span>{html.escape(indent)} · {html.escape(cleanup_label)}</span>{selection_action}</div>'
        '<span class="source-position" data-source-position>Ln 1, Col 1</span></footer>'
        f'</main>{coworker_html}<script>{_POLL_JS}{_SOURCE_JS}</script></body></html>'
    )
    return document.encode("utf-8")


def _date(value: Any) -> str:
    text = str(value or "")
    return text[:10] if text else ""


def _datetime(value: Any) -> str:
    text = str(value or "")
    try:
        import datetime

        parsed = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return text
    return parsed.strftime("%d %b %Y, %H:%M")


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("-", " ").capitalize())


def _badge(label: str, kind: str = "") -> str:
    classes = f"badge {kind}".strip()
    return f'<span class="{html.escape(classes, quote=True)}">{html.escape(label)}</span>'


def _report_slug_anchor(slug: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]", "-", slug)
    return f"report-{clean}"


def _report_anchor(report: ReportSnapshot) -> str:
    return _report_slug_anchor(report.slug)


def _summary_markdown(text: str, limit: int = 1) -> str:
    """Select a compact claim preview without rewriting report content."""
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    selected = lines[:limit]
    if not selected:
        selected = [line for line in text.splitlines() if line.strip()][:limit]
    return "\n".join(selected)


def _brief_claims(text: str) -> str:
    claims: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith(("- ", "* ")):
            if current:
                claims.append(" ".join(current))
            current = [line[2:].strip()]
        elif current and line.strip() and not line.startswith("#"):
            current.append(line.strip())
        elif current:
            claims.append(" ".join(current))
            current = []
    if current:
        claims.append(" ".join(current))
    return "".join(f"<p>{_claim(html.escape(claim))}</p>" for claim in claims)


def _report_section(report: ReportSnapshot, label: str) -> str:
    return {
        "Who": report.who,
        "What": report.what,
        "Where": report.where,
        "Why": report.why,
        "State": report.state_text,
    }[label]


def _category_preview(report: ReportSnapshot, label: str) -> str:
    if report.brief is not None and label != "State":
        value = {
            "Who": report.brief.who,
            "What": report.brief.what,
            "Where": report.brief.where,
            "Why": report.brief.why,
        }[label]
        return _brief_claims(value)
    preview = _summary_markdown(_report_section(report, label))
    return _brief_claims(preview)


def _report_state_badge(report: ReportSnapshot) -> str:
    state = report.state
    if state == "verified":
        return _badge("Verified", "verified")
    if state == "unverified":
        return _badge("Unverified", "unverified")
    return _badge(state.capitalize(), "stale")


def _generation_label(report: ReportSnapshot) -> str:
    identity = report.agent_model or report.agent_provider
    if report.agent_model and report.agent_provider:
        identity = f"{report.agent_model} ({report.agent_provider})"
    if identity and report.agent_harness:
        return f"Generated by {identity} via {report.agent_harness}"
    if identity:
        return f"Generated by {identity}"
    if report.agent_harness:
        return f"Generated via {report.agent_harness}"
    return ""


def _report_metadata(report: ReportSnapshot) -> str:
    metadata = []
    if report.owner:
        metadata.append(f"Work by {report.owner}")
    generator = _generation_label(report)
    if generator:
        metadata.append(generator)
    if report.verified_commit:
        metadata.append(report.verified_commit)
    return " · ".join(metadata)


def _category_trail(
    source_reports: tuple[ReportSnapshot, ...],
    label: str,
    summary: str = "Full trail",
) -> str:
    reports = [report for report in source_reports if _report_section(report, label)]
    count = len(reports)
    noun = "report" if count == 1 else "reports"
    rows = []
    for report in reports:
        metadata = _report_metadata(report)
        metadata_html = (
            f'<span class="trail-report-meta">{html.escape(metadata)}</span>' if metadata else ""
        )
        rows.append(
            '<article class="trail-report">'
            '<div class="trail-report-head">'
            f'<div class="trail-report-name mono">{html.escape(report.branch)}'
            f'{metadata_html}</div>'
            f'<div class="trail-report-state">{_report_state_badge(report)}</div></div>'
            f'<div class="trail-copy">{render_report(_report_section(report, label))}</div></article>'
        )
    return (
        f'<details class="category-trail" data-category="{label.casefold()}">'
        f'<summary>{summary}<span class="trail-count">{count} {noun}</span></summary>'
        f'<div class="trail-body">{"".join(rows)}</div></details>'
    )


def _master_preview(reports: tuple[ReportSnapshot, ...], label: str) -> str:
    rows = []
    for report in reports:
        preview = _category_preview(report, label)
        if not preview:
            continue
        rows.append(
            '<article class="master-branch-summary" '
            f'data-synthesis-branch="{html.escape(report.branch, quote=True)}">'
            f'<div class="master-branch-label mono">{html.escape(report.branch)}</div>'
            f'<div class="brief-claims" data-synthesis-copy>{preview}</div></article>'
        )
    return "".join(rows)


def _agent_report_panel(reports: tuple[ReportSnapshot, ...], value: str, *, master: bool) -> str:
    panel_id = "report-master" if master else _report_anchor(reports[0])
    hidden = "" if master else " hidden"
    if not reports:
        return (
            f'<div class="report-panel" id="{panel_id}" data-report-panel="{html.escape(value, quote=True)}"{hidden}>'
            '<p class="empty">No agent reports yet.</p></div>'
        )

    context = ""
    if not master:
        report = reports[0]
        generation = _generation_label(report)
        suffix = f" · {generation}" if generation else ""
        context = (
            f'<p class="report-context"><span class="mono">{html.escape(report.branch)}</span>'
            f'<span class="agent-generation">{html.escape(suffix)}</span></p>'
        )

    categories = []
    for label in ("Who", "What", "Where", "Why"):
        preview = _master_preview(reports, label) if master else _category_preview(reports[0], label)
        synthesis_attr = "" if master else " data-synthesis-copy"
        categories.append(
            f'<section class="work-category" data-synthesis-category="{label.casefold()}">'
            f'<div class="work-category-head"><h3>{label}</h3>'
            f'<button class="coworker-inline" type="button" data-coworker-category="{label.casefold()}" '
            f'aria-label="Ask the ticket coworker about {label}" title="Ask about {label}">{_CHAT_ICON}</button></div>'
            f'<div class="{"master-brief" if master else "brief-claims"}"{synthesis_attr}>{preview}</div>'
            f'{_category_trail(reports, label)}</section>'
        )
    branch_attr = "" if master else f' data-report-branch="{html.escape(reports[0].branch, quote=True)}"'
    return (
        f'<div class="report-panel" id="{panel_id}" data-report-panel="{html.escape(value, quote=True)}"'
        f'{hidden}{branch_attr}>'
        f'{context}{"".join(categories)}<div class="report-state-trail">'
        f'{_category_trail(reports, "State", "Report state")}</div></div>'
    )


def _agent_empty_branch_panel(branch: BranchSnapshot) -> str:
    return (
        f'<div class="report-panel" id="{_report_slug_anchor(branch.slug)}" '
        f'data-report-panel="{html.escape(branch.slug, quote=True)}" '
        f'data-report-branch="{html.escape(branch.name, quote=True)}" hidden>'
        f'<p class="report-context"><span class="mono">{html.escape(branch.name)}</span></p>'
        '<p class="empty">No agent report for this branch yet. Use Synthesize above to create it.</p>'
        '</div>'
    )


def _agent_work(ticket: TicketSnapshot) -> str:
    if not ticket.reports:
        return (
            '<section class="section agent-work"><div class="section-heading agent-heading">'
            '<h2>Agent work</h2></div><p class="empty">No agent reports yet.</p></section>'
        )
    options = ['<option value="master">Combined</option>']
    panels = [_agent_report_panel(ticket.reports, "master", master=True)]
    reports = {report.slug: report for report in ticket.reports}
    included: set[str] = set()
    for branch in ticket.branches:
        report = reports.get(branch.slug)
        options.append(
            f'<option value="{html.escape(branch.slug, quote=True)}">{html.escape(branch.name)}</option>'
        )
        panels.append(
            _agent_report_panel((report,), report.slug, master=False)
            if report
            else _agent_empty_branch_panel(branch)
        )
        included.add(branch.slug)
    for report in ticket.reports:
        if report.slug in included:
            continue
        options.append(
            f'<option value="{html.escape(report.slug, quote=True)}">{html.escape(report.branch)}</option>'
        )
        panels.append(_agent_report_panel((report,), report.slug, master=False))
    return (
        '<section class="section agent-work"><div class="section-heading agent-heading">'
        '<h2>Agent work</h2><label class="report-picker"><span>Report scope</span>'
        f'<select data-report-select aria-label="Agent report">{"".join(options)}</select></label></div>'
        f'{"".join(panels)}</section>'
    )


def _ticket_metadata(data: Mapping[str, Any]) -> Mapping[str, Any]:
    value = data.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _provider_metadata(data: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = _ticket_metadata(data)
    tracker = str(metadata.get("tracker") or "").casefold()
    for key, value in metadata.items():
        if str(key).casefold() == tracker and isinstance(value, Mapping):
            return value
    for key, value in metadata.items():
        if key not in {"project"} and isinstance(value, Mapping):
            return value
    return {}


def _source_timestamp(data: Mapping[str, Any], name: str) -> str:
    metadata = _ticket_metadata(data)
    provider = _provider_metadata(data)
    value = metadata.get(f"source_{name}") or provider.get(f"{name}_at")
    return str(value or data.get(name) or "")


def _project_context(data: Mapping[str, Any]) -> tuple[str, str]:
    metadata = _ticket_metadata(data)
    provider = _provider_metadata(data)
    candidates = (data.get("project"), metadata.get("project"), provider.get("project"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip(), ""
        if isinstance(candidate, Mapping):
            name = str(candidate.get("name") or candidate.get("title") or "").strip()
            if name:
                return name, str(candidate.get("url") or "")
    name = str(data.get("project_name") or "").strip()
    return name, ""


_ACTIVITY_LABELS = {
    "comment": "Comment",
    "history": "History",
    "log": "Log",
    "note": "Note",
    "worklog": "Work log",
}


def _activity(ticket: TicketSnapshot) -> str:
    entries: list[dict[str, Any]] = []
    comments = ticket.data.get("comments") or []
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, Mapping):
                entries.append({**comment, "kind": "comment"})
    activity = ticket.data.get("activity") or []
    if isinstance(activity, list):
        entries.extend(dict(item) for item in activity if isinstance(item, Mapping))
    for key, kind in (("work_history", "history"), ("worklogs", "worklog"), ("logs", "log"), ("notes", "note")):
        values = ticket.data.get(key) or []
        if isinstance(values, list):
            for value in values:
                if isinstance(value, Mapping):
                    entries.append({"kind": kind, **value})
                elif isinstance(value, str) and value.strip():
                    entries.append({"kind": kind, "text": value})
    entries.sort(key=lambda item: str(item.get("when") or item.get("timestamp") or ""), reverse=True)

    rows = []
    for entry in entries:
        text = str(
            entry.get("text")
            or entry.get("body")
            or entry.get("message")
            or entry.get("summary")
            or entry.get("action")
            or ""
        ).strip()
        if not text:
            continue
        kind = str(entry.get("kind") or "activity").casefold()
        label = _ACTIVITY_LABELS.get(kind, kind.replace("-", " ").capitalize())
        author = html.escape(str(entry.get("author") or entry.get("actor") or ""))
        when = html.escape(_datetime(entry.get("when") or entry.get("timestamp")))
        source = str(entry.get("source") or "")
        source_html = ""
        if source.startswith(("https://", "http://")):
            source_html = (
                f' · <a href="{html.escape(source, quote=True)}" rel="noreferrer">Open source</a>'
            )
        author_html = f"<strong>{author}</strong>" if author else ""
        rows.append(
            f'<article class="comment"><div class="comment-head">'
            f'<span class="activity-kind">{html.escape(label)}</span>{author_html}'
            f'<span class="comment-meta">{when}{source_html}</span></div>'
            f'<div class="comment-text rich-text">'
            f'{render_ticket_text(text)}</div></article>'
        )
    if not rows:
        return ""
    return (
        '<section class="section issue-activity"><h2>Activity</h2>'
        f'<div class="comments">{"".join(rows)}</div></section>'
    )


def _lifecycle(ticket: TicketSnapshot) -> str:
    rows = []
    for status in _STATUS_ORDER:
        current = status == ticket.status
        class_name = ' class="current"' if current else ""
        aria = ' aria-current="step"' if current else ""
        rows.append(f'<li{class_name}{aria}>{html.escape(_status_label(status))}</li>')
    return f'<section class="section"><h2>Lifecycle</h2><ol class="lifecycle">{"".join(rows)}</ol></section>'


def _branch_badge(state: str) -> str:
    labels = {
        "merged": "Merged",
        "open": "Open",
        "missing": "Missing",
        "comparison-missing": "Needs Git state",
    }
    kind = state if state in {"merged", "open", "missing"} else ""
    return _badge(labels.get(state, state.capitalize()), kind)


def _work_controls(ticket: TicketSnapshot, token: str) -> tuple[str, str]:
    if not token:
        return "", ""
    button = (
        '<button class="branch-add" type="button" aria-label="Start a branch" '
        'aria-expanded="false" aria-controls="work-panel" data-work-toggle>+</button>'
    )
    panel = (
        '<div class="work-panel" id="work-panel" data-work-panel hidden>'
        '<h3>Start work</h3>'
        f'<form class="work-form" data-work-form data-ticket="{html.escape(ticket.id, quote=True)}" '
        f'data-token="{html.escape(token, quote=True)}">'
        '<label class="work-field">Short branch name'
        '<input name="name" autocomplete="off" placeholder="position-system" required></label>'
        '<label class="work-field">Branch from<select name="base" data-work-base required>'
        '<option value="">Loading…</option></select></label>'
        '<button class="work-submit" type="submit" data-work-submit disabled>Create and switch</button>'
        '<p class="work-state" data-work-status aria-live="polite"></p></form></div>'
    )
    return button, panel


def _branches(ticket: TicketSnapshot, work_token: str = "") -> str:
    button, panel = _work_controls(ticket, work_token)
    heading = f'<div class="branch-heading"><h2>Branches</h2>{button}</div>{panel}'
    if not ticket.branches:
        return f'<section class="section">{heading}<p class="empty">No branches.</p></section>'
    reports = {report.slug: report for report in ticket.reports}
    rows = []
    for branch in ticket.branches:
        report = reports.get(branch.slug)
        selectable = bool(ticket.reports)
        tag = "a" if selectable else "div"
        anchor = _report_anchor(report) if report else _report_slug_anchor(branch.slug)
        href = f' href="#{anchor}"' if selectable else ""
        ahead = "?" if branch.ahead is None else branch.ahead
        behind = "?" if branch.behind is None else branch.behind
        development = _date(branch.last_development)
        date_text = f" · {development}" if development else ""
        if branch.state == "comparison-missing":
            branch_meta = "Open this branch to generate its Git comparison"
        elif branch.state == "missing":
            branch_meta = "Not in local or fetched refs"
        else:
            branch_meta = f"ahead {ahead} · behind {behind}{date_text}"
        rows.append(
            f'<{tag} class="branch-row"{href}><span class="branch-top">'
            f'<span class="branch-name mono">{html.escape(branch.name)}</span>'
            f'{_branch_badge(branch.state)}</span>'
            f'<span class="branch-meta">{html.escape(branch_meta)}</span></{tag}>'
        )
    return (
        f'<section class="section">{heading}'
        f'<div class="branch-list">{"".join(rows)}</div></section>'
    )


_FIELD_LABELS = {
    "assignee": "Assignee",
    "created": "Created",
    "github": "GitHub",
    "gitlab": "GitLab",
    "id": "ID",
    "issue_node_id": "Issue node ID",
    "item_id": "Item ID",
    "jira": "Jira",
    "labels": "Labels",
    "people_of_interest": "People",
    "priority": "Priority",
    "references": "Linked tickets",
    "repo": "Repository",
    "reporter": "Reporter",
    "source_repository": "Source repository",
    "source_url": "Source",
    "type": "Type",
    "updated": "Updated",
    "url": "URL",
}


def _field_label(path: tuple[str, ...]) -> str:
    labels = []
    for key in path:
        labels.append(_FIELD_LABELS.get(key.casefold(), key.replace("_", " ").capitalize()))
    return " · ".join(labels)


def _flatten_value(path: tuple[str, ...], value: Any) -> list[tuple[tuple[str, ...], Any]]:
    if isinstance(value, Mapping):
        flattened: list[tuple[tuple[str, ...], Any]] = []
        for key in sorted(value, key=lambda item: str(item).casefold()):
            flattened.extend(_flatten_value((*path, str(key)), value[key]))
        return flattened
    if value in (None, "") or value == [] or value == ():
        return []
    return [(path, value)]


def _field_role(path: tuple[str, ...]) -> str:
    """Classify source fields by meaning, independent of any provider name."""
    lowered = tuple(part.casefold() for part in path)
    leaf = lowered[-1]
    in_project = "project" in lowered[:-1]
    if leaf in {"source_title", "source_created", "source_updated", "created_at", "updated_at"}:
        return "hidden"
    if in_project and leaf in {"name", "number", "title", "url"}:
        return "hidden"
    if in_project and "fields" in lowered and leaf == "status":
        return "hidden"
    if leaf.endswith("_id") or leaf in {
        "author_association", "closed_at", "issue_number", "issue_state", "locked",
        "node_id", "state_reason",
    }:
        return "metadata"
    if in_project and leaf == "owner":
        return "metadata"
    if len(lowered) > 1 and leaf in {"state", "status"}:
        return "metadata"
    return "detail"


def _display_path(path: tuple[str, ...]) -> tuple[str, ...]:
    if len(path) > 1 and path[-1].casefold() == "repository":
        return ("source_repository",)
    return path


def _field_value(path: tuple[str, ...], value: Any) -> str:
    key = path[-1].casefold()
    if key in {"created", "updated"} or key.endswith("_at"):
        value = _datetime(value)
    if isinstance(value, (list, tuple)):
        if key == "references":
            links = (
                f'<a class="field-token mono" href="/ticket/{html.escape(str(item), quote=True)}">'
                f'{html.escape(str(item))}</a>'
                for item in value
            )
            return f'<span class="field-values">{"".join(links)}</span>'
        tokens = "".join(
            f'<span class="field-token">{html.escape(str(item))}</span>' for item in value
        )
        return f'<span class="field-values">{tokens}</span>'
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return f'<span class="mono">{html.escape(json.dumps(value, ensure_ascii=False, sort_keys=True))}</span>'
    text = str(value)
    if key in {"priority", "type"}:
        text = text.replace("-", " ").capitalize()
    if key == "tracker":
        text = {"github": "GitHub", "gitlab": "GitLab", "jira": "Jira"}.get(
            text.casefold(), text
        )
    if text.startswith(("https://", "http://")):
        label = "Open source" if key == "source_url" else "Open link"
        return (
            f'<a href="{html.escape(text, quote=True)}" rel="noreferrer">'
            f'{html.escape(label)}</a>'
        )
    monospace = key in {"repo", "id", "key"} or key.endswith("_id")
    class_name = ' class="mono"' if monospace else ""
    return f"<span{class_name}>{html.escape(text)}</span>"


def _details(ticket: TicketSnapshot) -> str:
    data = ticket.data
    fields: list[tuple[tuple[str, ...], Any]] = []
    ordered = (
        "type", "assignee", "reporter", "priority", "created", "people_of_interest",
        "references",
    )
    for key in ordered:
        value = _source_timestamp(data, "created") if key == "created" else data.get(key)
        fields.extend(_flatten_value((key,), value))

    reserved = {
        "id", "key", "title", "description", "status", "acceptance_criteria",
        "activity", "comments", "labels", "logs", "metadata", "notes", "updated",
        "work_history", "worklogs", "assigned_branches", *ordered,
    }
    for key in sorted((key for key in data if key not in reserved), key=str.casefold):
        fields.extend(_flatten_value((str(key),), data[key]))
    metadata_fields: list[tuple[tuple[str, ...], Any]] = []
    metadata = data.get("metadata")
    if isinstance(metadata, Mapping):
        preferred = [key for key in ("tracker", "source_url") if key in metadata]
        remaining = sorted(
            (
                key for key in metadata
                if key not in {*preferred, "project", "source_created", "source_updated"}
            ),
            key=str.casefold,
        )
        for key in (*preferred, *remaining):
            metadata_fields.extend(_flatten_value((str(key),), metadata[key]))

    details: list[tuple[tuple[str, ...], Any]] = list(fields)
    technical: list[tuple[tuple[str, ...], Any]] = []
    for path, value in metadata_fields:
        role = _field_role(path)
        if role == "detail":
            details.append((_display_path(path), value))
        elif role == "metadata":
            technical.append((path, value))

    def rows(selected: list[tuple[tuple[str, ...], Any]]) -> str:
        return "".join(
            f"<dt>{html.escape(_field_label(path))}</dt><dd>{_field_value(path, value)}</dd>"
            for path, value in selected
        )

    detail_rows = rows(details)
    metadata_rows = rows(technical)
    if not detail_rows and not metadata_rows:
        return ""
    disclosure = ""
    if metadata_rows:
        disclosure = (
            '<details class="metadata-disclosure"><summary>Metadata</summary>'
            f'<div class="metadata-menu"><dl>{metadata_rows}</dl></div></details>'
        )
    return (
        '<section class="issue-details"><div class="details-heading"><h2>Details</h2>'
        f'{disclosure}</div><dl>{detail_rows}</dl></section>'
    )


def _validation(ticket: TicketSnapshot) -> str:
    if not ticket.validation_errors:
        return ""
    errors = "".join(f"<li>{html.escape(error)}</li>" for error in ticket.validation_errors)
    return (
        '<div class="validation" role="alert"><strong>Ticket data needs attention</strong>'
        f"<ul>{errors}</ul></div>"
    )


def _catalog_nav(active: str) -> str:
    return (
        '<nav class="catalog-nav" aria-label="Ticket database">'
        f'<a href="/"{(" aria-current=\"page\"" if active == "tickets" else "")}>Tickets</a>'
        f'<a href="/projects"{(" aria-current=\"page\"" if active == "projects" else "")}>Projects</a>'
        '</nav>'
    )


def _query_value(params: Mapping[str, str], key: str, default: str = "") -> str:
    value = params.get(key, default)
    return str(value).strip()


def _ticket_query(
    params: Mapping[str, str], *, default_assignee: str, project_key: str = ""
) -> TicketQuery:
    try:
        page = int(_query_value(params, "page", "1"))
    except ValueError:
        page = 1
    assignee = _query_value(params, "assignee", default_assignee)
    return TicketQuery(
        search=_query_value(params, "q"),
        status=_query_value(params, "status", "active"),
        assignee=assignee or "*",
        reporter=_query_value(params, "reporter"),
        label=_query_value(params, "label"),
        project=project_key or _query_value(params, "project"),
        page=page,
        per_page=20,
    )


def _option(value: str, label: str, selected: str) -> str:
    current = ' selected' if value.casefold() == selected.casefold() else ''
    return (
        f'<option value="{html.escape(value, quote=True)}"{current}>'
        f'{html.escape(label)}</option>'
    )


def _ticket_filters(
    action: str,
    query: TicketQuery,
    facets: TicketFacets,
    *,
    current_user: str,
    show_project: bool,
) -> str:
    actual_statuses = tuple(
        value for value in facets.statuses if value.casefold() not in {item.casefold() for item in _STATUS_ORDER}
    )
    status_options = [_option("active", "Open / active", query.status), _option("all", "All", query.status)]
    status_options.extend(
        _option(value, _status_label(value), query.status)
        for value in (*_STATUS_ORDER, *actual_statuses)
    )

    assignee_options = [_option("*", "Anyone", query.assignee)]
    if current_user:
        assignee_options.append(_option(current_user, f"Me — {current_user}", query.assignee))
    assignee_options.extend(
        _option(value, value, query.assignee)
        for value in facets.assignees
        if value.casefold() != current_user.casefold()
    )
    reporter_options = [_option("", "Anyone", query.reporter)] + [
        _option(value, value, query.reporter) for value in facets.reporters
    ]
    label_options = [_option("", "Any label", query.label)] + [
        _option(value, value, query.label) for value in facets.labels
    ]
    project_field = ""
    if show_project:
        project_options = [_option("", "All projects", query.project)] + [
            _option(value, value, query.project) for value in facets.projects
        ]
        project_field = (
            '<label class="filter-field project"><span>Project</span><select name="project">'
            f'{"".join(project_options)}</select></label>'
        )
    classes = "ticket-filters with-project" if show_project else "ticket-filters"
    return (
        '<div class="catalog-tools">'
        f'<form class="{classes}" method="get" action="{html.escape(action, quote=True)}">'
        '<label class="filter-field search"><span>Search</span>'
        f'<input name="q" type="search" value="{html.escape(query.search, quote=True)}" '
        'placeholder="Key, title, description"></label>'
        '<label class="filter-field status"><span>Status</span><select name="status">'
        f'{"".join(status_options)}</select></label>'
        '<label class="filter-field assignee"><span>Assignee</span><select name="assignee">'
        f'{"".join(assignee_options)}</select></label>'
        '<label class="filter-field"><span>Reporter</span><select name="reporter">'
        f'{"".join(reporter_options)}</select></label>'
        '<label class="filter-field"><span>Label</span><select name="label">'
        f'{"".join(label_options)}</select></label>'
        f'{project_field}<button class="filter-action" type="submit">Apply</button>'
        f'<a class="filter-clear" href="{html.escape(action, quote=True)}">Clear</a>'
        '</form></div>'
    )


def _query_string(query: TicketQuery, page: int, *, include_project: bool) -> str:
    params = {
        "q": query.search,
        "status": query.status,
        "assignee": query.assignee,
        "reporter": query.reporter,
        "label": query.label,
        "page": str(page),
    }
    if include_project:
        params["project"] = query.project
    return urlencode({key: value for key, value in params.items() if value})


def _pagination(action: str, results: TicketPage, query: TicketQuery, *, include_project: bool) -> str:
    if results.page_count <= 1:
        return ""

    def link(label: str, page: int, disabled: bool) -> str:
        if disabled:
            return f'<span class="disabled" aria-disabled="true">{label}</span>'
        href = f'{action}?{_query_string(query, page, include_project=include_project)}'
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    return (
        '<nav class="pagination" aria-label="Ticket pages">'
        f'{link("Previous", results.page - 1, results.page == 1)}'
        f'<span class="page-position">Page {results.page} of {results.page_count}</span>'
        f'{link("Next", results.page + 1, results.page == results.page_count)}'
        '</nav>'
    )


def _ticket_href(ticket: TicketSummary, companion: bool) -> str:
    if companion:
        return f'/project/{html.escape(ticket.project, quote=True)}/ticket/{html.escape(ticket.id, quote=True)}'
    return f'/ticket/{html.escape(ticket.id, quote=True)}'


def _ticket_results(results: TicketPage, *, companion: bool, show_project: bool) -> str:
    if not results.tickets:
        return '<p class="empty">No tickets match these filters.</p>'
    groups: dict[str, list[TicketSummary]] = {}
    for ticket in results.tickets:
        groups.setdefault(ticket.status or "(no status)", []).append(ticket)
    body: list[str] = []
    for status, tickets in groups.items():
        heading = _status_label(status) if status != "(no status)" else status
        body.append(
            '<section class="ticket-group">'
            f'<div class="ticket-group-head"><h2>{html.escape(heading)}</h2>'
            f'<span class="ticket-group-count">{len(tickets)}</span></div>'
        )
        for ticket in tickets:
            context: list[str] = []
            if show_project and ticket.project:
                context.append(html.escape(ticket.project_name or ticket.project))
            if ticket.assignee:
                context.append(f'Assigned to {html.escape(ticket.assignee)}')
            context.extend(
                f'<span class="ticket-label">{html.escape(label)}</span>' for label in ticket.labels
            )
            updated = f'Updated {html.escape(_datetime(ticket.updated))}' if ticket.updated else ""
            body.append(
                f'<a class="ticket-link" href="{_ticket_href(ticket, companion)}">'
                f'<span class="ticket-id mono">{html.escape(ticket.id)}</span>'
                '<span class="ticket-summary">'
                f'<span class="ticket-title">{html.escape(ticket.title or "Untitled")}</span>'
                f'<span class="ticket-context">{"".join(f"<span>{item}</span>" for item in context)}</span>'
                f'</span><span class="ticket-link-meta">{updated}</span></a>'
            )
        body.append("</section>")
    return "".join(body)


def _current_user(root: Path) -> str:
    identity = config.get_identity_name(root)
    if identity:
        return identity
    user, _source = config.get_user()
    return user or ""


def page_index(root: Path, fp: str, params: Mapping[str, str] | None = None) -> bytes:
    catalog = TicketCatalog(root)
    current_user = _current_user(root)
    query = _ticket_query(params or {}, default_assignee=current_user or "*")
    results = catalog.query(query)
    facets = catalog.facets()
    body = (
        '<div class="catalog">'
        f'{_catalog_nav("tickets")}'
        '<div class="catalog-title"><h1>Tickets</h1>'
        f'<span class="catalog-count">{results.total} ticket{"" if results.total == 1 else "s"}</span></div>'
        f'{_ticket_filters("/", query, facets, current_user=current_user, show_project=True)}'
        f'{_ticket_results(results, companion=catalog.locator is not None, show_project=True)}'
        f'{_pagination("/", results, query, include_project=True)}'
        '</div>'
    )
    return _page("Tickets — TicketBunker", body, fp)


def page_projects(root: Path, fp: str, params: Mapping[str, str] | None = None) -> bytes:
    catalog = TicketCatalog(root)
    current_user = _current_user(root)
    query = _ticket_query(params or {}, default_assignee="*")
    facets = catalog.facets()
    projects = catalog.projects(query)
    has_specific_filters = bool(
        query.search
        or query.status not in {"", "active"}
        or query.assignee != "*"
        or query.reporter
        or query.label
    )
    project_rows = []
    for item in projects:
        key = (
            f'<span class="project-key mono">{html.escape(item.key)}</span>'
            if item.name.casefold() != item.key.casefold()
            else ""
        )
        if has_specific_filters:
            meta = f"{item.ticket_count} matching · {item.active_ticket_count} active"
        else:
            meta = f'{item.active_ticket_count} active ticket{"" if item.active_ticket_count == 1 else "s"}'
        project_rows.append(
            '<a class="project-link" '
            f'href="/project/{html.escape(item.key, quote=True)}?{_query_string(query, 1, include_project=False)}">'
            '<span><span class="project-name">'
            f'{html.escape(item.name)}</span>{key}</span>'
            f'<span class="project-meta">{html.escape(meta)}</span></a>'
        )
    project_rows_html = "".join(project_rows)
    if not project_rows_html:
        project_rows_html = '<p class="empty">No projects match these filters.</p>'
    body = (
        '<div class="catalog">'
        f'{_catalog_nav("projects")}'
        '<div class="catalog-title"><h1>Projects</h1>'
        f'<span class="catalog-count">{len(projects)} project{"" if len(projects) == 1 else "s"}</span></div>'
        f'{_ticket_filters("/projects", query, facets, current_user=current_user, show_project=False)}'
        f'<div class="project-list">{project_rows_html}</div></div>'
    )
    return _page("Projects — TicketBunker", body, fp)


def page_project(
    root: Path, project_key: str, fp: str, params: Mapping[str, str] | None = None
) -> bytes | None:
    catalog = TicketCatalog(root)
    if project_key not in catalog.project_keys():
        return None
    current_user = _current_user(root)
    query = _ticket_query(params or {}, default_assignee="*", project_key=project_key)
    results = catalog.query(query)
    facets = catalog.facets(project_key)
    project_summary = next(item for item in catalog.projects() if item.key == project_key)
    action = f'/project/{project_key}'
    body = (
        '<div class="catalog">'
        f'{_catalog_nav("projects")}'
        f'<div class="crumbs"><a href="/projects">Projects</a> / {html.escape(project_key)}</div>'
        '<div class="catalog-title">'
        f'<h1>{html.escape(project_summary.name)} tickets</h1>'
        f'<span class="catalog-count">{results.total} ticket{"" if results.total == 1 else "s"}</span></div>'
        f'{_ticket_filters(action, query, facets, current_user=current_user, show_project=False)}'
        f'{_ticket_results(results, companion=catalog.locator is not None, show_project=False)}'
        f'{_pagination(action, results, query, include_project=False)}'
        '</div>'
    )
    return _page(f"{project_summary.name} tickets — TicketBunker", body, fp)


def _truth_bar(
    root: Path,
    ticket: TicketSnapshot,
    project_key: str,
    token: str,
) -> str:
    value = truth.state(root, project_key, ticket.id)
    items = []
    for key in ("companion", "git", "synthesis"):
        current = value.get(key) if isinstance(value.get(key), Mapping) else {}
        status = str(current.get("status") or "cached")
        label = str(current.get("label") or key.capitalize())
        compact = str(current.get("compact") or (label if key == "synthesis" else ""))
        detail = str(current.get("detail") or "")
        action = str(current.get("action") or "")
        command = str(current.get("command") or "refresh")
        log_url = str(current.get("log_url") or "")
        action_html = ""
        if token:
            action_hidden = "" if action else " hidden"
            log_hidden = "" if log_url else " hidden"
            action_html = (
                f'<button class="truth-action" type="button" data-truth-action '
                f'data-truth-command="{html.escape(command, quote=True)}"{action_hidden}>'
                f'{html.escape(action or "Refresh")}</button>'
                f'<a class="truth-log" data-truth-log href="{html.escape(log_url or "/truth/agent-log", quote=True)}"'
                f'{log_hidden}>View log</a>'
            )
        item_hidden = " hidden" if key != "synthesis" and status in {"current", "local"} else ""
        summary_label = f"{label}. {detail}" if detail else label
        compact_html = (
            f'<span class="truth-compact">{html.escape(compact)}</span>' if compact else ""
        )
        items.append(
            f'<details class="truth-item truth-{key}" data-truth-item="{key}" '
            f'data-state="{html.escape(status, quote=True)}"{item_hidden}>'
            f'<summary class="truth-trigger" aria-label="{html.escape(summary_label, quote=True)}" '
            f'title="{html.escape(label, quote=True)}">'
            f'<span class="truth-dot" aria-hidden="true"></span>{compact_html}</summary>'
            '<div class="truth-popover">'
            f'<span class="truth-label">{html.escape(label)}</span>'
            f'<span class="truth-detail">{html.escape(detail)}</span>{action_html}</div></details>'
        )
    return (
        '<section class="truth-strip" aria-label="Shared truth" aria-live="polite" data-truth '
        f'data-ticket="{html.escape(ticket.id, quote=True)}" '
        f'data-project="{html.escape(project_key, quote=True)}" '
        f'data-token="{html.escape(token, quote=True)}" '
        f'data-revision="{html.escape(str(value.get("content_revision") or ""), quote=True)}">'
        f'{"".join(items)}</section>'
    )


def _agent_log_page(root: Path, fp: str) -> bytes:
    path = autoagent.log_path(root)
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 65536))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()[-200:]
        text = "\n".join(lines)
    except OSError:
        text = "No synthesis log has been written on this machine yet."
    body = (
        '<div class="crumbs"><a href="/">Tickets</a></div>'
        '<article class="ticket-main ticket-page"><h1>Synthesis log</h1>'
        f'<p class="muted">{html.escape(str(path))}</p>'
        f'<pre><code>{html.escape(text)}</code></pre></article>'
    )
    return _page("Synthesis log — TicketBunker", body, fp)


def _coworker_surface(
    root: Path,
    ticket: TicketSnapshot,
    project_key: str,
    token: str,
) -> str:
    if not token:
        return ""
    user = config.get_user(root)[0] or "User"
    options = ['<option value="combined">Combined</option>']
    options.extend(
        f'<option value="{html.escape(report.slug, quote=True)}">{html.escape(report.branch)}</option>'
        for report in ticket.reports
    )
    tabs = []
    for index, category in enumerate(coworker.CATEGORIES):
        selected = category == "what"
        tabs.append(
            f'<button class="coworker-tab" id="coworker-tab-{category}" type="button" role="tab" '
            f'data-coworker-tab="{category}" aria-selected="{str(selected).lower()}" '
            f'aria-controls="coworker-messages" tabindex="{0 if selected else -1}">'
            f'{category.capitalize()}</button>'
        )
    return (
        '<button class="coworker-launcher" type="button" data-coworker-launcher '
        'aria-label="Show ticket coworker" aria-expanded="false" aria-controls="coworker-panel">'
        f'{_CHAT_ICON}</button>'
        '<p class="source-status" data-source-status role="status" aria-live="polite" hidden></p>'
        '<button class="selection-ask" type="button" data-selection-ask '
        'aria-label="Ask the ticket coworker about selected synthesis" title="Ask about selection" hidden>'
        f'{_CHAT_ICON}</button>'
        '<section class="coworker-panel" id="coworker-panel" data-coworker '
        'aria-label="Ticket coworker" '
        f'data-ticket="{html.escape(ticket.id, quote=True)}" '
        f'data-project="{html.escape(project_key, quote=True)}" '
        f'data-token="{html.escape(token, quote=True)}" hidden>'
        '<header class="coworker-titlebar">'
        f'<span class="coworker-title">{html.escape(user)} · {html.escape(ticket.id)}</span>'
        '<button class="coworker-collapse" type="button" data-coworker-collapse '
        'aria-label="Collapse ticket coworker" title="Collapse coworker">›</button>'
        '</header>'
        f'<div class="coworker-tabs" role="tablist" aria-label="Coworker topic">{"".join(tabs)}</div>'
        '<label class="coworker-scope"><span>Scope</span>'
        f'<select data-coworker-scope>{"".join(options)}</select></label>'
        '<div class="coworker-messages" id="coworker-messages" role="tabpanel" '
        'aria-labelledby="coworker-tab-what" '
        'data-coworker-messages aria-live="polite">'
        '<p class="coworker-empty">Ask about this ticket or correct its synthesis.</p></div>'
        '<form class="coworker-compose" data-coworker-form>'
        '<div class="coworker-reference" data-coworker-reference hidden>'
        '<span class="coworker-reference-meta" data-coworker-reference-meta></span>'
        '<button class="coworker-reference-remove" type="button" data-coworker-reference-remove '
        'aria-label="Remove synthesis reference">×</button>'
        '<p class="coworker-reference-copy" data-coworker-reference-copy></p></div>'
        '<label for="coworker-message">Message</label>'
        '<textarea id="coworker-message" name="message" maxlength="8000" '
        'placeholder="Ask for context, explain a concern, or correct the report" required></textarea>'
        '<div class="coworker-compose-row"><p class="coworker-status" data-coworker-status></p>'
        '<button class="coworker-send" type="submit" data-coworker-send>Send</button></div>'
        '</form></section>'
    )


def page_ticket(
    root: Path,
    ticket_id: str,
    fp: str,
    project_key: str | None = None,
    work_token: str = "",
) -> bytes | None:
    try:
        if project_key:
            ticket = TicketCatalog(root).get_ticket(project_key, ticket_id)
        else:
            ticket = TicketDatabase(root).get_ticket(ticket_id, refresh_branches=True)
    except commands.CommandRejected:
        raise
    except (FileNotFoundError, KeyError, ValueError):
        return None

    status = ticket.status
    head_badges = []
    if status:
        kind = "done" if status in {"done", "closed"} else "status"
        head_badges.append(_badge(_status_label(status), kind))
    labels = ticket.data.get("labels") or []
    if isinstance(labels, list):
        head_badges.extend(_badge(str(label), "label") for label in labels if str(label).strip())
    facts = []
    if head_badges:
        facts.append(f'<div class="head-badges">{"".join(head_badges)}</div>')
    updated = _source_timestamp(ticket.data, "updated")
    if updated:
        facts.append(f'<span class="head-updated">Updated {html.escape(_datetime(updated))}</span>')
    head_facts = f'<div class="head-facts">{"".join(facts)}</div>' if facts else ""

    project_name, project_url = _project_context(ticket.data)
    resolved_project = project_key or ""
    project_html = ""
    if project_name:
        if project_url.startswith(("https://", "http://")):
            project_html = (
                f'<a class="ticket-project" href="{html.escape(project_url, quote=True)}" '
                f'rel="noreferrer">{html.escape(project_name)}</a><span aria-hidden="true">/</span>'
            )
        else:
            project_html = (
                f'<span class="ticket-project">{html.escape(project_name)}</span>'
                '<span aria-hidden="true">/</span>'
            )

    ticket_sections = [
        '<section class="section"><h2>Description</h2>'
        f'<div class="description rich-text">'
        f'{render_ticket_text(ticket.description) if ticket.description else "<p class=\"empty\">No description.</p>"}'
        '</div></section>'
    ]
    criteria = ticket.data.get("acceptance_criteria") or []
    if isinstance(criteria, list) and criteria:
        items = "".join(f"<li>{_inline(html.escape(str(item)))}</li>" for item in criteria)
        ticket_sections.append(
            f'<section class="section"><h2>Acceptance criteria</h2><ul class="criteria">{items}</ul></section>'
        )
    activity = _activity(ticket)
    if activity:
        ticket_sections.append(activity)
    details = _details(ticket)

    tabs = (
        '<div class="view-tabs" role="tablist" aria-label="Ticket view">'
        '<button class="view-tab" id="ticket-tab" type="button" role="tab" '
        'aria-selected="true" aria-controls="ticket-panel" data-view-tab="ticket">Ticket</button>'
        '<button class="view-tab" id="agent-tab" type="button" role="tab" tabindex="-1" '
        'aria-selected="false" aria-controls="agent-panel" data-view-tab="agent">Agent</button></div>'
    )
    rail = (
        f'<aside class="ticket-rail">{_lifecycle(ticket)}'
        f'{_branches(ticket, work_token if project_key and (root / layout.LOCATOR_FILE).exists() else "")}'
        '</aside>'
    )
    panels = (
        '<div class="ticket-layout"><div class="ticket-view-stack">'
        '<div class="view-panel" id="ticket-panel" role="tabpanel" aria-labelledby="ticket-tab" '
        'data-view-panel="ticket">'
        '<article class="ticket-main ticket-page">'
        f'{"".join(ticket_sections)}{details}</article></div>'
        '<div class="view-panel" id="agent-panel" role="tabpanel" aria-labelledby="agent-tab" '
        'data-view-panel="agent" hidden>'
        f'<div class="ticket-main">{_agent_work(ticket)}</div></div>'
        f'</div>{rail}</div>'
    )

    if project_key:
        crumbs = (
            '<div class="crumbs"><a href="/projects">Projects</a> / '
            f'<a href="/project/{html.escape(project_key, quote=True)}">{html.escape(project_key)}</a>'
            f' / {html.escape(ticket.id)}</div>'
        )
    else:
        crumbs = '<div class="crumbs"><a href="/">Tickets</a></div>'
    body = (
        f'{crumbs}'
        f'{_validation(ticket)}'
        '<header class="ticket-head">'
        f'<div class="ticket-key">{project_html}<span class="mono">{html.escape(ticket.id)}</span></div>'
        '<div class="ticket-title-row">'
        f'<h1>{html.escape(ticket.title or "Untitled")}</h1>'
        f'{_truth_bar(root, ticket, resolved_project, work_token)}</div>{head_facts}</header>'
        f'{tabs}{panels}{_coworker_surface(root, ticket, resolved_project, work_token)}'
    )
    return _page(f"{ticket.id} — TicketBunker", body, fp)


@dataclass(frozen=True)
class ViewerProblem:
    """A browser-safe explanation of a failed viewer request."""

    code: str
    title: str
    message: str
    state: str
    action: str
    incident: str = ""

    def as_dict(self) -> dict[str, Any]:
        value = {"ok": False, **asdict(self)}
        if not self.incident:
            value.pop("incident")
        return value


def _command_problem(exc: commands.CommandRejected, *, mutating: bool) -> ViewerProblem:
    """Translate backend detail into concise, stable browser copy."""
    lines = [line.strip() for line in exc.message.splitlines() if line.strip()]
    message = next(
        (
            line
            for line in lines
            if not line.casefold().startswith(("next:", "git said:", "traceback"))
        ),
        "TicketBunker could not finish this request.",
    )
    action = next(
        (line.split(":", 1)[1].strip() for line in lines if line.casefold().startswith("next:")),
        "",
    )
    code = exc.code.casefold()
    if "worktree_dirty" in code:
        title = "Working tree needs attention"
        state = "Nothing changed. Your uncommitted files are still in place."
        action = action or "Commit or stash your changes, then retry."
    elif "remote" in code or "companion" in code or "network" in code:
        title = "Remote state unavailable"
        state = "The cached local state was kept. Your working files were not changed."
        action = action or "Run tb doctor, restore remote access, then retry."
    elif "branch" in code or "git" in code:
        title = "Branch action stopped"
        state = (
            "The operation stopped without a confirmed result. Check the current branch and Git status."
            if mutating
            else "The request stopped. TicketBunker did not run a recovery command."
        )
        action = action or "Run git status, resolve the reported Git state, then retry."
    elif code.startswith("coworker_proposal"):
        title = "Report update stopped"
        state = "The current child reports and Combined synthesis were kept."
        action = action or "Review the current synthesis, then ask the coworker to prepare a new proposal."
    elif code.startswith("coworker"):
        title = "Coworker unavailable"
        state = "Your conversation, reports, and source files were not changed."
        action = action or "Run tb agent status, then retry this message."
    elif code == "source_changed":
        title = "File changed on disk"
        state = "The browser kept your draft. TicketBunker did not overwrite the newer file."
        action = action or "Copy any draft changes you need, reload the file, then apply them again."
    elif code.startswith("source_save") or code.startswith("source_content") or code.startswith("source_digest"):
        title = "Save stopped"
        state = "The browser kept your draft. TicketBunker did not change the source file."
        action = action or "Check the file state and local permissions, then retry Save."
    elif code.startswith("source_editor"):
        title = "Source link did not open"
        message = "TicketBunker could not start the optional external editor."
        state = "No editor was opened and your source files were not changed."
        action = action or "Select Included editor in Local settings, then retry the link."
    elif code.startswith("source_"):
        title = "Source link unavailable"
        state = "No editor was opened and your source files were not changed."
        action = action or "Switch to the referenced branch or refresh its synthesis, then retry."
    else:
        title = "TicketBunker needs attention"
        state = (
            "The operation stopped without a confirmed result. Check the current branch and Git status."
            if mutating
            else "The request stopped. TicketBunker did not run a recovery command."
        )
        action = action or "Run tb doctor, then retry."
    return ViewerProblem(exc.code, title, message, state, action)


def _unexpected_problem(*, mutating: bool) -> ViewerProblem:
    return ViewerProblem(
        code="viewer_internal_error",
        title="Viewer stopped safely",
        message="TicketBunker could not finish this request.",
        state=(
            "The operation stopped before TicketBunker could confirm completion. Run git status before continuing."
            if mutating
            else "The request stopped. Your source worktree was not intentionally changed."
        ),
        action="Run tb doctor, then reload this page.",
        incident=secrets.token_hex(4),
    )


def _git_admin_dir(root: Path) -> Path | None:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if not marker.is_file():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value.startswith("gitdir:"):
        return None
    target = Path(value.split(":", 1)[1].strip())
    return target if target.is_absolute() else (root / target).resolve()


def _record_viewer_error(
    root: Path,
    path: str,
    exc: BaseException,
    *,
    incident: str = "",
) -> None:
    """Keep diagnostic detail in Git's local administrative area, never in the page."""
    git_dir = _git_admin_dir(root)
    if git_dir is None:
        return
    try:
        log_dir = git_dir / "ticketbunker"
        log_dir.mkdir(parents=True, exist_ok=True)
        value = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "incident": incident,
            "path": path,
            "error": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        with (log_dir / "viewer-errors.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _problem_page(problem: ViewerProblem, retry_path: str) -> bytes:
    incident = (
        f'<p class="problem-id">Diagnostic ID <code>{html.escape(problem.incident)}</code></p>'
        if problem.incident
        else ""
    )
    body = (
        '<section class="problem-card">'
        f'<h1>{html.escape(problem.title)}</h1>'
        f'<p>{html.escape(problem.message)}</p>'
        '<div class="problem-state">'
        f'<div><strong>Current state</strong>{html.escape(problem.state)}</div>'
        f'<div><strong>Next</strong>{html.escape(problem.action)}</div>'
        '</div><div class="problem-actions">'
        f'<a class="primary" href="{html.escape(retry_path, quote=True)}">Try again</a>'
        '<a href="/">Tickets</a></div>'
        f'{incident}</section>'
    )
    document = (
        '<!doctype html><html lang="en" data-viewer-token=""><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<link rel="icon" href="data:,">'
        f'<title>{html.escape(problem.title)} | TicketBunker</title><style>{_STYLE}</style></head>'
        f'<body><main>{body}</main><script>{_PRESENCE_JS}</script></body></html>'
    )
    return document.encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    root: Path
    token: str

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        try:
            self._do_GET()
        except (ConnectionError, TimeoutError):
            pass
        except commands.CommandRejected as exc:
            problem = _command_problem(exc, mutating=False)
            _record_viewer_error(self.root, self.path, exc)
            if urlsplit(self.path).path.startswith(("/work/state/", "/truth/", "/coworker/")):
                self._send_json(409, problem.as_dict())
            else:
                self._send(409, _problem_page(problem, self.path))
        except Exception as exc:  # Browser boundary: diagnostics stay local.
            problem = _unexpected_problem(mutating=False)
            _record_viewer_error(self.root, self.path, exc, incident=problem.incident)
            if urlsplit(self.path).path.startswith(("/work/state/", "/truth/", "/coworker/")):
                self._send_json(500, problem.as_dict())
            else:
                self._send(500, _problem_page(problem, self.path))

    def _do_GET(self) -> None:
        root = self.root
        fp = fingerprint(root)
        request = urlsplit(self.path)
        path = request.path
        params = {
            key: values[-1]
            for key, values in parse_qs(request.query, keep_blank_values=True).items()
            if values
        }
        if path == "/poll":
            self._send(200, fp.encode(), "text/plain; charset=utf-8")
            return
        if path == "/_viewer/health":
            server = self.server
            self._send_json(
                200,
                {
                    "ok": True,
                    "launch": str(getattr(server, "launch_id", "")),
                    "root": str(root),
                    "version": __version__,
                },
            )
            return
        if path == "/":
            self._send(200, page_index(root, fp, params))
            return
        if path == "/projects":
            self._send(200, page_projects(root, fp, params))
            return
        if path == "/source":
            try:
                line = int(params.get("line") or 1)
            except ValueError as exc:
                raise commands.CommandRejected("source_line_invalid", "Choose a numeric source line.") from exc
            self._send(
                200,
                page_source(
                    root,
                    str(params.get("path") or ""),
                    line,
                    str(params.get("return") or "/"),
                    self.token,
                ),
            )
            return
        if path == "/truth/state":
            value = truth.state(root, str(params.get("project") or ""), str(params.get("ticket") or ""))
            self._send_json(200, {"ok": True, **value})
            return
        if path == "/truth/agent-log":
            self._send(200, _agent_log_page(root, fp))
            return
        if path == "/coworker/state":
            value = coworker.state(
                root,
                str(params.get("project") or ""),
                str(params.get("ticket") or ""),
                category=str(params.get("category") or "what"),
                scope=str(params.get("scope") or "combined"),
            )
            self._send_json(200, {"ok": True, **value})
            return
        match = re.fullmatch(r"/work/state/([A-Za-z0-9._-]+)", path)
        if match:
            value = workflow.state(root, match.group(1))
            self._send_json(200, {"ok": True, **value})
            return
        match = re.fullmatch(r"/project/([A-Z][A-Z0-9]*)", path)
        if match:
            page = page_project(root, match.group(1), fp, params)
            if page is not None:
                self._send(200, page)
                return
        match = re.fullmatch(
            r"/project/([A-Z][A-Z0-9]*)/ticket/([A-Za-z0-9._-]+)", path
        )
        if match:
            page = page_ticket(root, match.group(2), fp, match.group(1), self.token)
            if page is not None:
                self._send(200, page)
                truth.queue_refresh(root, match.group(1), match.group(2))
                return
        match = re.fullmatch(r"/ticket/([A-Za-z0-9._-]+)", path)
        if match:
            page = page_ticket(root, match.group(1), fp, work_token=self.token)
            if page is not None:
                self._send(200, page)
                truth.queue_refresh(root, "", match.group(1))
                return
        body = (
            '<div class="crumbs"><a href="/">Tickets</a></div>'
            "<h1>Not found</h1><p>No such ticket.</p>"
        )
        self._send(404, _page("Not found", body, fp))

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        try:
            self._do_POST()
        except (ConnectionError, TimeoutError):
            pass
        except commands.CommandRejected as exc:
            problem = _command_problem(exc, mutating=True)
            _record_viewer_error(self.root, self.path, exc)
            self._send_json(409, problem.as_dict())
        except Exception as exc:  # Browser boundary: diagnostics stay local.
            problem = _unexpected_problem(mutating=True)
            _record_viewer_error(self.root, self.path, exc, incident=problem.incident)
            self._send_json(500, problem.as_dict())

    def _do_POST(self) -> None:
        path = urlsplit(self.path).path
        commands_by_path = {
            "/work": "workflow.start",
            "/truth/refresh": "truth.queue",
            "/truth/synthesize": "truth.synthesize",
            "/coworker/chat": "coworker.chat",
            "/coworker/proposal/accept": "coworker.proposal.accept",
            "/coworker/proposal/dismiss": "coworker.proposal.dismiss",
            "/source/open": "editor.open",
            "/source/resolve": "editor.resolve",
            "/source/save": "editor.save",
        }
        command = commands_by_path.get(path)
        presence_action = path in {"/_viewer/presence", "/_viewer/close"}
        if command is None and not presence_action:
            self._send_json(404, {"ok": False, "code": "not_found", "message": "No such action."})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        maximum = 4_100_000 if path == "/source/save" else 65536
        if length <= 0 or length > maximum:
            self._send_json(
                400,
                {"ok": False, "code": "request_invalid", "message": "The request is empty or too large."},
            )
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_json(
                400,
                {"ok": False, "code": "request_invalid", "message": "The request must be one JSON object."},
            )
            return
        if not isinstance(payload, dict):
            self._send_json(
                400,
                {"ok": False, "code": "request_invalid", "message": "The request must be one JSON object."},
            )
            return
        supplied = self.headers.get("X-TicketBunker-Token") or str(payload.pop("_token", "") or "")
        if not supplied or not secrets.compare_digest(supplied, self.token):
            self._send_json(
                403,
                {"ok": False, "code": "transport_token_invalid", "message": "Reload the viewer and retry."},
            )
            return
        if presence_action:
            session = str(payload.get("session") or "")
            server = self.server
            if path == "/_viewer/presence":
                getattr(server, "note_presence", lambda _value: None)(session)
            else:
                getattr(server, "note_close", lambda _value: None)(session)
            self._send_json(200, {"ok": True})
            return
        if command == "truth.queue":
            payload["force"] = True
        result = commands.dispatch(
            command,
            payload,
            commands.CommandContext(self.root, transport="http"),
        )
        self._send_json(202 if command in {"truth.queue", "truth.synthesize"} else 200, result)

    def _send_json(self, code: int, value: Mapping[str, Any]) -> None:
        self._send(
            code,
            (json.dumps(dict(value), ensure_ascii=False) + "\n").encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _send(self, code: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        try:
            if content_type.startswith("text/html") and b'data-viewer-token=""' in body:
                body = body.replace(
                    b'data-viewer-token=""',
                    f'data-viewer-token="{html.escape(self.token, quote=True)}"'.encode(),
                    1,
                )
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, TimeoutError):
            pass

    def log_message(self, *_args: object) -> None:
        pass


class _ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        launch_id: str = "",
    ) -> None:
        super().__init__(server_address, handler)
        self.launch_id = launch_id
        self._presence_lock = threading.Lock()
        self._viewer_sessions: dict[str, float] = {}
        self._started_at = time.monotonic()
        self._saw_viewer = False
        self._empty_since: float | None = None
        self._active_requests = 0
        self._monitor_stop = threading.Event()

    def process_request_thread(self, request: object, client_address: object) -> None:
        with self._presence_lock:
            self._active_requests += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._presence_lock:
                self._active_requests -= 1

    def note_presence(self, session: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,128}", session):
            return
        with self._presence_lock:
            self._viewer_sessions[session] = time.monotonic()
            self._saw_viewer = True
            self._empty_since = None

    def note_close(self, session: str) -> None:
        with self._presence_lock:
            self._viewer_sessions.pop(session, None)
            if self._saw_viewer and not self._viewer_sessions and self._empty_since is None:
                self._empty_since = time.monotonic()

    def _monitor_idle(self) -> None:
        while not self._monitor_stop.wait(1):
            now = time.monotonic()
            stop = False
            with self._presence_lock:
                self._viewer_sessions = {
                    session: seen
                    for session, seen in self._viewer_sessions.items()
                    if now - seen < 1800
                }
                if self._saw_viewer:
                    if self._viewer_sessions:
                        self._empty_since = None
                    elif self._empty_since is None:
                        self._empty_since = now
                    stop = (
                        self._empty_since is not None
                        and now - self._empty_since >= 3
                        and self._active_requests == 0
                    )
                else:
                    stop = now - self._started_at >= 60 and self._active_requests == 0
            if stop:
                self.shutdown()
                return

    def serve_until_closed(self) -> None:
        monitor = threading.Thread(target=self._monitor_idle, name="tb-viewer-idle", daemon=True)
        monitor.start()
        try:
            self.serve_forever()
        finally:
            self._monitor_stop.set()
            monitor.join(timeout=2)

    def handle_error(self, _request: object, _client_address: object) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError)):
            return
        print("TicketBunker viewer request stopped safely. Run tb doctor for help.", file=sys.stderr)


def server_log_path(root: Path) -> Path:
    return autoagent.log_path(Path(root).resolve()).parent.parent / "viewer.log"


def serve(root: Path, port: int, *, launch_id: str = "") -> _ViewerServer:
    handler = type("BoundHandler", (_Handler,), {"root": root, "token": secrets.token_urlsafe(32)})
    return _ViewerServer(("127.0.0.1", port), handler, launch_id=launch_id)
