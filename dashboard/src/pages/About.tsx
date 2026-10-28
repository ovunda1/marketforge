// About (M11b): who built this, what it is, and where the code is.
import { Link } from "react-router-dom";

import { REPO_URL } from "../lib/links";
import { about as C, site } from "../copy";

export function About() {
  return (
    <div className="page">
      <header className="page-head">
        <h1>{C.title}</h1>
        <p>{C.lead}</p>
      </header>
      <div className="prose">
        {C.paragraphs.map((p) => <p key={p}>{p}</p>)}
        <p className="advice" data-testid="not-advice">{site.notAdvice}</p>
        <h2>{C.linksTitle}</h2>
        <ul>
          <li><a href={REPO_URL}>{C.code}</a></li>
          <li><Link to="/how-it-works">{site.nav.how}</Link></li>
          <li><Link to="/glossary">{site.nav.glossary}</Link></li>
        </ul>
      </div>
    </div>
  );
}
