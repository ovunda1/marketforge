// How it works (M11, M11b): the architecture in a few paragraphs, with links to the code and docs.
import { Link } from "react-router-dom";

import { Architecture } from "../components/Architecture";
import { Panel } from "../components/Panel";
import { how as C } from "../copy";
import { REPO_URL } from "../lib/links";

const blob = (p: string) => `${REPO_URL}/blob/main/${p}`;

export function HowItWorks() {
  return (
    <div className="page">
      <header className="page-head">
        <h1>{C.title}</h1>
        <p>{C.intro}</p>
      </header>
      <Panel id="arch" n="01" title={C.sections.arch}>
        <Architecture />
      </Panel>
      <Panel id="path" n="02" title={C.sections.path}>
        <div className="prose">
          <ol className="steps">
            {C.steps.map(([lead, rest]) => <li key={lead}><b>{lead}</b> {rest}</li>)}
          </ol>
          <h3>{C.whyTitle}</h3>
          <ul>
            {C.why.map(([lead, rest]) => <li key={lead}><b>{lead}</b> {rest}</li>)}
          </ul>
          <p><Link to="/backtests">{C.seeBacktests}</Link></p>
          <p>{C.apiDocs} <a href="/api/docs">{C.apiDocsLink}</a></p>
        </div>
      </Panel>
      <Panel id="code" n="03" title={C.sections.code}>
        <div className="links">
          {C.links.map(([path, title, text]) => (
            <a key={path} href={blob(path)}><strong>{title}</strong><small>{text}</small><small>{path}</small></a>
          ))}
        </div>
      </Panel>
    </div>
  );
}
