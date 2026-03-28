"""
OANDA v20 REST API client.
Handles account info, positions, candle data, and order placement.
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

    # ── internal helpers ─────────────────────────────────────────────────────

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

    # ── account ──────────────────────────────────────────────────────────────

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
        Dict mapping instrument -> net signed units (positive = long).
        """
        data = self._get(f"/v3/accounts/{self.account_id}/positions")
        positions = {}
        for pos in data.get("positions", []):
            instr       = pos["instrument"]
            long_units  = float(pos["long"]["units"])
            short_units = float(pos["short"]["units"])   # negative value
            net         = long_units + short_units
            if net != 0:
                positions[instr] = net
        return positions

    # ── market data ───────────────────────────────────────────────────────────

    def get_candles(self, instrument: str, count: int = 400,
                    granularity: str = "D") -> pd.DataFrame:
        """
        Fetch daily mid-price OHLC candles for `instrument`.

        Returns DataFrame with DatetimeIndex and columns:
            Open, High, Low, Close, Volume
        Only complete (closed) candles are included.
        """
        data = self._get(
            f"/v3/instruments/{instrument}/candles",
            params={"count": count, "granularity": granularity, "price": "M"},
        )
        rows = []
        for c in data.get("candles", []):
            if not c.get("complete", False):
                continue
            rows.append({
                "date":   pd.to_datetime(c["time"]).normalize(),
                "Open":   float(c["mid"]["o"]),
                "High":   float(c["mid"]["h"]),
                "Low":    float(c["mid"]["l"]),
                "Close":  float(c["mid"]["c"]),
                "Volume": int(c["volume"]),
            })

        if not rows:
            raise ValueError(f"No complete candles returned for {instrument}")

        df = pd.DataFrame(rows).set_index("date")
        df = df[~df.index.duplicated(keep="last")]
        df.sort_index(inplace=True)
        return df

    def get_current_price(self, instrument: str) -> float:
        """Return latest mid-price (may include today's incomplete bar)."""
        data = self._get(
            f"/v3/instruments/{instrument}/candles",
            params={"count": 1, "granularity": "D", "price": "M"},
        )
        candles = data.get("candles", [])
        if candles:
            return float(candles[-1]["mid"]["c"])
        raise ValueError(f"Could not fetch price for {instrument}")

    # ── orders ────────────────────────────────────────────────────────────────

    def place_order(self, instrument: str, units: float) -> dict:
        """
        Place a market order.
        units > 0 → buy (open/increase long)
        units < 0 → sell (reduce/close long)

        Uses REDUCE_FIRST so partial closes work correctly.
        """
        body = {
            "order": {
                "type":         "MARKET",
                "instrument":   instrument,
                "units":        str(int(units)),
                "timeInForce":  "FOK",
                "positionFill": "REDUCE_FIRST",
            }
        }
        return self._post(f"/v3/accounts/{self.account_id}/orders", body)

    def close_long(self, instrument: str) -> dict:
        """Close the entire long position for an instrument."""
        body = {"longUnits": "ALL"}
        return self._put(
            f"/v3/accounts/{self.account_id}/positions/{instrument}/close",
            body,
        )
