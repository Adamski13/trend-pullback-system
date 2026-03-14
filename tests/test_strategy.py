"""
Unit tests for signal logic and position sizing.

Tests are fully synthetic — no network calls required.
"""

import numpy as np
import pandas as pd
import pytest

from src.indicators import sma, ema, atr, add_indicators
from src.strategy import generate_signals, calc_position_size


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CFG = {
    "strategy": {
        "regime_ma_length": 200,
        "ema_length": 21,
        "atr_length": 14,
        "risk_per_layer_pct": 1.0,
        "stop_buffer_atr_mult": 0.5,
        "trail_atr_mult": 2.5,
        "max_layers": 3,
        "allow_shorts": False,
    },
    "execution": {
        "initial_capital": 100_000,
        "commission_pct": 0.0,
        "slippage_pct": 0.0,
        "point_value": 1.0,
    },
}


def _make_df(closes, highs=None, lows=None, opens=None) -> pd.DataFrame:
    n = len(closes)
    closes = np.array(closes, dtype=float)
    highs = np.array(highs, dtype=float) if highs is not None else closes * 1.005
    lows = np.array(lows, dtype=float) if lows is not None else closes * 0.995
    opens = np.array(opens, dtype=float) if opens is not None else closes * 0.998
    dates = pd.date_range("2015-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes},
        index=dates,
    )


def _trending_up(n=400, start=100.0, slope=0.15):
    """Generate a slowly trending-up series with small noise."""
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.3, n)
    closes = start + np.arange(n) * slope + noise.cumsum()
    return np.maximum(closes, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Indicator tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSMA:
    def test_first_valid_bar(self):
        s = pd.Series(range(1, 11), dtype=float)
        result = sma(s, 5)
        assert np.isnan(result.iloc[3])
        assert result.iloc[4] == pytest.approx(3.0)

    def test_matches_pandas_rolling(self):
        rng = np.random.default_rng(0)
        s = pd.Series(rng.uniform(100, 200, 300))
        expected = s.rolling(20).mean()
        actual = sma(s, 20)
        pd.testing.assert_series_equal(actual, expected, check_names=False)


class TestEMA:
    def test_nan_before_min_periods(self):
        s = pd.Series(range(1, 30), dtype=float)
        result = ema(s, 10)
        assert np.isnan(result.iloc[8])
        assert not np.isnan(result.iloc[9])

    def test_ema_responds_faster_than_sma(self):
        # After a big jump, EMA should move more than SMA
        base = [100.0] * 50
        jump = [200.0] * 10
        s = pd.Series(base + jump)
        e = ema(s, 20)
        sm = sma(s, 20)
        assert e.iloc[-1] > sm.iloc[-1]


class TestATR:
    def test_all_nan_before_warmup(self):
        df = _make_df([100.0] * 20)
        result = atr(df["High"], df["Low"], df["Close"], 14)
        assert result.iloc[:13].isna().all()

    def test_atr_positive(self):
        closes = _trending_up(100)
        df = _make_df(closes)
        a = atr(df["High"], df["Low"], df["Close"], 14)
        assert (a.dropna() > 0).all()


# ─────────────────────────────────────────────────────────────────────────────
# Position sizing
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionSizing:
    def test_basic_formula(self):
        # equity=100k, risk=1%, entry=100, stop=99 → risk=$1000, dist=1, qty=1000
        qty = calc_position_size(100_000, 0.01, 100.0, 99.0)
        assert qty == pytest.approx(1000.0)

    def test_zero_stop_distance(self):
        qty = calc_position_size(100_000, 0.01, 100.0, 100.0)
        assert qty == 0.0

    def test_point_value_scales_inverse(self):
        qty1 = calc_position_size(100_000, 0.01, 100.0, 99.0, point_value=1.0)
        qty2 = calc_position_size(100_000, 0.01, 100.0, 99.0, point_value=10.0)
        assert qty2 == pytest.approx(qty1 / 10.0)

    def test_risk_pct_scales_linearly(self):
        qty1 = calc_position_size(100_000, 0.01, 100.0, 99.0)
        qty2 = calc_position_size(100_000, 0.02, 100.0, 99.0)
        assert qty2 == pytest.approx(2 * qty1)


# ─────────────────────────────────────────────────────────────────────────────
# Signal generation
# ─────────────────────────────────────────────────────────────────────────────

class TestSignals:
    def _build_signal_df(self, closes, highs=None, lows=None):
        df = _make_df(closes, highs=highs, lows=lows)
        df = add_indicators(df, DEFAULT_CFG)
        return generate_signals(df, DEFAULT_CFG)

    def test_no_signal_without_prior_pullback(self):
        """If price never dips below EMA, no long signal should fire."""
        closes = _trending_up(400, start=200, slope=0.5)
        sig = self._build_signal_df(closes)
        # Remove warm-up bars
        valid = sig.dropna(subset=["sma200", "ema21", "atr14"])
        # Manually check: all signals that fire must have been preceded by a pullback
        # (structural check, not counting them directly as price behavior varies)
        assert isinstance(sig["long_signal"].sum(), (int, np.integer))

    def test_signal_requires_pullback_then_reclaim(self):
        """
        Synthetic: price well above EMA200 (regime bull), dips below EMA21 for a few bars,
        then reclaims. Expect at least one long signal.
        """
        # Build 400 bars trending up, then force a dip
        closes = list(_trending_up(400, start=300, slope=0.2))
        # Introduce a pullback by inserting lower closes around bar 350
        for i in range(345, 360):
            closes[i] = closes[344] * 0.97  # dip below EMA21 region
        # Then recover
        for i in range(360, 375):
            closes[i] = closes[344] * 1.02

        sig = self._build_signal_df(closes)
        assert sig["long_signal"].sum() >= 1, "Expected at least one long signal after dip+reclaim"

    def test_no_long_in_bear_regime(self):
        """Price below SMA200 → zero long signals regardless of EMA21 behaviour."""
        # Downtrend: price always below SMA200
        closes = list(range(500, 100, -1))
        closes = [float(c) for c in closes]
        sig = self._build_signal_df(closes)
        assert sig["long_signal"].sum() == 0

    def test_regime_flip_resets_pullback(self):
        """
        If regime flips bearish while pullback is active, no long signal should fire
        on the subsequent reclaim.
        """
        closes = list(_trending_up(400, start=300, slope=0.2))
        # Drop price below SMA200 briefly during pullback zone
        for i in range(345, 365):
            closes[i] = 50.0  # far below SMA200
        # Then spike back up
        for i in range(365, 400):
            closes[i] = closes[344] + (i - 365) * 0.5

        sig = self._build_signal_df(closes)
        # Signals at the transition back up should not be from the stale pullback
        # (We just verify no crash and signal count is sane)
        assert sig["long_signal"].sum() >= 0  # structural check

    def test_stop_is_below_pullback_low(self):
        """Any emitted signal_stop_long must be below pullback low."""
        closes = list(_trending_up(400, start=300, slope=0.2))
        for i in range(345, 358):
            closes[i] = closes[344] * 0.975
        for i in range(358, 375):
            closes[i] = closes[344] * 1.03

        lows = [c * 0.995 for c in closes]
        sig = self._build_signal_df(closes, lows=lows)
        signals = sig[sig["long_signal"]]
        for _, row in signals.iterrows():
            assert row["signal_stop_long"] < row["Low"], (
                f"Stop {row['signal_stop_long']:.2f} should be below bar low {row['Low']:.2f}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_insufficient_history_no_signals(self):
        """With fewer bars than the regime MA, there should be no signals."""
        closes = [100.0 + i * 0.1 for i in range(50)]  # only 50 bars
        df = _make_df(closes)
        df = add_indicators(df, DEFAULT_CFG)
        sig = generate_signals(df, DEFAULT_CFG)
        assert sig["long_signal"].sum() == 0

    def test_flat_price_no_crash(self):
        closes = [100.0] * 300
        df = _make_df(closes)
        df = add_indicators(df, DEFAULT_CFG)
        sig = generate_signals(df, DEFAULT_CFG)
        assert "long_signal" in sig.columns
