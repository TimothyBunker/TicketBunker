import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sourceLinks } from "./report";

type Props = {
  children: string;
  onOpenSource?: (path: string, line: number) => void;
  onCopySource?: (path: string, line: number) => void;
  compact?: boolean;
};

// A claim line starts with its trust tag. Speculation carries a grounding rank
// ("grounded — …", "adjacent:", "ungrounded"); the dash or colon after it is
// punctuation, not content.
const CLAIM_RE = /^\s*\[(observation|real|speculation|inference)\]\s*(?:(grounded|adjacent|ungrounded)\s*(?:[—–:-]\s*)?)?/i;

export const TRUST_LABELS: Record<string, { label: string; title: string }> = {
  observation: { label: "Observed", title: "A mechanical Git or ticket fact produced by the pipeline, reproducible from the same repository state." },
  real: { label: "Verified", title: "A concrete fact the agent located; its path or line anchor was checked at publish time." },
  inference: { label: "Inferred", title: "An agent inference recorded in the working store." },
  "speculation:grounded": { label: "Speculation · grounded", title: "Interpretation anchored to a verified finding." },
  "speculation:adjacent": { label: "Speculation · adjacent", title: "Interpretation anchored to the right area but no specific symbol." },
  "speculation:ungrounded": { label: "Speculation · ungrounded", title: "Interpretation with nothing concrete found; talk to the reporter or a person of interest." },
  speculation: { label: "Speculation", title: "Interpretation, not a verified fact." },
};

export function claimTag(text: string): { key: string; rest: string } | null {
  const match = CLAIM_RE.exec(text);
  if (!match) return null;
  const tag = match[1].toLowerCase();
  const grounding = match[2]?.toLowerCase();
  const key = tag === "speculation" && grounding ? `speculation:${grounding}` : tag;
  return { key, rest: text.slice(match[0].length) };
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
    return { chip: <TrustChip tagKey={claim.key} />, tone: claim.key, children: [claim.rest, ...others] };
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

export default function Markdown({
  children,
  onOpenSource,
  onCopySource,
  compact = false,
}: Props) {
  return (
    <div className={compact ? "markdown compact" : "markdown"}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          li({ children: items, node: _node, ...rest }) {
            const replaced = withChip(Children.toArray(items));
            if (!replaced) return <li {...rest}>{items}</li>;
            const tone = replaced.tone.startsWith("speculation") ? "speculation" : replaced.tone;
            return <li {...rest} className={`claim claim-${tone}`}>{replaced.chip}<span className="claim-text">{replaced.children}</span></li>;
          },
          a({ href = "", children: label }) {
            if (href.startsWith("#tb-source:")) {
              const raw = href.slice("#tb-source:".length);
              const split = raw.lastIndexOf(":");
              const path = decodeURIComponent(raw.slice(0, split));
              const line = Number(raw.slice(split + 1)) || 1;
              return (
                <span className="source-reference">
                  <button type="button" onClick={() => onOpenSource?.(path, line)}>
                    {label}
                  </button>
                  <button
                    type="button"
                    className="copy-source"
                    aria-label={`Copy absolute path for ${path} line ${line}`}
                    title="Copy absolute path"
                    onClick={() => onCopySource?.(path, line)}
                  >
                    ⧉
                  </button>
                </span>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {label}
              </a>
            );
          },
        }}
      >
        {sourceLinks(children)}
      </ReactMarkdown>
    </div>
  );
}
