import { Children, cloneElement, createContext, isValidElement, memo, useContext, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import FloatingActions, { type FloatingAction } from "./FloatingActions";
import { sourceLinks, sourceReference } from "./report";

// What the page can do with one file reference. The ticket page provides
// these once; every Markdown surface under it offers the same three actions.
// Where a reference was read: the report topic, scope, and branch. Sending it
// to chat lands in that topic, not a default one.
export type ReportPlace = { category: string; scope: string; branch: string };

export type SourceActions = {
  open?: (path: string, line: number) => void;
  copy?: (path: string, line: number) => void;
  ask?: (path: string, line: number, place: ReportPlace | null) => void;
};

export const SourceActionsContext = createContext<SourceActions>({});
export const ReportPlaceContext = createContext<ReportPlace | null>(null);

type Props = {
  children: string;
  onOpenSource?: (path: string, line: number) => void;
  onCopySource?: (path: string, line: number) => void;
  onAskSource?: (path: string, line: number) => void;
  compact?: boolean;
};

// A claim line starts with its trust tag. Speculation carries a grounding rank
// ("grounded — …", "adjacent:", "ungrounded"); the dash or colon after it is
// punctuation, not content.
const CLAIM_RE = /^\s*\[(observation|real|speculation|inference)\]\s*(?:(grounded|adjacent|ungrounded)\s*(?:[—–:-]\s*)?)?/i;
// A proactive claim names its aspect after the tag: "Risk:" for something
// that could cause the reader problems, "Next:" for one thing to do now.
const ASPECT_RE = /^\s*(Risk|Next|Ask|Done|Partial|Missing|Unclear|Defect):\s*/i;

export const ASPECT_LABELS: Record<string, { label: string; title: string }> = {
  defect: { label: "Defect", title: "An error the agent verified by reading both sides: a declaration that does not match its definition, a duplicate definition, a misspelled identifier, a missing import. Always anchored." },
  risk: { label: "Risk", title: "Something that could cause you problems: a bug, an untested change, a conflict, a stale branch, an unclear owner." },
  next: { label: "Next", title: "One concrete thing you could do now." },
  ask: { label: "Ask", title: "Who to talk to about this ticket, and why." },
  done: { label: "Done", title: "This ticket item is present in the code, at the cited place." },
  partial: { label: "Partial", title: "This ticket item is started but not complete, at the cited place." },
  missing: { label: "Missing", title: "Nothing in the code satisfies this ticket item yet." },
  unclear: { label: "Unclear", title: "The agent could not tell whether this item is satisfied." },
};

// The Standing verdict: "Verdict: blocked — sentence".
const VERDICT_RE = /^\s*Verdict:\s*(not started|in progress|blocked|looks done)\s*(?:[—–:-]\s*)?/i;

export const VERDICT_LABELS: Record<string, string> = {
  "not started": "Not started",
  "in progress": "In progress",
  blocked: "Blocked",
  "looks done": "Looks done",
};

/** Split a Standing verdict claim into its verdict and the sentence after it. */
export function verdictOf(text: string): { verdict: string; rest: string } | null {
  const claim = claimTag(text);
  const body = claim ? claim.rest : text;
  const match = VERDICT_RE.exec(body);
  if (!match) return null;
  return { verdict: match[1].toLowerCase(), rest: body.slice(match[0].length) };
}

export const TRUST_LABELS: Record<string, { label: string; title: string }> = {
  observation: { label: "Observed", title: "A mechanical Git or ticket fact produced by the pipeline, reproducible from the same repository state." },
  real: { label: "Verified", title: "A concrete fact the agent located; its path or line anchor was checked at publish time." },
  inference: { label: "Inferred", title: "An agent inference recorded in the working store." },
  "speculation:grounded": { label: "Speculation · grounded", title: "Interpretation anchored to a verified finding." },
  "speculation:adjacent": { label: "Speculation · adjacent", title: "Interpretation anchored to the right area but no specific symbol." },
  "speculation:ungrounded": { label: "Speculation · ungrounded", title: "Interpretation with nothing concrete found; talk to the reporter or a person of interest." },
  speculation: { label: "Speculation", title: "Interpretation, not a verified fact." },
};

export function claimTag(text: string): { key: string; rest: string; aspect?: string } | null {
  const match = CLAIM_RE.exec(text);
  if (!match) return null;
  const tag = match[1].toLowerCase();
  const grounding = match[2]?.toLowerCase();
  const key = tag === "speculation" && grounding ? `speculation:${grounding}` : tag;
  const rest = text.slice(match[0].length);
  const aspect = ASPECT_RE.exec(rest);
  if (aspect) return { key, rest: rest.slice(aspect[0].length), aspect: aspect[1].toLowerCase() };
  return { key, rest };
}

function AspectChip({ aspect }: { aspect: string }) {
  const entry = ASPECT_LABELS[aspect];
  if (!entry) return null;
  return <span className={`aspect-chip ${aspect}`} title={entry.title}>{entry.label}</span>;
}

function TrustChip({ tagKey }: { tagKey: string }) {
  const entry = TRUST_LABELS[tagKey] || TRUST_LABELS.speculation;
  const tone = tagKey.startsWith("speculation") ? tagKey.split(":")[1] || "speculation" : tagKey;
  return <span className={`trust-chip ${tone}`} title={entry.title}>{entry.label}</span>;
}

function withChip(nodes: ReactNode[]): { chip: ReactNode; tone: string; children: ReactNode[] } | null {
  if (nodes.length === 0) return null;
  const [first, ...others] = nodes;
  if (typeof first === "string") {
    const claim = claimTag(first);
    if (!claim) return null;
    const chip = claim.aspect ? <><TrustChip tagKey={claim.key} /><AspectChip aspect={claim.aspect} /></> : <TrustChip tagKey={claim.key} />;
    return { chip, tone: claim.aspect ? `${claim.key} aspect-${claim.aspect}` : claim.key, children: [claim.rest, ...others] };
  }
  if (isValidElement(first) && (first as ReactElement<{ children?: ReactNode }>).type === "p") {
    const element = first as ReactElement<{ children?: ReactNode }>;
    const inner = Children.toArray(element.props.children);
    const replaced = withChip(inner);
    if (!replaced) return null;
    return { chip: replaced.chip, tone: replaced.tone, children: [cloneElement(element, undefined, ...replaced.children), ...others] };
  }
  return null;
}

// One file reference. Clicking it shows what can be done with it rather than
// doing one thing: open the editor there, copy the absolute path, or send the
// reference into the coworker chat.
function SourceRef({ path, line, label }: { path: string; line: number; label: ReactNode }) {
  const actions = useContext(SourceActionsContext);
  const place = useContext(ReportPlaceContext);
  const anchor = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const items: FloatingAction[] = [];
  if (actions.open) items.push({ label: "Open in editor", onSelect: () => actions.open?.(path, line) });
  if (actions.copy) items.push({ label: "Copy path", onSelect: () => actions.copy?.(path, line) });
  if (actions.ask) items.push({ label: "Send to chat", onSelect: () => actions.ask?.(path, line, place) });
  if (items.length === 0) return <>{label}</>;
  return (
    <span ref={anchor} className={open ? "source-reference open" : "source-reference"}>
      <button type="button" className="source-ref" aria-haspopup="true" aria-expanded={open} title={`${path}, line ${line}`} onClick={() => setOpen((value) => !value)}>
        {label}
      </button>
      {open && <FloatingActions label={`Actions for ${path}`} actions={items} anchor={anchor} onClose={() => setOpen(false)} />}
    </span>
  );
}

// The element map is one module-level object: react-markdown treats a new
// component identity as a new element type and would rebuild the DOM on every
// parent render, which drops any text selection the reader is holding.
const COMPONENTS: Components = {
  li({ children: items, node: _node, ...rest }) {
    const replaced = withChip(Children.toArray(items));
    if (!replaced) return <li {...rest}>{items}</li>;
    const [trust, ...aspects] = replaced.tone.split(" ");
    const tone = trust.startsWith("speculation") ? "speculation" : trust;
    const grade = trust.startsWith("speculation:") ? [`grade-${trust.split(":")[1]}`] : [];
    return <li {...rest} className={["claim", `claim-${tone}`, ...grade, ...aspects].join(" ")}>{replaced.chip}<span className="claim-text">{replaced.children}</span></li>;
  },
  code({ children: value, className, node: _node, ...rest }) {
    const raw = String(value ?? "");
    const reference = className || raw.includes("\n") ? null : sourceReference(raw);
    if (reference) return <SourceRef path={reference.path} line={reference.line} label={<code {...rest}>{raw}</code>} />;
    return <code className={className} {...rest}>{value}</code>;
  },
  a({ href = "", children: label }) {
    if (href.startsWith("#tb-source:")) {
      const raw = href.slice("#tb-source:".length);
      const split = raw.lastIndexOf(":");
      const path = decodeURIComponent(raw.slice(0, split));
      const line = Number(raw.slice(split + 1)) || 1;
      return <SourceRef path={path} line={line} label={label} />;
    }
    return (
      <a href={href} target="_blank" rel="noreferrer">
        {label}
      </a>
    );
  },
};

const REMARK_PLUGINS = [remarkGfm];

function Markdown({ children, onOpenSource, onCopySource, onAskSource, compact = false }: Props) {
  const provided = useContext(SourceActionsContext);
  const actions = useMemo<SourceActions>(
    () => ({ open: onOpenSource || provided.open, copy: onCopySource || provided.copy, ask: onAskSource || provided.ask }),
    [onOpenSource, onCopySource, onAskSource, provided],
  );
  const source = useMemo(() => sourceLinks(children), [children]);
  return (
    <SourceActionsContext.Provider value={actions}>
      <div className={compact ? "markdown compact" : "markdown"}>
        <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
          {source}
        </ReactMarkdown>
      </div>
    </SourceActionsContext.Provider>
  );
}

export default memo(Markdown);
