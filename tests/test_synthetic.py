"""Agent-based synthetic order flow (M8): noise traders, a market maker and momentum
traders, all trading through the real engine, seeded."""

import dataclasses

import mfengine
import pytest

from research.data import schema, store
from research.sim import agents, bars as synth_bars
from research.sim.market import SEC, SynthConfig, generate

SHORT = SynthConfig(seed=7, duration_ns=30 * SEC)


@pytest.fixture(scope="module")
def short_run():
    return generate(SHORT)


def test_same_seed_same_flow(short_run) -> None:
    again = generate(SHORT)
    assert again.digest == short_run.digest
    assert again.quotes.equals(short_run.quotes) and again.trades.equals(short_run.trades)


def test_different_seed_different_flow(short_run) -> None:
    assert generate(dataclasses.replace(SHORT, seed=8)).digest != short_run.digest


def test_seed_and_config_are_stored_with_the_output(short_run) -> None:
    assert short_run.meta["seed"] == 7
    assert short_run.meta["config"] == SHORT.to_dict()
    assert SynthConfig.from_dict(short_run.meta["config"]) == SHORT


def test_output_is_a_valid_dataset(short_run, tmp_path) -> None:
    schema.validate(short_run.quotes, "quotes")
    schema.validate(short_run.trades, "trades")
    assert len(short_run.trades) > 100
    both = ((short_run.quotes["bid_qty"] > 0) & (short_run.quotes["ask_qty"] > 0)).mean()
    assert both > 0.9, both
    store.write(short_run.trades, tmp_path / "t.parquet", "trades", short_run.meta)
    assert store.load(tmp_path / "t.parquet").meta["seed"] == 7


def test_agents_only_see_expected_rejects(short_run) -> None:
    # Post-only quotes that would cross, and market orders into an empty side, are the
    # only rejects a well-behaved agent can get. Anything else is an agent bug.
    allowed = {mfengine.RejectReason.POST_ONLY_WOULD_CROSS.name, mfengine.RejectReason.NO_LIQUIDITY.name}
    assert set(short_run.reject_counts) <= allowed, short_run.reject_counts


def test_all_three_agent_types_trade(short_run) -> None:
    assert short_run.fills_by_agent["noise"] > 0
    assert short_run.fills_by_agent["market_maker"] > 0
    assert short_run.fills_by_agent["momentum"] > 0


def test_noise_arrivals_follow_the_configured_rate() -> None:
    cfg = dataclasses.replace(SHORT, duration_ns=200 * SEC)
    result = generate(cfg)
    expected = cfg.noise.rate_per_sec * 200
    assert abs(result.arrivals["noise"] - expected) < 0.05 * expected


# --- Agent decisions on scripted inputs ------------------------------------------------

def test_market_maker_skews_quotes_with_inventory() -> None:
    mm = agents.MarketMaker(agents.MarketMakerConfig(half_spread_ticks=2, qty=10, inventory_per_tick=10,
                                                     max_inventory=50), account_id=20)
    assert mm.quote_prices(10_000, 0) == (9_998, 10_002)
    assert mm.quote_prices(10_000, 25) == (9_996, 10_000)  # long: lean lower to sell
    assert mm.quote_prices(10_000, -25) == (10_000, 10_004)  # short: lean higher to buy
    assert mm.quote_prices(10_000, 50) == (None, 9_997)  # at the limit: stop buying
    assert mm.quote_prices(10_000, -50) == (10_003, None)


def test_momentum_trader_follows_the_trend_with_a_cooldown() -> None:
    cfg = agents.MomentumConfig(count=1, fast_halflife_ns=SEC, slow_halflife_ns=10 * SEC,
                                threshold_ticks=1.0, qty=5, cooldown_ns=2 * SEC, max_position=50)
    trader = agents.MomentumTrader(cfg, account_id=30)
    step = SEC // 10
    t = 0
    for _ in range(100):  # flat for 10 s
        assert trader.on_trade_price(t, 10_000) is None
        t += step
    decisions = []
    for i in range(1, 11):  # +1 tick every 100 ms
        decisions.append(trader.on_trade_price(t, 10_000 + i))
        t += step
    assert mfengine.Side.BUY in decisions
    first = decisions.index(mfengine.Side.BUY)
    assert all(d is None for d in decisions[first + 1:])  # cooldown
    for i in range(30):  # falling for 3 s
        d = trader.on_trade_price(t, 10_010 - 2 * i)
        t += step
        if d is not None:
            assert d == mfengine.Side.SELL
            break
    else:
        pytest.fail("no sell on a falling tape")


# --- Synthetic daily bars (offline research data) ---------------------------------------

def test_synthetic_bars_are_seeded_valid_and_have_actions() -> None:
    df = synth_bars.generate_bars(seed=3, symbols=("AAA", "BBB", "CCC"), n_days=400,
                                  start="2015-01-02", split_every=150, dividend_every=60)
    schema.validate(df, "bars")
    assert df.equals(synth_bars.generate_bars(seed=3, symbols=("AAA", "BBB", "CCC"), n_days=400,
                                              start="2015-01-02", split_every=150, dividend_every=60))
    assert (df["split_num"] != df["split_den"]).any()
    assert (df["dividend"] > 0).any()
    assert set(df["symbol"]) == {"AAA", "BBB", "CCC"}
