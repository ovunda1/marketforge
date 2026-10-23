// The dashboard's market-data client (M11): a line-for-line port of services/client.py's
// BookClient. Both are tested against tests/fixtures/marketdata/cases.json, generated from
// a real matcher, so they provably agree.
//
// Synced after a snapshot; an update with the next md_seq in the same epoch keeps it
// synced; an older one is ignored; anything else (a gap, a new epoch) makes it unsynced
// until the next snapshot, and onMessage returns false so the caller asks for one. After
// a slow-consumer overflow the server sends "resnapshot" and then a snapshot by itself.

export type Level = [price: number, qty: number, orders: number];

export interface MdMessage {
  type: string;
  epoch?: number;
  md_seq?: number;
  seq_no?: number;
  bids?: Level[];
  asks?: Level[];
  [key: string]: unknown;
}

export class BookClient {
  epoch: number | null = null;
  mdSeq = -1;
  seqNo: number | null = null;
  synced = false;
  resnapshots = 0;
  private bidMap = new Map<number, Level>();
  private askMap = new Map<number, Level>();

  constructor(public readonly symbol: string) {}

  /** Applies one message; false means "unsynced, a snapshot is needed". */
  onMessage(msg: MdMessage): boolean {
    const kind = msg.type;
    if (kind === "snapshot") {
      if (this.epoch !== null) this.resnapshots += 1;
      this.epoch = msg.epoch!;
      this.mdSeq = msg.md_seq!;
      this.seqNo = msg.seq_no ?? null;
      this.bidMap = new Map(msg.bids!.map((lv) => [lv[0], [lv[0], lv[1], lv[2]]]));
      this.askMap = new Map(msg.asks!.map((lv) => [lv[0], [lv[0], lv[1], lv[2]]]));
      this.synced = true;
      return true;
    }
    if (kind === "resnapshot") {
      this.synced = false;
      return false;
    }
    if (kind !== "update") return this.synced;
    if (!this.synced) return false;
    if (msg.epoch === this.epoch && msg.md_seq! <= this.mdSeq) return true; // already in the snapshot
    if (msg.epoch !== this.epoch || msg.md_seq !== this.mdSeq + 1) {
      this.synced = false;
      return false;
    }
    for (const [side, levels] of [[this.bidMap, msg.bids!], [this.askMap, msg.asks!]] as const) {
      for (const [price, qty, count] of levels) {
        if (qty === 0) side.delete(price);
        else side.set(price, [price, qty, count]);
      }
    }
    this.mdSeq = msg.md_seq!;
    this.seqNo = msg.seq_no ?? this.seqNo;
    return true;
  }

  bids(): Level[] {
    return [...this.bidMap.values()].sort((a, b) => b[0] - a[0]);
  }

  asks(): Level[] {
    return [...this.askMap.values()].sort((a, b) => a[0] - b[0]);
  }
}
