# ILSS Signal Generator

Intraday signal detection for the ILSS (Intraday Liquidity Sweep System).
Detects SFP patterns at key levels during validated session windows.

## Validated Parameters (Phase 1–7 + Enhancements)

| Instrument | Sessions | Hold | Bias Filter |
|------------|----------|------|-------------|
| NAS100_USD | london_open | 4h (16 bars) | EWMAC ≥5 |
| GBP_USD | london_open, ny_close | 2h (8 bars) | EWMAC ≥5 |
| USD_JPY | asian | 4h (16 bars) | EWMAC ≥5 |
| XAU_USD | ny_afternoon | 2h (8 bars) | EWMAC ≥5 |
| BTC_USD | ny_close | 6h (24 bars) | None |

SFP sweep depth: 0.4–0.8× ATR (E2, walk-forward confirmed, WFE 1.46)

## Setup

1. Set environment variables:
```bash
export OANDA_TOKEN="your-api-token"
export OANDA_ACCOUNT="001-001-XXXXXXXX-001"
export OANDA_ENV="practice"        # or "live"
export TELEGRAM_BOT_TOKEN="..."    # optional
export TELEGRAM_CHAT_ID="..."      # optional
```

2. Install dependencies (same as TPS v2):
```bash
pip install requests pandas numpy pyyaml
```

3. Test run:
```bash
cd ilss/signal_gen
python generate_signals.py
```

## Cron Setup (run every 15 minutes)

```bash
crontab -e
```

Add:
```
*/15 * * * * cd /Users/adam/trend-pullback-system/ilss/signal_gen && \
    /usr/bin/env python3 generate_signals.py >> /tmp/ilss_signals.log 2>&1
```

Or for a status summary at 21:30 UTC (end of trading day):
```
30 21 * * * cd /Users/adam/trend-pullback-system/ilss/signal_gen && \
    /usr/bin/env python3 generate_signals.py --status >> /tmp/ilss_signals.log 2>&1
```

## Modes

```bash
# Paper mode (default) — log signals, send Telegram alerts, no orders
python generate_signals.py

# Live mode — execute orders on OANDA with stop-loss attached
python generate_signals.py --live

# Daily status summary to Telegram
python generate_signals.py --status

# Override account NAV for sizing
python generate_signals.py --capital 50000
```

## Output Files

- `signal_log.csv` — all detected signals with entry/stop/size
- `state.json` — daily trade count, risk halt status, dedup tracking

## Paper Trading Protocol

Run paper mode for 4–6 weeks minimum before going live:
1. Watch the signal_log.csv for quality
2. Check Telegram alerts arrive cleanly
3. Verify entry/stop levels make sense in context
4. Review win rate and R per trade manually
5. When satisfied: `python generate_signals.py --live`
