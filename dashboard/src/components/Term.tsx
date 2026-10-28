// A term with a dotted underline that explains itself (M11b). Mouse: hover opens. Keyboard:
// focus opens. Touch: a tap opens, a tap anywhere else closes. Esc closes in every case.
// The open tooltip describes the button for screen readers; it is clamped to the viewport.
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { glossary, type Term as TermKey } from "../copy";

export function Term({ k, children }: { k: TermKey; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [shift, setShift] = useState(0);
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const tip = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = () => {
      setOpen(false);
      setPinned(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !tip.current) return setShift(0);
    const r = tip.current.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    setShift(r.left < 8 ? 8 - r.left : r.right > vw - 8 ? vw - 8 - r.right : 0); // measured unshifted
  }, [open]);

  return (
    <span className="term-wrap" ref={wrap}
          onPointerEnter={(e) => e.pointerType === "mouse" && setOpen(true)}
          onPointerLeave={(e) => e.pointerType === "mouse" && !pinned && setOpen(false)}>
      <button type="button" className="term" aria-expanded={open} aria-describedby={open ? id : undefined}
              onClick={() => { setPinned(!pinned); setOpen(!pinned); }}
              onFocus={(e) => e.currentTarget.matches(":focus-visible") && setOpen(true)}
              onBlur={() => !pinned && setOpen(false)}>
        {children ?? glossary[k].term}
      </button>
      {open && <span role="tooltip" id={id} className="tip" ref={tip}
                     style={shift ? { transform: `translateX(${shift}px)` } : undefined}>{glossary[k].text}</span>}
    </span>
  );
}
