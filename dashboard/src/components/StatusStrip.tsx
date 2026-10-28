// The thin black bar on Live market (M11, M11b), always visible: connection state (a mark
// and a word, never color alone), matcher epoch, journal seq_no, md_seq and round-trip time.
import { status as C } from "../copy";
import type { MarketFeed } from "../lib/feeds";
import { formatInt } from "../lib/money";
import { useTech } from "../lib/tech";
import { Term } from "./Term";

export function StatusStrip({ feed }: { feed: MarketFeed | null }) {
  const { show, toggle } = useTech();
  const status = feed?.status ?? "connecting";
  const c = feed?.client;
  return (
    <div className="status" data-testid="status-strip">
      <span className={`state ${status}`} role="status" data-testid="conn-state">
        <span className="dot" aria-hidden="true" />{C.state[status]}
      </span>
      <span><Term k="epoch">{C.epoch}</Term> <b className="num" data-testid="epoch">{c?.epoch ?? "–"}</b></span>
      <span><Term k="seqNo">{C.seqNo}</Term> <b className="num" data-testid="seq-no">{c?.seqNo != null ? formatInt(c.seqNo) : "–"}</b></span>
      <span><Term k="mdSeq">{C.mdSeq}</Term> <b className="num" data-testid="md-seq">{c && c.mdSeq >= 0 ? formatInt(c.mdSeq) : "–"}</b></span>
      <span><Term k="rtt">{C.rtt}</Term> <b className="num" data-testid="rtt">{feed?.rttMs != null ? `${feed.rttMs.toFixed(1)} ms` : "–"}</b></span>
      <button type="button" className="linkish" onClick={toggle} aria-pressed={show} data-testid="tech-toggle">
        {show ? C.hideTech : C.showTech}
      </button>
    </div>
  );
}
