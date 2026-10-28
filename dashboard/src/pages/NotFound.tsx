import { Link } from "react-router-dom";

import { notFound as C } from "../copy";

export function NotFound() {
  return (
    <div className="page">
      <header className="page-head"><h1>{C.title}</h1><p>{C.text}</p></header>
      <p><Link className="arrow-link" to="/">{C.home}</Link></p>
    </div>
  );
}
