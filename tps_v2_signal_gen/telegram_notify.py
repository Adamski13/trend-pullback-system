"""
TPS v2 Telegram Notifications

Sends trading signals to your phone via Telegram bot.

Setup (one-time, takes 2 minutes):
  1. Open Telegram, search for @BotFather
  2. Send /newbot
  3. Choose a name (e.g., "TPS v2 Signals")
  4. Choose a username (e.g., "tps_v2_signals_bot")
  5. Copy the token BotFather gives you
  6. Open a chat with your new bot and send /start
  7. Get your chat_id: visit https://api.telegram.org/bot<TOKEN>/getUpdates
     in a browser — look for "chat":{"id":XXXXXXX}
  8. Set environment variables:
       export TELEGRAM_BOT_TOKEN="your-bot-token"
       export TELEGRAM_CHAT_ID="your-chat-id"
"""

import os
import requests
from datetime import datetime, timezone


class TelegramNotifier:
    """Send TPS v2 signals to Telegram."""
    
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or os.environ.get('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = chat_id or os.environ.get('TELEGRAM_CHAT_ID', '')
        self.enabled = bool(self.bot_token and self.chat_id)
        
        if not self.enabled:
            print("  Telegram: not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
    
    def send(self, message: str) -> bool:
        """Send a message. Returns True if successful."""
        if not self.enabled:
            return False
        
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {
            'chat_id': self.chat_id,
            'text': message,
            'parse_mode': 'Markdown',
        }
        
        try:
            resp = requests.post(url, data=data, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"  Telegram send failed: {e}")
            return False
    
    def send_signal_report(self, nav: float, signals: list, env: str = 'practice'):
        """
        Send the full signal report as a Telegram message.
        
        Args:
            nav: Account NAV
            signals: List of signal dicts from generate_signals.py
            env: 'practice' or 'live'
        """
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        mode = "LIVE" if env == 'live' else "PAPER"
        
        lines = [
            f"*TPS v2 — Daily Signals*",
            f"_{now}_  [{mode}]",
            f"",
            f"NAV: ${nav:,.2f}",
            f"",
        ]
        
        has_trades = False
        
        for s in signals:
            # Forecast bar
            fc = s.get('forecast', 0)
            filled = int(fc)
            bar = '█' * filled + '░' * (20 - filled)
            
            # Regime emoji
            regime = '🟢' if s.get('regime') == 'BULL' else '🔴'
            
            # Action
            action = s.get('action', 'HOLD')
            if action == 'HOLD':
                action_str = '⏸ HOLD'
            elif action == 'ENTER':
                action_str = f"🟢 ENTER {abs(s.get('delta', 0)):.0f} units"
                has_trades = True
            elif action == 'BUY':
                action_str = f"📈 ADD {abs(s.get('delta', 0)):.0f} units"
                has_trades = True
            elif action == 'SELL':
                action_str = f"📉 REDUCE {abs(s.get('delta', 0)):.0f} units"
                has_trades = True
            elif action == 'CLOSE':
                action_str = f"🔴 CLOSE ALL"
                has_trades = True
            else:
                action_str = action
            
            lines.append(f"*{s.get('label', s.get('instrument', '?'))}* {regime}")
            lines.append(f"  F: {fc:.1f}/20  `{bar}`")
            lines.append(f"  Pos: {s.get('current_units', 0):.0f} → {s.get('target_units', 0):.0f}")
            lines.append(f"  {action_str}")
            lines.append(f"")
        
        if not has_trades:
            lines.append("_No trades today — all within buffer._")
        
        message = '\n'.join(lines)
        return self.send(message)
    
    def send_alert(self, instrument: str, action: str, forecast: float,
                   target: float, current: float):
        """Send a single urgent trade alert."""
        delta = target - current
        delta_str = f"+{delta:.0f}" if delta > 0 else f"{delta:.0f}"
        
        if action in ('ENTER', 'BUY'):
            emoji = '🟢'
        elif action == 'SELL':
            emoji = '📉'
        elif action == 'CLOSE':
            emoji = '🔴'
        else:
            emoji = '📊'
        
        message = (
            f"{emoji} *TPS v2 SIGNAL*\n"
            f"\n"
            f"*{instrument}*: {action}\n"
            f"Forecast: {forecast:.1f}/20\n"
            f"Position: {current:.0f} → {target:.0f} ({delta_str})\n"
            f"\n"
            f"_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
        )
        return self.send(message)
    
    def test(self):
        """Send a test message to verify the connection."""
        return self.send(
            "✅ *TPS v2 Telegram connected*\n"
            f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
            "Signal notifications are active."
        )


# ─── Standalone test ─────────────────────────────────────────────────
if __name__ == "__main__":
    notifier = TelegramNotifier()
    if notifier.enabled:
        success = notifier.test()
        print(f"Test message {'sent' if success else 'FAILED'}")
    else:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to test.")
        print("")
        print("Setup steps:")
        print("  1. Open Telegram, search @BotFather")
        print("  2. Send /newbot, follow prompts")
        print("  3. Copy the bot token")
        print("  4. Open chat with your bot, send /start")
        print("  5. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates")
        print("  6. Find your chat_id in the response")
        print("  7. export TELEGRAM_BOT_TOKEN='...'")
        print("  8. export TELEGRAM_CHAT_ID='...'")
        print("  9. python telegram_notify.py")
