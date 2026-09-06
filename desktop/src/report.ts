export const TOPICS = ["who", "what", "where", "why"] as const;
export type Topic = (typeof TOPICS)[number];

function section(text: string, heading: string): string {
  const lines = text.split(/\r?\n/);
  const selected: string[] = [];
  let keep = false;
  for (const line of lines) {
    if (/^##\s+/.test(line) && !/^###\s+/.test(line)) {
      keep = line.replace(/^##\s+/, "").trim().toLowerCase() === heading.toLowerCase();
      continue;
    }
    if (keep) selected.push(line);
  }
  return selected.join("\n").trim();
}

function subsection(text: string, parent: string, heading: string): string {
  const lines = text.split(/\r?\n/);
  const selected: string[] = [];
  let inParent = false;
  let keep = false;
  for (const line of lines) {
    if (/^##\s+/.test(line) && !/^###\s+/.test(line)) {
      inParent = line.replace(/^##\s+/, "").trim().toLowerCase() === parent.toLowerCase();
      keep = false;
      continue;
    }
    if (inParent && /^###\s+/.test(line)) {
      keep = line.replace(/^###\s+/, "").trim().toLowerCase() === heading.toLowerCase();
      continue;
    }
    if (inParent && keep) selected.push(line);
  }
  return selected.join("\n").trim();
}

/** One named part of the Reader brief ("Standing", "Checklist"), or "" when absent. */
export function briefPart(text: string, label: string): string {
  return subsection(text, "Reader brief", label);
}

const CLAIM_LINE_RE = /^\s*[-*]\s+(\[(?:observation|real|speculation|inference)\].*)$/i;
const TAG_RE = /^\[(observation|real|speculation|inference)\]\s*(?:(grounded|adjacent|ungrounded)\s*(?:[—–:-]\s*)?)?/i;
const PREFIX_RE = /^(Risk|Next|Ask|Done|Partial|Missing|Unclear|Defect|Step):\s*/i;

function claimLines(block: string): string[] {
  return block.split("\n").map((line) => CLAIM_LINE_RE.exec(line)?.[1] || "").filter(Boolean);
}

function prefixOf(claim: string): { prefix: string; body: string; tag: string } {
  const tagged = TAG_RE.exec(claim);
  const tag = tagged ? tagged[0] : "";
  const rest = claim.slice(tag.length);
  const prefixed = PREFIX_RE.exec(rest);
  return { prefix: prefixed ? prefixed[1].toLowerCase() : "", body: prefixed ? rest.slice(prefixed[0].length) : rest, tag };
}

export type PlanSteps = string[] & { derived?: boolean };

/**
 * The report's plan as claim strings ("[real] do this (path:1)"), in order.
 * A report with a `### Plan` supplies its own steps; older reports get one
 * derived from Next, the Defects under What, and the checklist's Partial and
 * Missing items, marked `derived` so the page can say so.
 */
export function planSteps(text: string): PlanSteps {
  const own = claimLines(subsection(text, "Reader brief", "Plan"))
    .map((claim) => prefixOf(claim))
    .filter((value) => value.prefix === "step")
    .map((value) => `${value.tag}${value.body}`);
  if (own.length) return own;
  const steps: PlanSteps = [];
  const seen = new Set<string>();
  const push = (tag: string, body: string) => {
    const key = body.trim();
    if (!key || seen.has(key)) return;
    seen.add(key);
    steps.push(`${tag}${key}`);
  };
  for (const claim of claimLines(subsection(text, "Reader brief", "Standing"))) {
    const value = prefixOf(claim);
    if (value.prefix === "next") push(value.tag, value.body);
  }
  for (const claim of [...claimLines(subsection(text, "Reader brief", "What")), ...claimLines(section(text, "What"))]) {
    const value = prefixOf(claim);
    if (value.prefix === "defect") push(value.tag, `Fix: ${value.body}`);
  }
  for (const claim of claimLines(subsection(text, "Reader brief", "Checklist"))) {
    const value = prefixOf(claim);
    if (value.prefix === "missing" || value.prefix === "partial") push(value.tag, `${value.prefix === "missing" ? "Implement" : "Finish"}: ${value.body}`);
  }
  steps.derived = true;
  return steps;
}

export function reportTopic(text: string, topic: Topic) {
  const label = topic[0].toUpperCase() + topic.slice(1);
  return {
    synthesis: subsection(text, "Reader brief", label),
    trail: section(text, label),
  };
}

// One repository path the way reports and agents write it: a path with a
// slash, or a bare file name with an extension, optionally followed by :line.
// Mirrors the anchor checker's ANCHOR_RE so what the lint accepts is what the
// page links.
const PATH_PATTERN = "(?:[A-Za-z0-9_.-]*\\/[A-Za-z0-9_./-]+|[A-Za-z0-9_-]+\\.[A-Za-z][A-Za-z0-9]{0,7})";
const REFERENCE_RE = new RegExp(`^(${PATH_PATTERN})(?::(\\d+))?$`);
const MENTION_RE = new RegExp(`(^|[\\s(])@(${PATH_PATTERN})(?::(\\d+))?`, "g");
const CODE_SPAN_RE = /(```[\s\S]*?```|`[^`\n]*`)/;

export type SourceReference = { path: string; line: number };

function link(label: string, path: string, line: number): string {
  return `[${label}](#tb-source:${encodeURIComponent(path)}:${line})`;
}

/** Parse "src/app.py:12" or "README.md" into a reference; null for anything else. */
export function sourceReference(text: string): SourceReference | null {
  const value = text.trim().replace(/[.,;:]+$/, "");
  const match = REFERENCE_RE.exec(value);
  if (!match || !/[A-Za-z]/.test(match[1])) return null;
  return { path: match[1], line: Number(match[2]) || 1 };
}

/** Turn @path:line mentions (either @[path]:line or @path:line) into source links, leaving code spans alone. */
export function sourceLinks(text: string): string {
  return text
    .split(CODE_SPAN_RE)
    .map((part, index) => {
      if (index % 2 === 1) return part;
      return part
        .replace(/@\[([^\]\n]+)\]:(\d+)/g, (_match, path: string, line: string) => link(`@${path}:${line}`, path, Number(line)))
        .replace(MENTION_RE, (_match, lead: string, path: string, line?: string) => {
          const trimmed = path.replace(/[.,;:]+$/, "");
          const trailing = path.slice(trimmed.length);
          if (!/[A-Za-z]/.test(trimmed)) return _match;
          const label = line ? `@${trimmed}:${line}` : `@${trimmed}`;
          return `${lead}${link(label, trimmed, Number(line) || 1)}${line ? "" : trailing}`;
        });
    })
    .join("");
}

export function relativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value || "Not yet";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(timestamp);
}

export type VerifiedStamp = {
  branch: string;
  commit: string;
  ts: string;
  harness: string;
  provider: string;
  model: string;
};

export function verifiedStamp(text: string): VerifiedStamp | null {
  const match = /<!-- tb:verified (.*?) -->/.exec(text);
  if (!match) return null;
  const values: Record<string, string> = {};
  for (const pair of match[1].split(/\s+/)) {
    const index = pair.indexOf("=");
    if (index <= 0) continue;
    try {
      values[pair.slice(0, index)] = decodeURIComponent(pair.slice(index + 1));
    } catch {
      values[pair.slice(0, index)] = pair.slice(index + 1);
    }
  }
  return {
    branch: values.branch || "",
    commit: values.commit || "",
    ts: values.ts || "",
    harness: values.harness || "",
    provider: values.provider || "",
    model: values.model || "",
  };
}

export function provenanceLine(stamp: VerifiedStamp | null, recordedAt: string): string {
  const parts: string[] = [];
  if (stamp?.commit) parts.push(`Verified at ${stamp.commit}${stamp.branch ? ` on ${stamp.branch}` : ""}${stamp.ts ? ` · ${relativeTime(stamp.ts)}` : ""}`);
  const generator = [stamp?.harness, stamp?.provider, stamp?.model].filter(Boolean).join(" / ");
  if (generator) parts.push(`Generated by ${generator}`);
  if (recordedAt) parts.push(`Recorded ${relativeTime(recordedAt)}`);
  return parts.join(" · ");
}
