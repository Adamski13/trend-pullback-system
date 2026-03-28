#!/usr/bin/env python3
"""
TPS v2 Setup Helper

Walks you through:
  1. Testing OANDA connection
  2. Setting up Telegram bot
  3. Installing the daily cron job

Usage:
    python setup.py
"""

import os
import sys
import subprocess
from pathlib import Path


def check_env_var(name, description):
    val = os.environ.get(name, '')
    if val:
        masked = val[:6] + '...' + val[-4:]
        print(f"  ✅ {name}: {masked}")
        return True
    else:
        print(f"  ❌ {name}: not set")
        print(f"     → {description}")
        return False


def test_oanda():
    print("\n1. OANDA CONNECTION")
    print("─" * 40)
    
    ok = True
    ok &= check_env_var('OANDA_TOKEN', 'export OANDA_TOKEN="your-token"')
    ok &= check_env_var('OANDA_ACCOUNT', 'export OANDA_ACCOUNT="XXX-XXX-XXXXXXX-XXX"')
    ok &= check_env_var('OANDA_ENV', 'export OANDA_ENV="practice" or "live"')
    
    if ok:
        print("\n  Testing connection...")
        try:
            from oanda_client import OandaClient
            client = OandaClient(
                os.environ['OANDA_TOKEN'],
                os.environ['OANDA_ACCOUNT'],
                os.environ.get('OANDA_ENV', 'practice')
            )
            summary = client.get_account()
            nav = float(summary.get('NAV', 0))
            print(f"  ✅ Connected! NAV: ${nav:,.2f}")
            return True
        except Exception as e:
            print(f"  ❌ Connection failed: {e}")
            return False
    return False


def test_telegram():
    print("\n2. TELEGRAM NOTIFICATIONS")
    print("─" * 40)
    
    ok = True
    ok &= check_env_var('TELEGRAM_BOT_TOKEN', 'Create bot via @BotFather on Telegram')
    ok &= check_env_var('TELEGRAM_CHAT_ID', 'Get from https://api.telegram.org/bot<TOKEN>/getUpdates')
    
    if ok:
        print("\n  Sending test message...")
        try:
            from telegram_notify import TelegramNotifier
            notifier = TelegramNotifier()
            success = notifier.test()
            if success:
                print("  ✅ Message sent! Check your Telegram.")
            else:
                print("  ❌ Failed to send. Check token and chat_id.")
            return success
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False
    else:
        print("\n  Setup steps:")
        print("  1. Open Telegram → search @BotFather")
        print("  2. Send /newbot → pick a name → pick a username")
        print("  3. Copy the bot token BotFather gives you")
        print("  4. Open a chat with your new bot → send /start")
        print("  5. In browser, visit:")
        print("     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates")
        print("  6. Find 'chat':{'id': XXXXXXX} in the response")
        print("  7. Set the env vars:")
        print('     export TELEGRAM_BOT_TOKEN="your-bot-token"')
        print('     export TELEGRAM_CHAT_ID="your-chat-id"')
        return False


def setup_cron():
    print("\n3. DAILY CRON JOB")
    print("─" * 40)
    
    script_dir = Path(__file__).parent.resolve()
    python_path = sys.executable
    
    # Build the cron command
    # Runs at 22:05 UTC (after NY close at 21:00 UTC / 5pm ET)
    env_vars = ""
    for var in ['OANDA_TOKEN', 'OANDA_ACCOUNT', 'OANDA_ENV', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']:
        val = os.environ.get(var, '')
        if val:
            env_vars += f'{var}="{val}" '
    
    cron_cmd = f'5 22 * * 1-5 cd {script_dir} && {env_vars}{python_path} generate_signals.py >> {script_dir}/cron.log 2>&1'
    
    print(f"  Proposed cron entry (runs Mon-Fri at 22:05 UTC):")
    print(f"  {cron_cmd}")
    print()
    
    response = input("  Install this cron job? (y/n): ").strip().lower()
    
    if response == 'y':
        try:
            # Get current crontab
            result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
            current = result.stdout if result.returncode == 0 else ''
            
            # Check if already installed
            if 'generate_signals.py' in current:
                print("  ⚠  Cron job already exists. Skipping.")
                return True
            
            # Add new entry
            new_crontab = current.rstrip() + '\n' + cron_cmd + '\n'
            process = subprocess.run(
                ['crontab', '-'],
                input=new_crontab,
                capture_output=True,
                text=True
            )
            
            if process.returncode == 0:
                print("  ✅ Cron job installed!")
                print(f"  Signals will run Mon-Fri at 22:05 UTC")
                print(f"  Logs: {script_dir}/cron.log")
                return True
            else:
                print(f"  ❌ Failed: {process.stderr}")
                return False
        except Exception as e:
            print(f"  ❌ Error: {e}")
            print("  You can add it manually with: crontab -e")
            return False
    else:
        print("  Skipped. To add manually, run: crontab -e")
        print(f"  And paste: {cron_cmd}")
        return False


def main():
    print("=" * 50)
    print("  TPS v2 — Setup Helper")
    print("=" * 50)
    
    oanda_ok = test_oanda()
    telegram_ok = test_telegram()
    
    if oanda_ok:
        setup_cron()
    else:
        print("\n  ⚠  Fix OANDA connection before setting up cron.")
    
    print("\n" + "=" * 50)
    print("  SUMMARY")
    print("=" * 50)
    print(f"  OANDA:     {'✅ Connected' if oanda_ok else '❌ Not configured'}")
    print(f"  Telegram:  {'✅ Working' if telegram_ok else '❌ Not configured'}")
    print(f"\n  Once both are ✅, the system will:")
    print(f"  • Run automatically Mon-Fri at 22:05 UTC")
    print(f"  • Compute forecasts for NAS100, XAUUSD, BTCUSD")
    print(f"  • Send signals to your phone via Telegram")
    print(f"  • Log everything to state/ and cron.log")
    print("=" * 50)


if __name__ == "__main__":
    main()
