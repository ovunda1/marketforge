// The architecture diagram (M11), drawn as SVG: wide for desktops, stacked for phones.
// Every box links to its code (the link's name is the box's own text). The arrows follow
// the real data path (docs/services.md).
import { REPO_URL } from "../App";

const CODE = (path: string) => `${REPO_URL}/blob/main/${path}`;

interface BoxProps { x: number; y: number; w: number; h: number; title: string; lines: string[]; href: string; hot?: boolean }

function Box({ x, y, w, h, title, lines, href, hot }: BoxProps) {
  return (
    <a href={href}>
      <g className={hot ? "box hot" : "box"}>
        <rect x={x} y={y} width={w} height={h} rx="4" />
        <text x={x + 12} y={y + 22} className="bt">{title}</text>
        {lines.map((l, i) => <text key={l} x={x + 12} y={y + 42 + i * 17}>{l}</text>)}
      </g>
    </a>
  );
}

function Arrow({ d, label, lx, ly }: { d: string; label?: string; lx?: number; ly?: number }) {
  return (
    <g className="arrow" aria-hidden="true">
      <path d={d} markerEnd="url(#arrow-head)" />
      {label && <text x={lx} y={ly} className="al">{label}</text>}
    </g>
  );
}

const DESC = "Browsers talk REST and WebSocket to two stateless API gateway processes. The gateways forward commands " +
  "over a Redis stream to one matcher process, the single writer, which owns every symbol's engine and journal and " +
  "commits each batch (intake log fsync, journal, journal fsync) before any reply or market data goes out. A " +
  "projector replays the journals into Postgres, which the gateways read; a backtest worker runs the same engine on " +
  "historical data.";

function Defs() {
  return (
    <defs>
      <marker id="arrow-head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
        <path d="M0,0 L8,4 L0,8 z" />
      </marker>
    </defs>
  );
}

function Matcher({ x, y }: { x: number; y: number }) {
  return (
    <>
      <Box x={x} y={y} w={230} h={175} hot title="Matcher (single writer)" lines={[]} href={CODE("services/matcher/core.py")} />
      <a href={CODE("engine/src/order_book.cpp")}>
        <g className="inner">
          <rect x={x + 14} y={y + 30} width={202} height={56} rx="3" />
          <text x={x + 26} y={y + 52} className="strong">Engine per symbol (C++20)</text>
          <text x={x + 26} y={y + 72}>RiskGate → OrderBook</text>
        </g>
      </a>
      <g className="inner" aria-hidden="true">
        <rect x={x + 14} y={y + 96} width={202} height={66} rx="3" />
        <text x={x + 26} y={y + 118} className="strong">Group commit per batch</text>
        <text x={x + 26} y={y + 137}>intake fsync → journal</text>
        <text x={x + 26} y={y + 154}>→ fsync → replies, md</text>
      </g>
    </>
  );
}

export function Architecture() {
  return (
    <>
      <svg viewBox="0 0 980 350" role="group" aria-labelledby="arch-t" aria-describedby="arch-d" className="arch arch-h">
        <title id="arch-t">MarketForge architecture</title>
        <desc id="arch-d">{DESC}</desc>
        <Defs />
        <Box x={10} y={105} w={130} h={78} title="Browser" lines={["dashboard (React)", "REST + WebSocket"]} href={CODE("dashboard/src/App.tsx")} />
        <Box x={190} y={95} w={160} h={98} title="API gateway ×2" lines={["FastAPI, uvicorn", "stateless", "never imports engine"]} href={CODE("services/api/app.py")} />
        <Box x={400} y={95} w={150} h={98} title="Redis" lines={["mf:cmd stream", "market-data pub/sub", "backtest job queue"]} href={CODE("services/common/keys.py")} />
        <Matcher x={600} y={20} />
        <Box x={850} y={40} w={120} h={62} title="Journals" lines={["binary, append"]} href={CODE("docs/journal-format.md")} />
        <Box x={850} y={150} w={120} h={62} title="Projector" lines={["journal → SQL"]} href={CODE("services/projector/projection.py")} />
        <Box x={850} y={260} w={120} h={62} title="Postgres" lines={["projection"]} href={CODE("services/db/models.py")} />
        <Box x={400} y={250} w={170} h={62} title="Backtest worker" lines={["arq · same engine"]} href={CODE("services/worker/tasks.py")} />
        <Arrow d="M140,144 L186,144" label="HTTP/WS" lx={142} ly={136} />
        <Arrow d="M350,130 L396,130" />
        <Arrow d="M396,160 L354,160" />
        <Arrow d="M550,130 L596,130" label="cmds" lx={556} ly={122} />
        <Arrow d="M596,160 L554,160" label="replies" lx={552} ly={178} />
        <Arrow d="M830,71 L846,71" />
        <Arrow d="M910,102 L910,146" />
        <Arrow d="M910,212 L910,256" />
        <Arrow d="M475,193 L475,246" label="jobs" lx={482} ly={226} />
        <Arrow d="M270,193 L270,338 L900,338 L900,326" label="reads (orders, positions, runs)" lx={280} ly={330} />
      </svg>
      <svg viewBox="0 0 360 820" role="group" aria-labelledby="arch-vt" aria-describedby="arch-vd" className="arch arch-v">
        <title id="arch-vt">MarketForge architecture</title>
        <desc id="arch-vd">{DESC}</desc>
        <Defs />
        <Box x={60} y={10} w={240} h={62} title="Browser" lines={["dashboard: REST + WebSocket"]} href={CODE("dashboard/src/App.tsx")} />
        <Box x={60} y={110} w={240} h={62} title="API gateway ×2" lines={["FastAPI, stateless, no engine"]} href={CODE("services/api/app.py")} />
        <Box x={60} y={210} w={240} h={62} title="Redis" lines={["command stream, pub/sub, jobs"]} href={CODE("services/common/keys.py")} />
        <Matcher x={65} y={310} />
        <Box x={10} y={530} w={160} h={62} title="Journals" lines={["binary, append"]} href={CODE("docs/journal-format.md")} />
        <Box x={190} y={530} w={160} h={62} title="Backtest worker" lines={["same engine"]} href={CODE("services/worker/tasks.py")} />
        <Box x={10} y={630} w={160} h={62} title="Projector" lines={["journal → SQL"]} href={CODE("services/projector/projection.py")} />
        <Box x={10} y={730} w={160} h={62} title="Postgres" lines={["projection"]} href={CODE("services/db/models.py")} />
        <Arrow d="M180,72 L180,106" />
        <Arrow d="M180,172 L180,206" />
        <Arrow d="M180,272 L180,306" label="commands / replies" lx={188} ly={294} />
        <Arrow d="M120,485 L90,526" />
        <Arrow d="M300,241 L340,241 L340,526" label="jobs" lx={306} ly={505} />
        <Arrow d="M90,592 L90,626" />
        <Arrow d="M90,692 L90,726" />
      </svg>
    </>
  );
}
