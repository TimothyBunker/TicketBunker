import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sourceLinks } from "./report";

type Props = {
  children: string;
  onOpenSource?: (path: string, line: number) => void;
  onCopySource?: (path: string, line: number) => void;
  compact?: boolean;
};

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
