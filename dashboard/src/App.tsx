// The frame (M11, M11b): skip link, the simulated-market bar on every page, navigation,
// routes, and the footer with the author and the code.
import { useEffect } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { TryTrading } from "./components/TryTrading";
import { site } from "./copy";
import { REPO_URL } from "./lib/links";
import { useSession } from "./lib/session";
import { About } from "./pages/About";
import { BacktestRun } from "./pages/BacktestRun";
import { Backtests } from "./pages/Backtests";
import { Glossary } from "./pages/Glossary";
import { HowItWorks } from "./pages/HowItWorks";
import { Market } from "./pages/Market";
import { NewBacktest } from "./pages/NewBacktest";
import { NotFound } from "./pages/NotFound";
import { Overview } from "./pages/Overview";

const T = site.titles;
const TITLES: [RegExp, string][] = [[/^\/market/, T.market], [/^\/backtests\/new/, T.newBacktest],
  [/^\/backtests\/./, T.run], [/^\/backtests/, T.backtests], [/^\/how-it-works/, T.how], [/^\/glossary/, T.glossary],
  [/^\/about/, T.about], [/^\/$/, T.overview]];

export function App() {
  const { session } = useSession();
  const { pathname } = useLocation();
  useEffect(() => {
    const t = TITLES.find(([re]) => re.test(pathname))?.[1] ?? T.notFound;
    document.title = `${t} · ${site.name}`;
  }, [pathname]);
  return (
    <>
      <a className="skip" href="#main">{site.skip}</a>
      <div className="banner" role="note">
        <span className="wrap"><strong>{site.banner}</strong></span>
      </div>
      <header className="nav">
        <div className="wrap nav-in">
          <Link className="brand" to="/">{site.name}</Link>
          <nav aria-label={site.navLabel}>
            <NavLink to="/" end>{site.nav.overview}</NavLink>
            <NavLink to="/market">{site.nav.market}</NavLink>
            <NavLink to="/backtests">{site.nav.backtests}</NavLink>
            <NavLink to="/how-it-works">{site.nav.how}</NavLink>
          </nav>
          <div className="nav-right">
            {session ? <span className="account-chip" title={site.accountTitle}>{session.name}</span>
              : <TryTrading compact />}
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/market" element={<Market />} />
          <Route path="/market/:symbol" element={<Market />} />
          <Route path="/backtests" element={<Backtests />} />
          <Route path="/backtests/new" element={<NewBacktest />} />
          <Route path="/backtests/:id" element={<BacktestRun />} />
          <Route path="/how-it-works" element={<HowItWorks />} />
          <Route path="/glossary" element={<Glossary />} />
          <Route path="/about" element={<About />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="footer">
        <div className="wrap footer-in">
          <p className="footer-by">{site.footerBuilt} {site.author}</p>
          <nav aria-label={site.footerLabel} className="footer-links">
            <a href={REPO_URL}>{site.nav.github}</a>
            <Link to="/about">{site.nav.about}</Link>
            <Link to="/glossary">{site.nav.glossary}</Link>
          </nav>
        </div>
      </footer>
    </>
  );
}
