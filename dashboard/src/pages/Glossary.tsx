// Glossary (M11b): every term the dotted-underline tooltips explain, in one list.
import { glossary, glossaryPage as C } from "../copy";

export function Glossary() {
  return (
    <div className="page">
      <header className="page-head">
        <h1>{C.title}</h1>
        <p>{C.intro}</p>
      </header>
      <dl className="gloss" data-testid="glossary">
        {Object.entries(glossary).map(([k, g]) => (
          <div key={k} id={k}><dt>{g.term}</dt><dd>{g.text}</dd></div>
        ))}
      </dl>
    </div>
  );
}
