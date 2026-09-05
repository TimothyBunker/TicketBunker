import { useEffect, useRef, type CSSProperties, type RefObject } from "react";

export type FloatingAction = { label: string; onSelect: () => void };

// A small toolbar that appears beside the thing it acts on: a file reference
// or a text selection. It closes on an outside press or Escape, and it never
// takes focus on mousedown, so a text selection behind it stays intact.
export default function FloatingActions({
  label,
  actions,
  onClose,
  style,
  anchor,
}: {
  label: string;
  actions: FloatingAction[];
  onClose: () => void;
  style?: CSSProperties;
  anchor?: RefObject<HTMLElement | null>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function away(event: Event) {
      const target = event.target as Node;
      if (ref.current?.contains(target) || anchor?.current?.contains(target)) return;
      onClose();
    }
    function key(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [onClose, anchor]);
  return (
    <div ref={ref} className="floating-actions" role="toolbar" aria-label={label} style={style} onMouseDown={(event) => event.preventDefault()}>
      {actions.map((action) => (
        <button key={action.label} type="button" onClick={() => { action.onSelect(); onClose(); }}>{action.label}</button>
      ))}
    </div>
  );
}
