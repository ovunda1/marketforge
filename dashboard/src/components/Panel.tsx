import type { ReactNode } from "react";

export function Panel({ id, title, right, focal, className, children, headingId }: {
  id?: string; title: ReactNode; right?: ReactNode; focal?: boolean; className?: string; children: ReactNode;
  headingId?: string;
}) {
  const hid = headingId ?? (id ? `${id}-h` : undefined);
  return (
    <section id={id} className={`panel${focal ? " focal" : ""}${className ? ` ${className}` : ""}`}
             aria-labelledby={hid}>
      <div className="panel-h">
        <h2 id={hid}>{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}
