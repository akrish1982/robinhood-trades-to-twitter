# Robinhood Trade Bot - Local Workflow

Automatically posts your Robinhood trades to X (Twitter) with beautiful trade cards.

## Features

- ✅ Authenticates with Robinhood using persistent sessions (pickle)
- ✅ Generates beautiful trade card images using Pillow
- ✅ Posts to X (Twitter) using Tweepy
- ✅ Tracks posted trades in SQLite database to avoid duplicates
- ✅ Comprehensive error logging to `bot.log`
- ✅ Designed for local execution with cron scheduling

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env` with your:
- **Robinhood credentials**: Username, password, and optional MFA code
- **Twitter API credentials**: Get these from [Twitter Developer Portal](https://developer.twitter.com/en/portal/dashboard)

### 3. Optional: Customize Assets

Place custom fonts and background images in the `assets/` directory:
- `assets/font.ttf` - Custom font for trade cards (optional)
- `assets/background.png` - Custom background image (optional, currently uses solid color)

## Usage

### Manual Execution

```bash
python main.py
```

### Automated Execution with Cron

#### Mac/Linux

1. Open your crontab:
```bash
crontab -e
```

2. Add a line to run the script every minute during market hours (9:30 AM - 4:00 PM ET, weekdays):
```bash
# Run every minute during market hours (9:30 AM - 4:00 PM ET, Mon-Fri)
* 9-16 * * 1-5 cd /Users/ananth/code/personal/trades_to_twitter && /usr/bin/python3 main.py
```

Or run every 5 minutes:
```bash
*/5 9-16 * * 1-5 cd /Users/ananth/code/personal/trades_to_twitter && /usr/bin/python3 main.py
```

**Note**: Adjust the path to your Python interpreter and project directory.

#### Ensure Your Computer Stays Awake

**macOS:**
1. System Settings → Energy Saver
2. Set "Prevent computer from sleeping automatically when the display is off" to ON
3. Or use `caffeinate` command:
```bash
caffeinate -d
```

**Linux:**
- Use `systemd-inhibit` or configure power management settings
- Disable sleep during market hours

## How It Works

1. **Authentication**: The script uses pickle to save your Robinhood session, so you only need to authenticate once (or when the session expires).

2. **Trade Detection**: Fetches recent filled orders from Robinhood.

3. **Duplicate Prevention**: Checks SQLite database (`last_trade.db`) to ensure each trade is only posted once.

4. **Image Generation**: Creates a beautiful trade card with:
   - Trade type (BUY/SELL) in color
   - Stock symbol
   - Quantity and price
   - Total value
   - Timestamp

5. **Twitter Posting**: Uploads the trade card image to X (Twitter) with a descriptive tweet.

6. **Error Handling**: All errors are logged to `bot.log` for debugging.

## Project Structure

```
/RobinhoodBot
├── main.py            # Core logic (Login -> Generate Image -> Post)
├── last_trade.db      # SQLite database tracking posted trades
├── robinhood_session.pkl  # Saved authentication session
├── bot.log            # Error and execution logs
├── assets/            # Folder for custom fonts/backgrounds
├── requirements.txt   # Python dependencies
├── .env               # Your API keys (NOT in git)
└── README.md          # This file
```

## Troubleshooting

### Session Expired
If you see authentication errors, delete `robinhood_session.pkl` and run again to re-authenticate.
1. --test-mode
Saves images and tweet text locally instead of posting to Twitter
Creates a test_output/ directory
Saves images as trade_card_*.png
Saves tweet text as tweet_*.txt
Still marks trades as posted in the database
2. --options-orders
Fetches options trades instead of equity trades
Uses get_all_option_orders() from robin_stocks
Extracts option symbols and details
Stores in a separate database table (posted_options_trades)
Formats tweet text for options contracts
3. --balance
Fetches account balance and portfolio information
Gets open equity and options positions
Saves data to account_balance.json
Prints a summary to the console
Usage Examples:
python main.py --test-mode
```
# Test mode - save locally without posting
python main.py --test-mode
# Get options trades instead of equity
python main.py --options-orders
# Get account balance and positions
python main.py --balance
# Combine options with test mode
python main.py --options-orders --test-mode
# Normal mode (default behavior)
python main.py
```
### No Trades Posted
- Check `bot.log` for errors
- Verify your Robinhood account has recent filled orders
- Ensure Twitter API credentials are correct

### Cron Not Running
- Check cron logs: `grep CRON /var/log/syslog` (Linux) or check Console.app (macOS)
- Verify Python path in crontab: `which python3`
- Ensure full paths are used in cron commands

## Security Notes

- **Never commit `.env` to git** - It contains sensitive credentials
- The `.env` file is already in `.gitignore`
- Session files (`.pkl`) should also not be committed

## License

This project is for personal use. Use at your own risk.

