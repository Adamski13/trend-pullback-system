"""
ILSS Telegram Notifications

Sends intraday SFP signals to Telegram.

Uses the same environment variables as TPS v2:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import os
import requests
from datetime import datetime, timezone


class ILSSTelegramNotifier:

    def __init__(self):
        # Prefer ILSS-specific vars; fall back to shared vars
        self.bot_token = (os.environ.get("ILSS_TELEGRAM_TOKEN")
                          or os.environ.get("TELEGRAM_BOT_TOKEN", ""))
        self.chat_id   = (os.environ.get("ILSS_TELEGRAM_CHAT_ID")
                          or os.environ.get("TELEGRAM_CHAT_ID", ""))
        self.enabled   = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            print("  Telegram: not configured (set ILSS_TELEGRAM_TOKEN / ILSS_TELEGRAM_CHAT_ID)")

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        url  = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            resp = requests.post(url, data=data, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"  Telegram send failed: {e}")
            return False

    def send_sfp_signal(self, signal: dict, paper: bool = True) -> bool:
        """
        Send an SFP signal alert.

        signal keys:
            symbol, label, session, direction,
            level_type, entry, stop, target_r, stop_dist,
            units, nav, bar_time
        """
        mode_tag = "PAPER" if paper else "LIVE"
        now      = datetime.now(timezone.utc).strftime("%H:%M UTC")

        direction = signal["direction"].upper()
        emoji     = "🟢" if direction == "BULL" else "🔴"

        # Determine target description
        hold_h = signal.get("hold_hours", "?")

        level_map = {
            "prev_day_low":  "PDL", "prev_day_high": "PDH",
            "asian_low":     "Asian Low", "asian_high": "Asian High",
        }
        level_str = level_map.get(signal["level_type"], signal["level_type"])

        lines = [
            f"{emoji} *ILSS SFP Signal* [{mode_tag}]",
            f"_{now}_",
            f"",
            f"*{signal['label']}* — {signal['session'].replace('_', ' ').title()}",
            f"Direction: *{direction}* sweep of {level_str}",
            f"",
            f"Entry:  `{signal['entry']:.5g}`",
            f"Stop:   `{signal['stop']:.5g}`  ({signal['stop_dist_r']:.2f}× ATR)",
            f"Exit:   time stop {hold_h}h",
            f"",
            f"Size:   {signal['units']:,.0f} units  (1% risk)",
            f"NAV:    ${signal['nav']:,.2f}",
        ]

        if not paper:
            lines.append(f"")
            lines.append(f"_Order placed on OANDA_")

        return self.send("\n".join(lines))

    def send_halt_alert(self, reason: str, nav: float) -> bool:
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"⛔ *ILSS Risk Halt* — {now}\n\n"
            f"Reason: {reason}\n"
            f"NAV: ${nav:,.2f}\n\n"
            f"_No new entries until next session._"
        )
        return self.send(msg)

    def send_heartbeat(self, nav: float, sessions: list[str],
                       day_trades: int, active_trades: int) -> bool:
        now      = datetime.now(timezone.utc).strftime("%H:%M UTC")
        sess_str = ", ".join(sessions) if sessions else "none"
        msg      = (f"💓 ILSS alive — {now}\n"
                    f"Sessions: {sess_str}\n"
                    f"Trades today: {day_trades}  Active: {active_trades}\n"
                    f"NAV: {nav:,.2f}")
        return self.send(msg)

    def send_status(self, nav: float, open_trades: int, day_trades: int,
                    day_pnl_pct: float) -> bool:
        now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        sign = "+" if day_pnl_pct >= 0 else ""
        msg  = (
            f"📊 *ILSS Daily Status* — {now}\n\n"
            f"NAV: ${nav:,.2f}\n"
            f"Day P&L: {sign}{day_pnl_pct*100:.2f}%\n"
            f"Open trades: {open_trades}\n"
            f"Trades today: {day_trades}"
        )
        return self.send(msg)
