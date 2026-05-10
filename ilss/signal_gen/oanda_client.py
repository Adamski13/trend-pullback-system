"""
OANDA v20 REST API client for ILSS signal generator.

Extends the TPS v2 client with M15 intraday candles and order management
suitable for intraday stop-loss entries.
"""

import requests
import pandas as pd

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_URL     = "https://api-fxtrade.oanda.com"


class OandaClient:
    def __init__(self, token: str, account_id: str, env: str = "practice"):
        self.token      = token
        self.account_id = account_id
        self.env        = env
        self.base_url   = PRACTICE_URL if env == "practice" else LIVE_URL

        self._session = requests.Session()
        self._session.headers.update({
            "Authorization":          f"Bearer {token}",
            "Content-Type":           "application/json",
            "Accept-Datetime-Format": "RFC3339",
        })

    # ── internal helpers ──────────────────────────────────────────────────────

    def _get(self, path: str, params: dict = None) -> dict:
        r = self._session.get(f"{self.base_url}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._session.post(f"{self.base_url}{path}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, body: dict) -> dict:
        r = self._session.put(f"{self.base_url}{path}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    # ── account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        """Return key account metrics: NAV, balance, unrealized P&L."""
        data = self._get(f"/v3/accounts/{self.account_id}/summary")
        acc  = data["account"]
        return {
            "nav":           float(acc["NAV"]),
            "balance":       float(acc["balance"]),
            "unrealized_pl": float(acc["unrealizedPL"]),
            "margin_used":   float(acc["marginUsed"]),
            "currency":      acc["currency"],
        }

    # ── positions ─────────────────────────────────────────────────────────────

    def get_positions(self) -> dict:
        """
        Return current open positions.
        Dict mapping instrument → net signed units (positive = long).
        """
        data = self._get(f"/v3/accounts/{self.account_id}/positions")
        positions = {}
        for pos in data.get("positions", []):
            instr       = pos["instrument"]
            long_units  = float(pos["long"]["units"])
            short_units = float(pos["short"]["units"])   # negative
            net         = long_units + short_units
            if net != 0:
                positions[instr] = net
        return positions

    def get_open_trades(self) -> list[dict]:
        """Return list of open trade dicts (id, instrument, units, price, stopLoss)."""
        data   = self._get(f"/v3/accounts/{self.account_id}/openTrades")
        trades = []
        for t in data.get("trades", []):
            sl = t.get("stopLossOrder", {}).get("price")
            trades.append({
                "id":         t["id"],
                "instrument": t["instrument"],
                "units":      float(t["currentUnits"]),
                "open_price": float(t["price"]),
                "stop_loss":  float(sl) if sl else None,
            })
        return trades

    # ── market data ───────────────────────────────────────────────────────────

    def get_candles(self, instrument: str, count: int = 250,
                    granularity: str = "D") -> pd.DataFrame:
        """
        Fetch mid-price OHLC candles (daily by default).
        Only complete (closed) candles returned.
        """
        data = self._get(
            f"/v3/instruments/{instrument}/candles",
            params={"count": count, "granularity": granularity, "price": "M"},
        )
        return self._candles_to_df(data, normalize_date=(granularity == "D"))

    def get_m15_candles(self, instrument: str, count: int = 300) -> pd.DataFrame:
        """
        Fetch M15 mid-price OHLC candles.
        Only complete (closed) candles returned.
        Index is UTC DatetimeIndex.
        """
        data = self._get(
            f"/v3/instruments/{instrument}/candles",
            params={"count": count, "granularity": "M15", "price": "M"},
        )
        return self._candles_to_df(data, normalize_date=False)

    def _candles_to_df(self, data: dict, normalize_date: bool = False) -> pd.DataFrame:
        rows = []
        for c in data.get("candles", []):
            if not c.get("complete", False):
                continue
            ts = pd.to_datetime(c["time"], utc=True)
            if normalize_date:
                ts = ts.normalize()
            rows.append({
                "date":   ts,
                "Open":   float(c["mid"]["o"]),
                "High":   float(c["mid"]["h"]),
                "Low":    float(c["mid"]["l"]),
                "Close":  float(c["mid"]["c"]),
                "Volume": int(c["volume"]),
            })

        if not rows:
            raise ValueError("No complete candles returned")

        df = pd.DataFrame(rows).set_index("date")
        df = df[~df.index.duplicated(keep="last")]
        df.sort_index(inplace=True)
        return df

    # ── orders ────────────────────────────────────────────────────────────────

    def place_market_order(self, instrument: str, units: int,
                           stop_loss: float | None = None) -> dict:
        """
        Place a market order with an optional stop-loss order attached.

        units > 0 → long entry
        units < 0 → short entry (Phase 2+ only)
        """
        order = {
            "type":         "MARKET",
            "instrument":   instrument,
            "units":        str(units),
            "timeInForce":  "FOK",
            "positionFill": "REDUCE_FIRST",
        }
        if stop_loss is not None:
            order["stopLossOnFill"] = {
                "price":       f"{stop_loss:.5f}",
                "timeInForce": "GTC",
            }
        return self._post(f"/v3/accounts/{self.account_id}/orders", {"order": order})

    def close_trade(self, trade_id: str) -> dict:
        """Close a specific trade by ID."""
        return self._put(
            f"/v3/accounts/{self.account_id}/trades/{trade_id}/close",
            {"units": "ALL"},
        )

    def close_all_trades(self) -> list[dict]:
        """Close every open trade on the account. Used for emergency hard stop."""
        trades  = self.get_open_trades()
        results = []
        for t in trades:
            try:
                results.append(self.close_trade(t["id"]))
            except Exception as e:
                results.append({"error": str(e), "trade_id": t["id"]})
        return results
