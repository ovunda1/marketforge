// A section (M11b): a numbered label on a thick rule, no box. `n` is the section number
// ("01"); `right` holds a note or controls on the same line.
import type { ReactNode } from "react";

export function Panel({ id, n, title, right, className, children, headingId }: {
  id?: string; n?: string; title: ReactNode; right?: ReactNode; className?: string; children: ReactNode;
  headingId?: string;
}) {
  const hid = headingId ?? (id ? `${id}-h` : undefined);
  return (
    <section id={id} className={`panel${className ? ` ${className}` : ""}`} aria-labelledby={hid}>
      <div className="panel-h">
        <h2 id={hid}>{n && <span className="sn">{n}</span>}{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}
