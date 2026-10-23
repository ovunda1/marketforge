// The slim strip on Live market (M11), always visible: connection state (an icon and a
// word, never color alone), matcher epoch, journal seq_no, md_seq and round-trip time.
import type { MarketFeed } from "../lib/feeds";
import { formatInt } from "../lib/money";
import { useTech } from "../lib/tech";
import { Info } from "./Info";

const LABEL = { connecting: "Connecting", live: "Live", resyncing: "Resyncing", reconnecting: "Reconnecting",
                offline: "Offline" } as const;

export function StatusStrip({ feed }: { feed: MarketFeed | null }) {
  const { show, toggle } = useTech();
  const status = feed?.status ?? "connecting";
  const c = feed?.client;
  return (
    <div className="status" data-testid="status-strip">
      <span className={`state ${status}`} role="status" data-testid="conn-state">
        <span className="dot" aria-hidden="true" />{LABEL[status]}
      </span>
      <span>epoch <b className="num" data-testid="epoch">{c?.epoch ?? "–"}</b> <Info term="epoch" /></span>
      <span>seq_no <b className="num" data-testid="seq-no">{c?.seqNo != null ? formatInt(c.seqNo) : "–"}</b> <Info term="seqNo" label="seq_no" /></span>
      <span>md_seq <b className="num" data-testid="md-seq">{c && c.mdSeq >= 0 ? formatInt(c.mdSeq) : "–"}</b> <Info term="mdSeq" label="md_seq" /></span>
      <span>RTT <b className="num" data-testid="rtt">{feed?.rttMs != null ? `${feed.rttMs.toFixed(1)} ms` : "–"}</b> <Info term="rtt" label="round-trip time" /></span>
      <button type="button" className="linkish" onClick={toggle} aria-pressed={show} data-testid="tech-toggle">
        {show ? "Hide technical details" : "Show technical details"}
      </button>
    </div>
  );
}
