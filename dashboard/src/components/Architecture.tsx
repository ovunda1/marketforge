// The architecture diagram (M11, M11b), drawn as SVG in black lines: wide for desktops,
// stacked for phones. Every box links to its code (the link's name is the box's own text).
// The arrows follow the real data path (docs/services.md). Labels are in copy.ts.
import { REPO_URL } from "../lib/links";
import { architecture as A } from "../copy";

const CODE = (path: string) => `${REPO_URL}/blob/main/${path}`;
const N = A.nodes;
const HREF = {
  browser: CODE("dashboard/src/App.tsx"), gateway: CODE("services/api/app.py"), redis: CODE("services/common/keys.py"),
  journals: CODE("docs/journal-format.md"), projector: CODE("services/projector/projection.py"),
  postgres: CODE("services/db/models.py"), worker: CODE("services/worker/tasks.py"),
};

interface BoxProps { x: number; y: number; w: number; h: number; title: string; lines: readonly string[]; href: string;
                     hot?: boolean }

function Box({ x, y, w, h, title, lines, href, hot }: BoxProps) {
  return (
    <a href={href}>
      <g className={hot ? "box hot" : "box"}>
        <rect x={x} y={y} width={w} height={h} />
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
  const M = N.matcher;
  return (
    <>
      <Box x={x} y={y} w={230} h={175} hot title={M.title} lines={[]} href={CODE("services/matcher/core.py")} />
      <a href={CODE("engine/src/order_book.cpp")}>
        <g className="inner">
          <rect x={x + 14} y={y + 30} width={202} height={56} />
          <text x={x + 26} y={y + 52} className="strong">{M.engine}</text>
          <text x={x + 26} y={y + 72}>{M.engineLine}</text>
        </g>
      </a>
      <g className="inner" aria-hidden="true">
        <rect x={x + 14} y={y + 96} width={202} height={66} />
        <text x={x + 26} y={y + 118} className="strong">{M.commit}</text>
        <text x={x + 26} y={y + 137}>{M.commitLines[0]}</text>
        <text x={x + 26} y={y + 154}>{M.commitLines[1]}</text>
      </g>
    </>
  );
}

export function Architecture() {
  return (
    <>
      <svg viewBox="0 0 980 350" role="group" aria-labelledby="arch-t" aria-describedby="arch-d" className="arch arch-h">
        <title id="arch-t">{A.title}</title>
        <desc id="arch-d">{A.desc}</desc>
        <Defs />
        <Box x={10} y={105} w={130} h={78} title={N.browser.title} lines={N.browser.wide} href={HREF.browser} />
        <Box x={190} y={95} w={160} h={98} title={N.gateway.title} lines={N.gateway.wide} href={HREF.gateway} />
        <Box x={400} y={95} w={150} h={98} title={N.redis.title} lines={N.redis.wide} href={HREF.redis} />
        <Matcher x={600} y={20} />
        <Box x={850} y={40} w={120} h={62} title={N.journals.title} lines={N.journals.wide} href={HREF.journals} />
        <Box x={850} y={150} w={120} h={62} title={N.projector.title} lines={N.projector.wide} href={HREF.projector} />
        <Box x={850} y={260} w={120} h={62} title={N.postgres.title} lines={N.postgres.wide} href={HREF.postgres} />
        <Box x={400} y={250} w={170} h={62} title={N.worker.title} lines={N.worker.wide} href={HREF.worker} />
        <Arrow d="M140,144 L186,144" label={A.arrows.http} lx={142} ly={136} />
        <Arrow d="M350,130 L396,130" />
        <Arrow d="M396,160 L354,160" />
        <Arrow d="M550,130 L596,130" label={A.arrows.cmds} lx={556} ly={122} />
        <Arrow d="M596,160 L554,160" label={A.arrows.replies} lx={552} ly={178} />
        <Arrow d="M830,71 L846,71" />
        <Arrow d="M910,102 L910,146" />
        <Arrow d="M910,212 L910,256" />
        <Arrow d="M475,193 L475,246" label={A.arrows.jobs} lx={482} ly={226} />
        <Arrow d="M270,193 L270,338 L900,338 L900,326" label={A.arrows.reads} lx={280} ly={330} />
      </svg>
      <svg viewBox="0 0 360 820" role="group" aria-labelledby="arch-vt" aria-describedby="arch-vd" className="arch arch-v">
        <title id="arch-vt">{A.title}</title>
        <desc id="arch-vd">{A.desc}</desc>
        <Defs />
        <Box x={60} y={10} w={240} h={62} title={N.browser.title} lines={N.browser.tall} href={HREF.browser} />
        <Box x={60} y={110} w={240} h={62} title={N.gateway.title} lines={N.gateway.tall} href={HREF.gateway} />
        <Box x={60} y={210} w={240} h={62} title={N.redis.title} lines={N.redis.tall} href={HREF.redis} />
        <Matcher x={65} y={310} />
        <Box x={10} y={530} w={160} h={62} title={N.journals.title} lines={N.journals.tall} href={HREF.journals} />
        <Box x={190} y={530} w={160} h={62} title={N.worker.title} lines={N.worker.tall} href={HREF.worker} />
        <Box x={10} y={630} w={160} h={62} title={N.projector.title} lines={N.projector.tall} href={HREF.projector} />
        <Box x={10} y={730} w={160} h={62} title={N.postgres.title} lines={N.postgres.tall} href={HREF.postgres} />
        <Arrow d="M180,72 L180,106" />
        <Arrow d="M180,172 L180,206" />
        <Arrow d="M180,272 L180,306" label={A.arrows.cmdsReplies} lx={188} ly={294} />
        <Arrow d="M120,485 L90,526" />
        <Arrow d="M300,241 L340,241 L340,526" label={A.arrows.jobs} lx={306} ly={505} />
        <Arrow d="M90,592 L90,626" />
        <Arrow d="M90,692 L90,726" />
      </svg>
    </>
  );
}
