// The 30-second guided tour of Live market (M11): five short steps, each pointing at one
// part of the page. Starts once per tab (remembered in sessionStorage), can be reopened
// from its button, and is skippable at any point (Skip, or Esc). It is a non-modal
// dialog: focus moves to it, the page stays usable.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export const TOUR_KEY = "mf.tour.seen";

const STEPS = [
  { target: "#book", title: "The order book",
    text: "Sellers' prices are on top (▼ asks), buyers' below (▲ bids). A trade happens when a new order meets a price on the other side." },
  { target: "#tape", title: "Trades",
    text: "Every trade prints here as it happens. The arrow shows which side started it." },
  { target: "[data-testid=status-strip]", title: "Live connection",
    text: "This strip shows the connection to the exchange. If an update is ever missed, the page notices and refetches the book." },
  { target: "#order", title: "Place an order",
    text: "Choose Buy or Sell, a quantity and a price. The sentence under the form says exactly what the order will do before you place it." },
  { target: "#order", title: "Practice money only",
    text: "Click Try trading for a practice account: no sign-up, and nothing here is real money or a real company." },
];

export function seenTour(): boolean {
  try {
    return window.sessionStorage.getItem(TOUR_KEY) === "1";
  } catch {
    return true; // no storage: don't pop up on every page load
  }
}

function markSeen(): void {
  try {
    window.sessionStorage.setItem(TOUR_KEY, "1");
  } catch {
    // not remembered
  }
}

export function Tour({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 120, left: 16 });
  const ref = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLHeadingElement>(null);
  const current = STEPS[step]!;

  const close = useCallback(() => {
    markSeen();
    setStep(0);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  useLayoutEffect(() => {
    if (!open) return;
    const el = document.querySelector<HTMLElement>(current.target);
    el?.classList.add("tour-target");
    el?.scrollIntoView({ block: "nearest", behavior: "auto" });
    const place = () => {
      const r = el?.getBoundingClientRect();
      const w = ref.current?.offsetWidth ?? 340, h = ref.current?.offsetHeight ?? 180;
      if (!r) return setPos({ top: 120, left: 16 });
      const vw = window.innerWidth, vh = window.innerHeight;
      let left = r.right + 12 + w <= vw ? r.right + 12 : r.left - 12 - w >= 0 ? r.left - 12 - w : Math.max(16, Math.min(vw - w - 16, r.left));
      let top = r.right + 12 + w <= vw || r.left - 12 - w >= 0 ? Math.max(16, r.top) : r.bottom + 12;
      if (top + h > vh - 16) top = Math.max(16, vh - h - 16);
      left = Math.max(16, Math.min(left, vw - w - 16));
      setPos({ top, left });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    headRef.current?.focus();
    return () => {
      el?.classList.remove("tour-target");
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, step, current.target]);

  if (!open) return null;
  const last = step === STEPS.length - 1;
  return (
    <div className="tour" role="dialog" aria-modal="false" aria-labelledby="tour-h" ref={ref} style={pos}
         data-testid="tour">
      <div className="tour-step">{step + 1} / {STEPS.length}</div>
      <h2 id="tour-h" tabIndex={-1} ref={headRef}>{current.title}</h2>
      <p>{current.text}</p>
      <div className="tour-actions">
        <button type="button" className="linkish" onClick={close}>Skip tour (Esc)</button>
        <span style={{ display: "flex", gap: 8 }}>
          {step > 0 && <button type="button" className="btn outline small" onClick={() => setStep(step - 1)}>Back</button>}
          <button type="button" className="btn primary small" onClick={() => (last ? close() : setStep(step + 1))}>
            {last ? "Done" : "Next →"}
          </button>
        </span>
      </div>
    </div>
  );
}
