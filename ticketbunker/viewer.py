"""The localhost ticket viewer and explicit local Work action (DESIGN.md §3.5).

Every request renders a fresh :class:`ticketbunker.api.TicketSnapshot` from
the repository-owned ``.tb/`` database. Ticket and Agent views remain read-only;
the isolated Work control dispatches the same guarded command as the CLI.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

from . import commands, config, layout, workflow
from .api import (
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
  --bg: #ffffff;
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
  --warning: #7f5f01;
  --warning-soft: #fff7d6;
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
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible {
  outline: 3px solid rgba(12, 102, 228, .28);
  outline-offset: 3px;
  border-radius: 2px;
}
main { max-width: 1320px; margin: 0 auto; padding: 30px 34px 72px; }
.crumbs { color: var(--muted); font-size: 12px; margin: 0 0 17px; }
.crumbs a { color: inherit; }
.ticket-head { padding-bottom: 18px; }
.ticket-key { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; color: var(--muted); font-size: 12px; font-weight: 600; letter-spacing: .02em; }
.ticket-project { color: var(--body); }
h1, h2, h3 { color: var(--fg); }
h1 { font-size: 26px; line-height: 1.25; letter-spacing: -.018em; margin: 5px 0 15px; max-width: 920px; }
h2 { font-size: 16px; line-height: 1.3; margin: 0 0 14px; }
h3 { font-size: 13px; line-height: 1.35; margin: 0 0 8px; }
p { margin: 0 0 12px; }
.head-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; margin: 0; }
.head-facts div { display: flex; align-items: center; gap: 7px; }
.head-badges { flex-wrap: wrap; }
.head-updated { color: var(--muted); font-size: 12px; }
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
.badge.status { color: #0055cc; background: var(--accent-soft); }
.badge.label { font-weight: 500; }
.badge.done, .badge.closed, .badge.verified, .badge.merged { color: var(--success); background: var(--success-soft); }
.badge.stale, .badge.open { color: var(--warning); background: var(--warning-soft); }
.badge.unverified, .badge.missing { color: var(--danger); background: var(--danger-soft); }
.ticket-layout { display: grid; grid-template-columns: minmax(0, 1fr) 310px; gap: 0; }
.ticket-main { min-width: 0; padding: 31px 42px 0 0; }
.ticket-page { max-width: 78ch; padding-right: 0; }
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
.work-category h3 { margin-bottom: 7px; font-size: 15px; }
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
.work-submit { min-height: 42px; border: 1px solid var(--accent); border-radius: var(--radius); color: #fff; background: var(--accent); font-weight: 650; cursor: pointer; }
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
  box-shadow: 0 8px 24px rgba(23, 43, 77, .14);
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
.tag { display: inline-block; margin: 0 5px 0 0; padding: 0 4px; border: 1px solid currentColor; border-radius: 3px; font: 10px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; vertical-align: .08em; }
.tag.obs { color: #0055cc; }
.tag.real { color: var(--success); }
.tag.spec { color: var(--warning); }
code { padding: 1px 3px; border-radius: 3px; background: var(--panel); color: var(--fg); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9em; overflow-wrap: anywhere; }
hr { border: 0; border-top: 1px solid var(--line); margin: 22px 0; }
ul { margin: 6px 0 12px; padding-left: 21px; }
li { margin: 3px 0; }
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
  background: rgba(255, 255, 255, .97);
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
.filter-action { border: 1px solid var(--accent); color: #fff; background: var(--accent); cursor: pointer; }
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
@media (min-width: 761px) and (max-width: 1000px) {
  .ticket-layout { grid-template-columns: minmax(0, 1fr) 260px; }
  .ticket-main { padding-right: 24px; }
  .ticket-page { padding-right: 0; }
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
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""


_POLL_JS = """
const fp = document.documentElement.dataset.fp;
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

async function loadWorkState() {
  if (!workForm || workForm.dataset.loaded === 'true') return;
  setWorkStatus('Checking the local repository…');
  workSubmit.disabled = true;
  try {
    const response = await fetch(`/work/state/${encodeURIComponent(workForm.dataset.ticket)}`);
    const value = await response.json();
    if (!response.ok) throw new Error(value.message || 'Repository preflight failed.');
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
    if (!response.ok) throw new Error(value.message || 'Branch creation failed.');
    setWorkStatus(`Created and switched to ${value.branch}.`, 'success');
    window.setTimeout(() => location.reload(), 900);
  } catch (error) {
    setWorkStatus(error.message, 'error');
    workSubmit.disabled = false;
  }
});

setInterval(async () => {
  try {
    const response = await fetch('/poll');
    if ((await response.text()).trim() !== fp) location.reload();
  } catch (error) { /* Keep the current repository snapshot if the server exits. */ }
}, 2000);
"""


_TAG_RE = re.compile(r"^\[(observation|real|speculation)\]\s*")
_TAG_CLASS = {"observation": "obs", "real": "real", "speculation": "spec"}
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

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, escaped)


def _claim(escaped: str) -> str:
    match = _TAG_RE.match(escaped)
    if not match:
        return _inline(escaped)
    tag = match.group(1)
    label = "speculation" if tag == "speculation" else tag
    return (
        f'<span class="tag {_TAG_CLASS[tag]}">{label}</span>'
        + _inline(escaped[match.end():])
    )


def _render_markdown(text: str, *, claims: bool, issue_copy: bool = False) -> str:
    """Render TicketBunker's safe markdown subset after escaping input."""
    output: list[str] = []
    list_tag = ""
    code_lines: list[str] | None = None

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            output.append(f"</{list_tag}>")
            list_tag = ""

    def open_list(tag: str) -> None:
        nonlocal list_tag
        if list_tag == tag:
            return
        close_list()
        output.append(f"<{tag}>")
        list_tag = tag

    def inline(value: str) -> str:
        return _claim(value) if claims else _inline(value)

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
        elif line.startswith(("- ", "* ")):
            open_list("ul")
            output.append(f"<li>{inline(escaped[2:])}</li>")
        elif re.match(r"^\d+\.\s", line):
            open_list("ol")
            content = re.sub(r"^\d+\.\s", "", escaped, count=1)
            output.append(f"<li>{inline(content)}</li>")
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
        f'<!doctype html><html lang="en" data-fp="{fp}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<link rel="icon" href="data:,">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main><script>{_POLL_JS}</script></body></html>"
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


def _report_anchor(report: ReportSnapshot) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]", "-", report.slug)
    return f"report-{clean}"


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
            '<article class="master-branch-summary">'
            f'<div class="master-branch-label mono">{html.escape(report.branch)}</div>'
            f'<div class="brief-claims">{preview}</div></article>'
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
        categories.append(
            f'<section class="work-category"><h3>{label}</h3>'
            f'<div class="{"master-brief" if master else "brief-claims"}">{preview}</div>'
            f'{_category_trail(reports, label)}</section>'
        )
    return (
        f'<div class="report-panel" id="{panel_id}" data-report-panel="{html.escape(value, quote=True)}"{hidden}>'
        f'{context}{"".join(categories)}<div class="report-state-trail">'
        f'{_category_trail(reports, "State", "Report state")}</div></div>'
    )


def _agent_work(ticket: TicketSnapshot) -> str:
    if not ticket.reports:
        return (
            '<section class="section agent-work"><div class="section-heading agent-heading">'
            '<h2>Agent work</h2></div><p class="empty">No agent reports yet.</p></section>'
        )
    options = ['<option value="master">Master</option>']
    panels = [_agent_report_panel(ticket.reports, "master", master=True)]
    for report in ticket.reports:
        options.append(
            f'<option value="{html.escape(report.slug, quote=True)}">{html.escape(report.branch)}</option>'
        )
        panels.append(_agent_report_panel((report,), report.slug, master=False))
    return (
        '<section class="section agent-work"><div class="section-heading agent-heading">'
        '<h2>Agent work</h2><label class="report-picker"><span>Report</span>'
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
    labels = {"merged": "Merged", "open": "Open", "missing": "Missing", "unobserved": "Unobserved"}
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
        tag = "a" if report else "div"
        href = f' href="#{_report_anchor(report)}"' if report else ""
        ahead = "?" if branch.ahead is None else branch.ahead
        behind = "?" if branch.behind is None else branch.behind
        development = _date(branch.last_development)
        date_text = f" · {development}" if development else ""
        if branch.state == "unobserved":
            branch_meta = "Awaiting first refresh"
        elif branch.state == "missing":
            branch_meta = "Branch not found"
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
            ticket = TicketDatabase(root).get_ticket(ticket_id)
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
    panels = (
        '<div class="view-panel" id="ticket-panel" role="tabpanel" aria-labelledby="ticket-tab" '
        'data-view-panel="ticket">'
        '<article class="ticket-main ticket-page">'
        f'{"".join(ticket_sections)}{details}</article></div>'
        '<div class="view-panel" id="agent-panel" role="tabpanel" aria-labelledby="agent-tab" '
        'data-view-panel="agent" hidden><div class="ticket-layout">'
        f'<div class="ticket-main">{_agent_work(ticket)}</div>'
        f'<aside class="ticket-rail">{_lifecycle(ticket)}'
        f'{_branches(ticket, work_token if project_key and (root / layout.LOCATOR_FILE).exists() else "")}'
        '</aside></div></div>'
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
        f'<h1>{html.escape(ticket.title or "Untitled")}</h1>{head_facts}</header>'
        f'{tabs}{panels}'
    )
    return _page(f"{ticket.id} — TicketBunker", body, fp)


class _Handler(BaseHTTPRequestHandler):
    root: Path
    token: str

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
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
        if path == "/":
            self._send(200, page_index(root, fp, params))
            return
        if path == "/projects":
            self._send(200, page_projects(root, fp, params))
            return
        match = re.fullmatch(r"/work/state/([A-Za-z0-9._-]+)", path)
        if match:
            try:
                value = workflow.state(root, match.group(1))
            except commands.CommandRejected as exc:
                self._send_json(409, exc.as_dict())
                return
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
                return
        match = re.fullmatch(r"/ticket/([A-Za-z0-9._-]+)", path)
        if match:
            page = page_ticket(root, match.group(1), fp)
            if page is not None:
                self._send(200, page)
                return
        body = (
            '<div class="crumbs"><a href="/">Tickets</a></div>'
            "<h1>Not found</h1><p>No such ticket.</p>"
        )
        self._send(404, _page("Not found", body, fp))

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if urlsplit(self.path).path != "/work":
            self._send_json(404, {"ok": False, "code": "not_found", "message": "No such action."})
            return
        supplied = self.headers.get("X-TicketBunker-Token") or ""
        if not supplied or not secrets.compare_digest(supplied, self.token):
            self._send_json(
                403,
                {"ok": False, "code": "transport_token_invalid", "message": "Reload the viewer and retry."},
            )
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > 65536:
            self._send_json(
                400,
                {"ok": False, "code": "request_invalid", "message": "The Work request is empty or too large."},
            )
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_json(
                400,
                {"ok": False, "code": "request_invalid", "message": "The Work request must be one JSON object."},
            )
            return
        if not isinstance(payload, dict):
            self._send_json(
                400,
                {"ok": False, "code": "request_invalid", "message": "The Work request must be one JSON object."},
            )
            return
        try:
            result = commands.dispatch(
                "workflow.start",
                payload,
                commands.CommandContext(self.root, transport="http"),
            )
        except commands.CommandRejected as exc:
            self._send_json(409, exc.as_dict())
            return
        self._send_json(200, result)

    def _send_json(self, code: int, value: Mapping[str, Any]) -> None:
        self._send(
            code,
            (json.dumps(dict(value), ensure_ascii=False) + "\n").encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _send(self, code: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def serve(root: Path, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"root": root, "token": secrets.token_urlsafe(32)})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
