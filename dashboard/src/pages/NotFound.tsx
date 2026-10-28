import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <div className="page">
      <div className="page-head"><h1>Not found</h1><p>There is no page at this address.</p></div>
      <p><Link to="/">Go to the overview</Link></p>
    </div>
  );
}
