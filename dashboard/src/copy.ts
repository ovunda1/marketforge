// Every sentence the dashboard shows, in one place, so the wording can be changed without
// touching a component. Reject and error messages live in content/rejects.json (a pytest
// checks it against the engine's and gateway's codes) and are re-exported here.
// Functions are templates: they take numbers already formatted (no money math here).
import rejects from "./content/rejects.json";

export { rejects };

export const site = {
  name: "MarketForge",
  author: "Ovunda Aguma",
  skip: "Skip to content",
  banner: "Simulated market",
  nav: { overview: "Overview", market: "Live market", backtests: "Backtests", how: "How it works",
         glossary: "Glossary", about: "About", github: "GitHub" },
  navLabel: "Main",
  footerLabel: "Footer",
  footerBuilt: "Built by",
  notAdvice: "Backtests use historical data and are not investment advice.",
  accountTitle: "Your practice account",
  titles: { market: "Live market", newBacktest: "New backtest", run: "Backtest result", backtests: "Backtests",
            how: "How it works", overview: "Overview", glossary: "Glossary", about: "About", notFound: "Not found" },
};

export const overview = {
  intro: "A stock exchange and a research backtester, built from scratch: a C++20 order book with a risk gate and " +
    "a replayable journal, Python bindings, a backtester with trading costs, and web services around a single " +
    "matching process.",
  ctaMarket: "Watch the live market",
  ctaBacktests: "See the backtests",
  ctaCode: "Read the code",
  heroUnit: "commands per second",
  heroLabel: (workload: string, cpu: string, cores: string) =>
    `One core, risk gate and order book, ${workload} workload. ${cpu} (${cores}).`,
  sections: { perf: "Engine", tests: "Correctness", showcase: "Backtest", arch: "Architecture" },
  perfCaption: "Commands per second and per-command latency by workload",
  perfCols: { workload: "Workload", rate: "Commands/s", p50: "p50", p99: "p99", p999: "p99.9" },
  perfNote: (reps: number, n: string, seed: number, cpu: string, compiler: string, commit: string, step: number) =>
    `narrow: few deep levels near a fixed mid. wide: a drifting mid, orders far from it. Median of ${reps} × ${n} ` +
    `commands, seed ${seed}. ${cpu}, ${compiler}, commit ${commit}. Latency is quantised to the clock step (${step} ns).`,
  missing: (make: string) => `Not recorded yet: run ${make}.`,
  make: { bench: "make bench", facts: "make facts", difftest: "make difftest-long", showcase: "make showcase" },
  difftest: "Differential test",
  difftestRuns: (runs: string) => runs,
  difftestOk: "C++ and Python engines identical",
  difftestBad: "Mismatch: see results/difftest.json",
  result: "Result",
  sanitizers: "Sanitizers",
  sanitizersValue: "ASan and UBSan, full suite in CI",
  passed: (n: string) => `${n} passed`,
  testSuites: "Test suites",
  testsNote: (date: string | null) =>
    "The differential test sends random commands through the C++ engine and an independent Python engine and " +
    `requires every event to match.${date ? ` Test counts from ${date}.` : ""}`,
  verdict: (significant: boolean, dsr: string, runs: number, threshold: number) =>
    `${significant ? "Statistically significant" : "Not statistically significant"}: deflated Sharpe ${dsr} ` +
    `after ${runs} logged backtests (needs ${threshold} or more).`,
  showcaseCaption: "Time-series momentum showcase, gross and net of costs",
  metric: "Metric",
  gross: "Gross",
  net: "Net of costs",
  sharpe: "Sharpe ratio",
  annualReturn: "Annual return",
  maxDrawdown: "Max drawdown",
  showcaseNote: (size: string, from: string, to: string, cost: string) =>
    `Time-series momentum on ${size} US ETFs (historical data, Tiingo), out-of-sample walk-forward ${from}–${to}. ` +
    `Costs: ${cost} a year.`,
  fullResult: "Full result",
  howLink: "How it works",
};

export const architecture = {
  title: "MarketForge architecture",
  desc: "Browsers talk REST and WebSocket to two stateless API gateway processes. The gateways forward commands " +
    "over a Redis stream to one matcher process, the single writer, which owns every symbol's engine and journal and " +
    "commits each batch (intake log fsync, journal, journal fsync) before any reply or market data goes out. A " +
    "projector replays the journals into Postgres, which the gateways read; a backtest worker runs the same engine on " +
    "historical data.",
  nodes: {
    browser: { title: "Browser", wide: ["dashboard (React)", "REST + WebSocket"], tall: ["dashboard: REST + WebSocket"] },
    gateway: { title: "API gateway ×2", wide: ["FastAPI, uvicorn", "stateless", "never imports engine"],
               tall: ["FastAPI, stateless, no engine"] },
    redis: { title: "Redis", wide: ["mf:cmd stream", "market-data pub/sub", "backtest job queue"],
             tall: ["command stream, pub/sub, jobs"] },
    journals: { title: "Journals", wide: ["binary, append"], tall: ["binary, append"] },
    projector: { title: "Projector", wide: ["journal → SQL"], tall: ["journal → SQL"] },
    postgres: { title: "Postgres", wide: ["projection"], tall: ["projection"] },
    worker: { title: "Backtest worker", wide: ["arq · same engine"], tall: ["same engine"] },
    matcher: { title: "Matcher (single writer)", engine: "Engine per symbol (C++20)", engineLine: "RiskGate → OrderBook",
               commit: "Group commit per batch", commitLines: ["intake fsync → journal", "→ fsync → replies, md"] },
  },
  arrows: { http: "HTTP/WS", cmds: "cmds", replies: "replies", jobs: "jobs", reads: "reads (orders, positions, runs)",
            cmdsReplies: "commands / replies" },
};

export const market = {
  heading: (symbol: string) => `Live market: ${symbol}`,
  symbols: "Symbols",
  fictional: "fictional company · simulated prices",
  last: "Last price",
  tourButton: "Take the 30-second tour",
  noSymbol: (s: string) => `No symbol ${s} here.`,
  seeSymbols: "See the listed symbols",
  sections: { book: "Order book", order: "Order", tape: "Trades", account: "Your account" },
};

export const status = {
  state: { connecting: "Connecting", live: "Live", resyncing: "Resyncing", reconnecting: "Reconnecting",
           offline: "Offline" },
  epoch: "epoch",
  seqNo: "seq_no",
  mdSeq: "md_seq",
  rtt: "RTT",
  showTech: "Show technical details",
  hideTech: "Hide technical details",
};

export const book = {
  hint: (levels: number) => `Top ${levels} levels. Select a price to use it.`,
  caption: "Order book: asks (sell orders) above the spread, bids (buy orders) below",
  bids: "Bids",
  asks: "Asks",
  bidsSub: "buy orders",
  asksSub: "sell orders",
  price: "Price",
  orders: "Orders",
  spread: "Spread",
  loading: "Loading the book…",
  noSellers: "No sellers",
  noBuyers: "No buyers",
  pick: (side: "bid" | "ask", price: string, qty: string, n: number) =>
    `${side === "bid" ? "Bid" : "Ask"} ${price}, ${qty} shares in ${n} order${n === 1 ? "" : "s"}. Use this price.`,
};

export const tape = {
  caption: "Recent trades, newest first",
  empty: "No trades yet.",
  price: "Price",
  qty: "Qty",
  side: "Side",
  seqNo: "seq_no",
  buy: "Buy",
  sell: "Sell",
};

export const order = {
  meta: (symbol: string, tick: string) => `${symbol}, tick ${tick}`,
  side: "Side",
  buy: "Buy",
  sell: "Sell",
  type: "Order type",
  limit: "Limit",
  market: "Market",
  qty: "Quantity",
  price: "Limit price ($)",
  marketPrice: "Price",
  bestNow: "Best available now",
  steps: (tick: string) => `Steps of ${tick}.`,
  prefilled: "Pre-filled with the best price on the other side.",
  tif: "Time in force",
  tifs: { gtc: "Good till cancelled", day: "Day (cancelled at the close)", ioc: "Immediate or cancel",
          fok: "Fill or kill" },
  postOnly: "Post-only",
  placing: "Placing…",
  placeBuy: "Place buy order",
  placeSell: "Place sell order",
  techAccount: (id: number, practice: boolean) => `account ${id} · ${practice ? "practice" : "key"}`,
};

export const account = {
  tabs: { orders: "Open orders", fills: "Fills", positions: "Positions" },
  tabsLabel: "Your account",
  cols: { symbol: "Symbol", side: "Side", price: "Price", open: "Open qty", filled: "Filled", orderId: "Order id",
          cancel: "Cancel", qty: "Qty", liquidity: "Liquidity", seqNo: "seq_no", shares: "Shares",
          last: "Last price", pnl: "P&L" },
  buy: "Buy",
  sell: "Sell",
  bought: "Bought",
  sold: "Sold",
  maker: "added (maker)",
  taker: "took (taker)",
  short: " (short)",
  cancel: "Cancel",
  cancelling: "Cancelling…",
  cancelLabel: (side: string, qty: string, symbol: string, price: string) => `Cancel ${side} ${qty} ${symbol} at ${price}`,
  noOrders: "No open orders.",
  noFills: "No fills yet.",
  noPositions: "No positions yet.",
};

export const tryTrading = {
  button: "Try trading",
  creating: "Creating…",
  intro: "Trading needs an account. A practice account takes one click: no sign-up, practice money only, and its " +
    "key stays in this browser tab.",
  main: "Try trading with a practice account",
  mainBusy: "Creating your practice account…",
  keyForm: "Use an API key",
  keyLabel: "API key",
  keyPlaceholderDemo: "…or paste an API key",
  keyPlaceholder: "Paste an API key",
  useKey: "Use key",
  dismiss: "Dismiss",
};

export const errorNote = { whatToDo: "What to do:" };

export const tech = {
  title: "Technical details",
  ws: "WebSocket",
  messages: "Messages",
  lastMessage: "Last message",
  ago: (s: string) => `${s} s ago`,
  gaps: "Gaps detected",
  resnapshots: "Resnapshots",
  reconnects: "Reconnects",
  closeCode: "Last close code",
  executions: "Executions feed",
  subscribed: "subscribed",
  connecting: "connecting",
  noAccount: "no account",
  caption: "Recent requests to the gateway",
  cols: { request: "Request", status: "Status", ms: "ms", worker: "Worker", id: "Request id" },
};

export const tour = {
  steps: [
    { target: "#book", title: "The order book",
      text: "Sellers' prices are on top (asks), buyers' below (bids). A trade happens when a new order meets a price " +
        "on the other side." },
    { target: "#tape", title: "Trades",
      text: "Every trade prints here as it happens. Side says whether a buyer or a seller started it." },
    { target: "[data-testid=status-strip]", title: "Live connection",
      text: "This bar shows the connection to the exchange. If an update is ever missed, the page notices and " +
        "fetches the book again." },
    { target: "#order", title: "Place an order",
      text: "Choose Buy or Sell, a quantity and a price. The sentence under the form says what the order will do " +
        "before you place it." },
    { target: "#order", title: "Practice money only",
      text: "Select Try trading for a practice account: no sign-up, and nothing here is real money or a real company." },
  ],
  of: (i: number, n: number) => `${i} / ${n}`,
  skip: "Skip tour (Esc)",
  back: "Back",
  next: "Next",
  done: "Done",
};

export const glossaryPage = {
  title: "Glossary",
  intro: "The terms this site uses, in plain words. Underlined terms on other pages show the same text when you " +
    "hover, focus or tap them.",
};

// Plain-language explanations: the dotted-underline tooltips and the glossary page.
export const glossary = {
  spread: { term: "Spread", text: "The gap between the best price to sell at (the bid) and the best price to buy at (the ask)." },
  orders: { term: "Orders", text: "How many separate orders make up the quantity at this price." },
  aggressor: { term: "Side", text: "Whether the order that arrived and caused the trade was a buy or a sell." },
  orderType: { term: "Order type", text: "Limit: you set the worst price you accept. Market: trade now at whatever prices are available." },
  tif: { term: "Time in force", text: "How long the order may wait if it can't fill at once." },
  postOnly: { term: "Post-only", text: "Only add to the book: if the order would trade the moment it arrives, the exchange rejects it instead." },
  liquidity: { term: "Maker and taker", text: "Maker: your order was resting and someone traded against it. Taker: your order traded on arrival." },
  pnl: { term: "P&L", text: "Profit and loss: the cash from your trades plus your shares valued at the last trade price." },
  epoch: { term: "Epoch", text: "Counts matcher restarts. A new epoch makes the page fetch a fresh copy of the book." },
  seqNo: { term: "seq_no", text: "The exchange's sequence number: every command it processes, in order, is numbered and journaled." },
  mdSeq: { term: "md_seq", text: "Market-data sequence: each book update is numbered, so a missed one is noticed and the book fetched again." },
  rtt: { term: "RTT", text: "Round-trip time: how long a ping takes from this page to the gateway and back, queueing included." },
  latency: { term: "p50, p99", text: "Per-command latency percentiles: half of all commands finish faster than p50, 99 in 100 faster than p99." },
  difftest: { term: "Differential test", text: "Random commands go through the C++ engine and an independent Python engine; every output event must match." },
  sharpe: { term: "Sharpe ratio", text: "Return per unit of risk, annualised. Noise alone can easily produce 0.5." },
  dsr: { term: "Deflated Sharpe", text: "The chance the true Sharpe beats what luck would give, given how many variations were tried." },
  drawdown: { term: "Drawdown", text: "How far the strategy fell from its previous peak, at each point in time." },
  walkForward: { term: "Walk-forward", text: "Train on a few years, test on the next year it hasn't seen, roll forward. Only test years are reported." },
  costs: { term: "Costs", text: "Commission, regulatory fees, the bid-ask spread, market impact and financing, as real trading would pay." },
  grossNet: { term: "Gross and net", text: "Gross is before costs; net is after every cost. The difference is what trading costs." },
  trainYears: { term: "Training years", text: "How many years of history each walk-forward step learns from before it is tested on the next year." },
  lookback: { term: "Lookback", text: "How many trading days of past returns the strategy uses to decide each position." },
  volWindow: { term: "Volatility window", text: "How many days of returns are used to estimate volatility when sizing positions." },
  targetVol: { term: "Target volatility", text: "The yearly volatility the strategy aims for; positions are scaled to hit it." },
  maxLeverage: { term: "Max leverage", text: "A cap on total exposure relative to capital." },
  lookbackMonths: { term: "Lookback months", text: "How many months of past returns rank the symbols against each other." },
  skipMonths: { term: "Skip months", text: "Months left out at the end of the lookback (the most recent month often reverses)." },
  quantile: { term: "Quantile", text: "The share of symbols bought (best ranked) and sold (worst ranked)." },
  universe: { term: "Universe", text: "The symbols the strategy may trade." },
  overfitting: { term: "Overfitting", text: "Trying many variations and keeping the best one makes results look better than they are. Every run here is recorded." },
} as const;
export type Term = keyof typeof glossary;

export const about = {
  title: "About",
  lead: "MarketForge was built by Ovunda Aguma as a study of how an exchange and a research backtester work.",
  paragraphs: [
    "Everything here was written from scratch: the order book and risk gate in C++20, the journal and replay, the " +
      "Python bindings and backtester, the services and this site.",
    "The live market is simulated. Its symbols are fictional and a bot provides the other side of trades. Backtests " +
      "use historical data and report results after costs, out of sample only.",
  ],
  linksTitle: "Links",
  code: "The code on GitHub",
};

// --- Messages built by lib/ (the logic stays there; the words live here) ------------------

export const inputErrors = {
  enterPrice: "Enter a price.",
  priceFormat: "Use digits with an optional decimal point, like 42.15.",
  pricePositive: "The price must be greater than zero.",
  offGrid: (tick: string, options: string[]) => `Prices move in steps of ${tick}. Try ${options.join(" or ")}.`,
  priceMax: (max: string) => `The highest price accepted is ${max}.`,
  enterQty: "Enter a quantity.",
  qtyFormat: "Use a whole number of shares, like 10.",
  qtyPositive: "The quantity must be at least 1.",
  qtyMax: (max: string) => `The largest quantity accepted is ${max}.`,
};

// The sentence under the order form. `head` is the first sentence, built by limitHead/marketHead.
export const orderSummary = {
  needQty: "Enter a quantity to see what this order will do.",
  needPrice: "Enter a price to see what this order will do.",
  verb: { buy: "Buy", sell: "Sell" },
  shares: (q: string, one: boolean) => `${q} ${one ? "share" : "shares"}`,
  marketHead: (verb: string, shares: string, symbol: string) =>
    `${verb} ${shares} of ${symbol} at the best prices available now.`,
  marketNobody: (head: string, buy: boolean) =>
    `${head} There are no ${buy ? "sellers" : "buyers"} right now, so the exchange would reject it.`,
  marketFrom: (head: string, price: string) =>
    `${head} Starting at ${price}; whatever can't fill immediately is cancelled, nothing waits in the book.`,
  limitHead: (verb: string, shares: string, symbol: string, buy: boolean, price: string) =>
    `${verb} ${shares} of ${symbol} ${buy ? `at ${price} or less` : `at ${price} or more`}.`,
  until: { gtc: "until you cancel it", day: "until you cancel it or the session closes" },
  postCross: (head: string, buy: boolean, other: string) =>
    `${head} Post-only: at this price it would trade with the ${buy ? "seller at" : "buyer at"} ${other} straight ` +
    `away, so the exchange would reject it. Move the price ${buy ? "below" : "above"} ${other}.`,
  postRest: (head: string, until: string) =>
    `${head} Post-only: it only adds to the book, never trades on arrival, and waits ${until}.`,
  crossNow: (buy: boolean, other: string) =>
    `${buy ? "Sellers are offering" : "Buyers are bidding"} ${other}, so it should start filling right away`,
  crossFok: (head: string, now: string, shares: string) =>
    `${head} ${now}, but only if all ${shares} can fill at once; otherwise nothing trades.`,
  crossIoc: (head: string, now: string) => `${head} ${now}; anything not filled immediately is cancelled.`,
  crossRest: (head: string, now: string, until: string) => `${head} ${now}; anything left waits in the book ${until}.`,
  nobody: (buy: boolean) => `There are no ${buy ? "sellers" : "buyers"} right now`,
  best: (buy: boolean, other: string) => `The best ${buy ? "offer is" : "bid is"} ${other}`,
  rest: (head: string, nobody: string, until: string, buy: boolean) =>
    `${head} ${nobody}, so it won't fill yet: it waits in the book ${until}, or until ` +
    `${buy ? "a seller comes down" : "a buyer comes up"} to your price.`,
  noRest: (head: string, nobody: string, fok: boolean) =>
    `${head} ${nobody}, so it would ${fok ? "be rejected" : "be cancelled"} without trading.`,
};

// What happened to a placed order.
export const orderResult = {
  did: { buy: "Bought", sell: "Sold" },
  ref: (id: number) => `order #${id}`,
  fill: (qty: string, price: string) => `${qty} at ${price}`,
  filledTitle: "Filled",
  filled: (did: string, qty: string, symbol: string, at: string) => `${did} ${qty} ${symbol}: ${at}.`,
  partTitle: "Partly filled",
  part: (did: string, qty: string, symbol: string, at: string, left: string, price: string, ref: string) =>
    `${did} ${qty} ${symbol} now (${at}); the other ${left} wait in the book at ${price} (${ref}).`,
  openTitle: "In the book",
  open: (side: string, qty: string, symbol: string, price: string, ref: string) =>
    `Your order to ${side} ${qty} ${symbol} at ${price} is waiting in the book (${ref}).`,
  cancelledAfter: (did: string, qty: string, symbol: string, at: string) => `${did} ${qty} ${symbol} (${at}). `,
  nothingTraded: "Nothing traded. ",
  other: (ref: string, status: string) => `${ref}: ${status}.`,
};

// Errors that do not come from rejects.json.
export const fallbackErrors = {
  clientTitle: "Something went wrong",
  clientFix: "Check your connection and try again.",
  cancelledTitle: "Cancelled",
  cancelled: (reason: string) => `Cancelled (${reason}).`,
  unsafeInteger: (path: string, value: string) => `integer beyond 2**53 - 1 at ${path} (would be rounded to ${value})`,
};

// The first sentence of every backtest result.
export const runSummary = {
  noNumbers: "This run has no performance numbers yet.",
  returned: (p: string) => `returned ${p} a year`,
  lost: (p: string) => `lost ${p} a year`,
  head: (did: string, grossReturn: string | null, sharpe: string, grossSharpe: string | null) =>
    `After costs, this strategy ${did}${grossReturn !== null ? ` (${grossReturn} before costs)` : ""} with a Sharpe ` +
    `ratio of ${sharpe}${grossSharpe !== null ? ` (${grossSharpe} before costs)` : ""}.`,
  noDsr: (head: string) => `${head} Its statistical significance could not be assessed.`,
  tried: (n: number) => `${n} parameter combination${n === 1 ? "" : "s"} tried`,
  significant: (head: string, tried: string, dsr: string, threshold: number) =>
    `${head} That is statistically significant even allowing for the ${tried} (deflated Sharpe ratio ${dsr}, at ` +
    `least ${threshold} needed).`,
  notSignificant: (head: string, tried: string, dsr: string, threshold: number) =>
    `${head} That is not statistically significant: allowing for the ${tried}, the deflated Sharpe ratio is ${dsr}, ` +
    `below the ${threshold} needed, so it could well be luck.`,
};

// The backtest form's checks (the gateway has its own, in services/common/backtest_form.py).
export const formErrors = {
  missing: "The form is missing.",
  unknown: (fields: string[]) => `Unknown field(s): ${fields.join(", ")}.`,
  dataset: "Choose one of the available datasets.",
  strategy: "Choose a strategy.",
  everyParam: "Choose at least one value for every parameter.",
  noSuchParam: (strategy: string, name: string) => `${strategy} has no parameter ${name}.`,
  oneValue: "Choose at least one value.",
  chooseFrom: (allowed: number[]) => `Choose from ${allowed.join(", ")}.`,
  tooMany: (size: number, max: number) => `That is ${size} combinations; choose at most ${max}.`,
  date: "Use a date like 2015-01-02.",
  dataStarts: (d: string) => `The data starts on ${d}.`,
  dataEnds: (d: string) => `The data ends on ${d}.`,
  endAfterStart: "The end must be after the start.",
  oneSymbol: "Choose at least one symbol.",
  twice: "A symbol is listed twice.",
  notInDataset: (s: string[]) => `Not in this dataset: ${s.join(", ")}.`,
  minSymbols: (label: string, n: number) => `${label} ranks symbols against each other: choose at least ${n}.`,
  noData: (s: string[]) => `No data in the chosen dates for ${s.join(", ")}.`,
  trainYears: "Choose 1 to 5 years of training.",
  costs: "Choose whether to include costs.",
  range: (have: number, years: number, need: number) =>
    `These dates hold ${have} trading days; walk-forward with ${years} year(s) of training and 1 year of testing ` +
    `needs at least ${need}. Start earlier or train for fewer years.`,
  lookback: (needed: number, years: number, days: number) =>
    `The longest lookback needs ${needed} trading days of history, more than ${years} year(s) of training ` +
    `(${days} days).`,
};

// --- Backtest pages -----------------------------------------------------------------------

export const strategies: Record<string, string> = {
  tsmom: "Time-series momentum", xsmom: "Cross-sectional momentum", obmom: "Order-book momentum",
};

export const backtests = {
  title: "Backtests",
  intro: "A backtest replays historical prices through a trading strategy to see how it would have done, after the " +
    "costs real trading pays. Results are out-of-sample: each year is tested with settings chosen only from the " +
    "years before it.",
  run: "Run a backtest",
  historical: "historical data",
  synthetic: "synthetic data",
  recorded: "Every run is recorded",
  serverRuns: "This server has run",
  serverRunsTail: (total: number, failed: number) =>
    ` backtest${total === 1 ? "" : "s"}${failed ? ` (${failed} failed)` : ""}. `,
  overfitting: (trials: number | null) =>
    "Trying many variations and keeping the best makes any strategy look better than it is, so every run stays in " +
    "this list, and each result is judged with a deflated Sharpe ratio that allows for the variations it tried" +
    `${trials ? ` (${trials} in this run's own parameter grid)` : ""}.`,
  cols: { run: "Run", status: "Status", data: "Data", period: "Period", sharpe: "Sharpe (net)", significance: "Significance" },
  significant: "significant",
  notSignificant: "not significant",
  backtest: "Backtest",
  showcase: "showcase",
  featured: "Showcase runs",
  featuredNote: "Fixed in git before the first real run",
  serverList: "Runs on this server",
  newestFirst: "Newest first",
  loading: "Loading…",
  empty: "No runs yet. Start one with “Run a backtest”.",
};

export const run = {
  showcase: " (showcase)",
  outOfSample: "out of sample",
  tickers: "tickers (historical data)",
  symbols: "symbols",
  costs: "costs",
  on: "on",
  off: "off",
  train: "train",
  days: "days, test",
  inRun: "backtests in this run",
  commit: "commit",
  loading: "Loading…",
  all: "All backtests",
  progress: "Progress",
  stages: { queued: "Waiting for a worker", running: "Started", validated: "Settings checked", data_loaded: "Data loaded",
            simulated: "Strategy simulated", written: "Results written", done: "Done" } as Record<string, string>,
  failedTitle: "This run failed",
  failedNote: "Failed runs stay in the list: they count as runs made.",
  equity: "Cumulative return",
  equitySub: "before and after costs",
  equityChart: "Cumulative return over the starting capital, gross and net of costs",
  gross: "Before costs (gross)",
  net: "After costs (net)",
  drawdown: "Drawdown",
  drawdownSub: "after costs",
  drawdownChart: "Drawdown from the previous peak, net of costs",
  drawdownNet: "Drawdown (net)",
  grossNet: "Gross vs net",
  grossNetCaption: "Performance before (gross) and after (net) costs",
  metric: "Metric",
  grossCol: "Gross",
  netCol: "Net of costs",
  rows: { annual_return: "Annual return", volatility: "Volatility (annual)", sharpe: "Sharpe ratio",
          sortino: "Sortino ratio", max_drawdown: "Max drawdown", calmar: "Calmar ratio", daily_hit_rate: "Days up",
          total_return: "Total return", dsr: "Deflated Sharpe ratio" } as Record<string, string>,
  costBreakdown: "Cost breakdown",
  noCosts: "No cost data.",
  costDrag: (annual: string, bps: string | null, total: string | null) =>
    `Costs took ${annual} a year off the return${bps !== null ? `, ${bps} basis points of every dollar traded` : ""}.` +
    `${total !== null ? ` Total: ${total}.` : ""}`,
  tearsheet: "Open the full tearsheet",
};

export const charts = {
  tooShort: "Not enough data to draw.",
  keys: (title: string) => `${title}. Use the left and right arrow keys to read values; the table below lists them by year.`,
  table: "Show the data as a table",
  atYearEnd: (title: string) => `${title}, at each year end`,
  date: "Date",
  costs: { spread: "Bid-ask spread", impact: "Market impact", commission: "Commission", borrow: "Borrow (short selling)",
           sec_fee: "SEC fee", financing: "Financing", carry_on_costs: "Interest lost on costs", slippage: "Slippage",
           vol_slippage: "Volatility slippage", latency: "Latency", maker_taker: "Maker-taker fees" } as Record<string, string>,
  noCostsCharged: "No costs were charged (costs were off for this run).",
  costsTitle: "Costs by component, largest first, in dollars over the whole run",
  costsCaption: "Costs by component",
  component: "Component",
  cost: "Cost",
};

export const newBacktest = {
  title: "Run a backtest",
  intro: "Pick a strategy and the history to test it on. The run is a walk-forward: it learns its settings on a few " +
    "years, is tested on the next year it hasn't seen, and rolls forward. Only the test years are reported.",
  walkForward: "How walk-forward works",
  loading: "Loading the data catalog…",
  formLabel: "Backtest settings",
  sections: { data: "Data and strategy", range: "History", universe: "Universe" },
  dataset: "Dataset",
  historical: "historical",
  synthetic: "synthetic",
  strategy: "Strategy",
  params: { lookback: "Lookback (trading days)", vol_window: "Volatility window (days)", target_vol: "Target volatility",
            max_leverage: "Max leverage", lookback_months: "Lookback (months)", skip_months: "Skip (months)",
            quantile: "Quantile traded" } as Record<string, string>,
  combinations: "Combinations tried",
  combinationsValue: (n: number, max: number) => `${n} of at most ${max}`,
  combinationsHint: "Each is tried on every training window; the best one in training is tested.",
  from: "From",
  to: "To",
  trainYears: "Training years",
  trainOption: (y: number) => `${y} year${y > 1 ? "s" : ""}, then 1 year of testing`,
  days: "Trading days in range",
  daysValue: (have: number, need: number) => `${have} (at least ${need} needed)`,
  available: (start: string, end: string) => `Data available ${start} to ${end}.`,
  symbolRange: (first: string, last: string) => `${first} to ${last}`,
  universeNote: (chosen: number, all: number, real: boolean) =>
    `${chosen} of ${all} · ${real ? "real tickers, historical data" : "fictional symbols"}`,
  symbols: "Symbols",
  selectAll: "Select all",
  clear: "Clear",
  costs: "Include trading costs (commission, fees, spread, impact, financing)",
  fix: (n: number) => `Fix the ${n} problem${n > 1 ? "s" : ""} above to run.`,
  queueing: "Queueing…",
  submit: (strategy: string, n: number) => `Run ${strategy} on ${n} symbol${n === 1 ? "" : "s"}`,
  needsAccount: "Running a backtest needs a practice account.",
  duration: "Runs take from a few seconds to a few minutes.",
  back: "Back to the list",
};

// --- Other pages --------------------------------------------------------------------------

export const how = {
  title: "How it works",
  intro: "MarketForge is a small exchange built the way real ones are: one process decides the order of everything, " +
    "writes it down before telling anyone, and everything else is a copy that can be rebuilt from that record.",
  sections: { arch: "Architecture", path: "The life of an order", code: "Read the code" },
  steps: [
    ["You select Place order.", "The browser sends it to an API gateway over REST. The price is already an integer " +
      "number of ticks: the dashboard converts your text exactly, never through floating point."],
    ["The gateway forwards it", "over a Redis stream to the matcher. Gateways hold no state; there can be any number " +
      "of them."],
    ["The matcher batches commands.", "For each batch it first writes and fsyncs an intake log, then the journal " +
      "record, runs the engine (risk checks, then matching), and fsyncs the journal. Only then do replies and market " +
      "data leave."],
    ["Market data fans out", "through Redis to every gateway, which streams it to browsers as a snapshot followed by " +
      "numbered updates. A missed number or a matcher restart makes the page fetch a fresh snapshot."],
    ["Postgres is a projection.", "A separate process replays the journals into tables for order history and " +
      "positions; it can be deleted and rebuilt at any time."],
  ] as [string, string][],
  whyTitle: "Why it is built this way",
  why: [
    ["One writer per symbol:", "no locks in the book, and a total order that replay reproduces exactly."],
    ["Integer prices", "everywhere, in C++, Python and this page: no rounding, ever."],
    ["The journal is the source of truth:", "a crash loses nothing that was acknowledged."],
    ["Honest research:", "costs are on by default, results are out-of-sample, and every run is recorded so " +
      "overfitting can't hide."],
  ] as [string, string][],
  seeBacktests: "See the backtests",
  apiDocs: "The REST API is documented at /api/docs. Use its Authorize button with a practice account's key.",
  apiDocsLink: "Open the API docs",
  links: [
    ["engine/src/order_book.cpp", "The order book", "Price-time priority matching, C++20, no allocation on the hot path"],
    ["docs/risk.md", "Pre-trade risk", "Every check, its reject code, and the order they run in"],
    ["docs/journal-format.md", "The journal", "Binary, append-only; replay reproduces every event exactly"],
    ["services/matcher/core.py", "The matcher", "The single writer: group commit per batch, idempotency, restart"],
    ["services/api/app.py", "The gateway", "FastAPI REST and WebSocket; never loads the engine"],
    ["services/client.py", "Market-data client", "Snapshot and numbered deltas; this dashboard runs a port of it"],
    ["research/runs.py", "Backtests", "Event-driven and bar backtests, walk-forward, run records"],
    ["research/backtest/costs.py", "Costs", "Commission, SEC fee, spread, impact, financing"],
    ["bench/RESULTS.md", "Benchmarks", "How the throughput and latency numbers were measured"],
    ["docs/services.md", "Services reference", "Processes, configuration, REST and WebSocket"],
  ] as [string, string, string][],
};

export const notFound = {
  title: "Not found",
  text: "There is no page at this address.",
  home: "Go to the overview",
};
