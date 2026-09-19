import type { ButtonHTMLAttributes } from "react";

export type IconName = "plus" | "settings" | "sync" | "grid" | "trash" | "panel-left" | "panel-right" | "folder" | "device" | "shared" | "pipeline";
export function Icon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === "plus" && <path d="M12 5v14M5 12h14" />}
    {name === "settings" && <><path d="m10 3-.5 2.2-2 1.2-2.2-.6-2 3.4L5 10.8v2.4l-1.7 1.6 2 3.4 2.2-.6 2 1.2L10 21h4l.5-2.2 2-1.2 2.2.6 2-3.4-1.7-1.6v-2.4l1.7-1.6-2-3.4-2.2.6-2-1.2L14 3Z" /><circle cx="12" cy="12" r="3" /></>}
    {name === "sync" && <><path d="M20 9a8 8 0 0 0-14-3L3 9m0-5v5h5M4 15a8 8 0 0 0 14 3l3-3m0 5v-5h-5" /></>}
    {name === "grid" && <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>}
    {name === "trash" && <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></>}
    {(name === "panel-left" || name === "panel-right") && <><rect x="3" y="4" width="18" height="16" rx="2" /><path d={name === "panel-left" ? "M9 4v16" : "M15 4v16"} /></>}
    {name === "folder" && <path d="M3 19V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z" />}
    {name === "device" && <><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8m-4-4v4" /></>}
    {name === "shared" && <><circle cx="6" cy="12" r="3" /><circle cx="18" cy="5" r="3" /><circle cx="18" cy="19" r="3" /><path d="m9 10 6-4m-6 8 6 4" /></>}
    {name === "pipeline" && <><rect x="3" y="3" width="6" height="6" rx="1" /><rect x="15" y="15" width="6" height="6" rx="1" /><path d="M9 6h9v9m-4-4 4 4 4-4M6 9v12" /></>}
  </svg>;
}

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  icon: IconName; label: string; hint?: string; issue?: string; loading?: boolean;
};
export default function IconButton({ icon, label, hint, issue, loading, className = "", ...props }: Props) {
  const help = [hint || label, issue].filter(Boolean).join(". ");
  return <button {...props} type={props.type || "button"} className={`icon-action ${loading ? "is-loading" : ""} ${className}`} aria-label={label} title={help}>
    <Icon name={icon} />{issue && <span className="action-issue" aria-label={issue} />}
  </button>;
}
