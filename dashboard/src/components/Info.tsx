// An ⓘ that explains a term (M11): opens on hover, focus or click; Esc closes. The button
// is labelled with the term, and the open tooltip describes it for screen readers.
import { useEffect, useId, useRef, useState } from "react";

import { GLOSSARY, type Term } from "../content/glossary";

export function Info({ term, label }: { term: Term; label?: string }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        setPinned(false);
      }
    };
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setPinned(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);
  return (
    <span className="info-wrap" ref={ref} onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => !pinned && setOpen(false)}>
      <button type="button" className="info" aria-label={`What is ${label ?? term}?`} aria-expanded={open}
              aria-describedby={open ? id : undefined}
              onClick={() => { setPinned(!pinned); setOpen(!pinned); }}
              onFocus={() => setOpen(true)} onBlur={() => !pinned && setOpen(false)}>
        i
      </button>
      {open && <span role="tooltip" id={id} className="tip">{GLOSSARY[term]}</span>}
    </span>
  );
}
