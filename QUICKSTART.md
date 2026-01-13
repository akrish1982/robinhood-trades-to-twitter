# Quick Start Guide

## 1. Install Dependencies

```bash
pip install -r requirements.txt
```

## 2. Set Up Environment

Run the setup script:
```bash
./setup_env.sh
```

Or manually create `.env` file with your credentials:
- Robinhood username and password
- Twitter API credentials (get from https://developer.twitter.com/en/portal/dashboard)

## 3. Test Run

```bash
python main.py
```

This will:
- Authenticate with Robinhood (may prompt for 2FA on first run)
- Check for recent trades
- Generate and post trade cards to Twitter

## 4. Set Up Cron (Automated Execution)

1. Find your Python path:
```bash
which python3
```

2. Edit crontab:
```bash
crontab -e
```

3. Add this line (adjust paths):
```bash
*/5 9-16 * * 1-5 cd /Users/ananth/code/personal/trades_to_twitter && /usr/bin/python3 main.py >> bot.log 2>&1
```

This runs every 5 minutes during market hours (9 AM - 4 PM, Mon-Fri).

## 5. Keep Your Computer Awake

**macOS:**
```bash
# Prevent sleep (run in terminal)
caffeinate -d
```

Or: System Settings → Energy Saver → Prevent automatic sleeping

## Troubleshooting

- **Check logs**: `tail -f bot.log`
- **Session expired**: Delete `robinhood_session.pkl` and re-run
- **No trades**: Verify you have filled orders in Robinhood
- **Twitter errors**: Check API credentials in `.env`

