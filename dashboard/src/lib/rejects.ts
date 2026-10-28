// Every engine reject, cancel reason and gateway error as plain language with a fix (M11).
// The texts live in content/rejects.json; a Python test checks it covers exactly the
// engine's enums and the gateway's ERROR_CODES.

import { fallbackErrors as F, rejects } from "../copy";
import { ApiError } from "./api";

export interface Explained {
  title: string;
  message: string;
  fix: string;
  code: string;
  reason?: string;
  fields?: { field: string; message: string }[];
}

type Section = Record<string, { title: string; message: string; fix: string }>;
const engine = rejects.engine as Section;
const admin = rejects.admin as Section;
const apiCodes = rejects.api as Section;
const cancels = rejects.cancel as Section;

export function explain(err: unknown): Explained {
  if (err instanceof ApiError) {
    const reason = err.reason;
    const entry = (err.code === "ORDER_REJECTED" && reason && engine[reason])
      || (err.code === "ADMIN_REJECTED" && reason && admin[reason])
      || apiCodes[err.code] || apiCodes.ERROR!;
    const fields = err.details.map((d) => ({
      field: d.field ?? (Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : ""),
      message: d.message ?? d.msg ?? "",
    }));
    return { ...entry, code: err.code, reason, fields: fields.length ? fields : undefined };
  }
  const message = err instanceof Error ? err.message : String(err);
  return { title: F.clientTitle, message, fix: F.clientFix, code: "CLIENT" };
}

export function explainReject(reason: string): Explained {
  return { ...(engine[reason] ?? apiCodes.ERROR!), code: "ORDER_REJECTED", reason };
}

export function explainCancel(reason: string): Explained {
  const key = reason.toUpperCase();
  return { ...(cancels[key] ?? { title: F.cancelledTitle, message: F.cancelled(reason), fix: "" }), code: key };
}

export const codes = { engine: Object.keys(engine), admin: Object.keys(admin), api: Object.keys(apiCodes),
                       cancel: Object.keys(cancels) };
