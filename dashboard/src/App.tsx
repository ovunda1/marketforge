// The frame (M11): skip link, the simulated-market banner on every page, navigation, routes.
import { useEffect } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { TryTrading } from "./components/TryTrading";
import { useSession } from "./lib/session";
import { BacktestRun } from "./pages/BacktestRun";
import { Backtests } from "./pages/Backtests";
import { HowItWorks } from "./pages/HowItWorks";
import { Market } from "./pages/Market";
import { NewBacktest } from "./pages/NewBacktest";
import { NotFound } from "./pages/NotFound";
import { Overview } from "./pages/Overview";

export const REPO_URL = "https://github.com/ovunda1/marketforge";

const TITLES: [RegExp, string][] = [[/^\/market/, "Live market"], [/^\/backtests\/new/, "New backtest"],
  [/^\/backtests\/./, "Backtest result"], [/^\/backtests/, "Backtests"], [/^\/how-it-works/, "How it works"],
  [/^\/$/, "Overview"]];

export function App() {
  const { session } = useSession();
  const { pathname } = useLocation();
  useEffect(() => {
    const t = TITLES.find(([re]) => re.test(pathname))?.[1] ?? "Not found";
    document.title = `${t} · MarketForge`;
  }, [pathname]);
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <div className="banner" role="note">
        <strong>Simulated market</strong> · fictional symbols · practice money only
      </div>
      <header className="nav">
        <Link className="brand" to="/"><span className="logo" aria-hidden="true">MF</span>MarketForge</Link>
        <nav aria-label="Main">
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/market">Live market</NavLink>
          <NavLink to="/backtests">Backtests</NavLink>
          <NavLink to="/how-it-works">How it works</NavLink>
        </nav>
        <div className="nav-right">
          <a className="nav-link" href={REPO_URL}>GitHub</a>
          {session ? <span className="account-chip" title="Your practice account">{session.name}</span>
            : <TryTrading compact />}
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
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="footer">
        MarketForge is a learning project. The market is simulated and every symbol on the Live market page is
        fictional; backtests use historical data and are not investment advice.
      </footer>
    </>
  );
}
