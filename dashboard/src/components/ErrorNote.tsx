// A reject or error as a sentence with a fix (M11), never a bare code. The code itself is
// shown only with "Show technical details".
import { errorNote as C } from "../copy";
import type { Explained } from "../lib/rejects";
import { useTech } from "../lib/tech";

export function ErrorNote({ e, role = "alert" }: { e: Explained; role?: "alert" | "status" }) {
  const { show } = useTech();
  return (
    <div className="note error" role={role}>
      <strong>{e.title}</strong>
      <p>{e.message}</p>
      {e.fields?.map((f) => <p key={f.field + f.message}><b>{f.field}</b>: {f.message}</p>)}
      {e.fix && <p><b>{C.whatToDo}</b> {e.fix}</p>}
      {show && <p className="mono muted">{e.code}{e.reason ? ` · ${e.reason}` : ""}</p>}
    </div>
  );
}
