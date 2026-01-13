#!/usr/bin/env python3
"""
Robinhood Trade Bot - Local Workflow
Authenticates with Robinhood, generates trade cards, and posts to X (Twitter)
"""

import os
import sys
import pickle
import sqlite3
import logging
import time
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import robin_stocks.robinhood as rh
from PIL import Image, ImageDraw, ImageFont
import tweepy
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
SESSION_FILE = "robinhood_session.pkl"
DB_FILE = "last_trade.db"
LOG_FILE = "bot.log"
ASSETS_DIR = Path("assets")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class TradeTracker:
    """Manages tracking of posted trades using SQLite"""
    
    def __init__(self, db_file: str = DB_FILE, is_options: bool = False):
        self.db_file = db_file
        self.is_options = is_options
        self._init_db()
    
    def _init_db(self):
        """Initialize the database table"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Table for equity trades
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS posted_trades (
                order_id TEXT PRIMARY KEY,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                trade_type TEXT DEFAULT 'equity'
            )
        ''')
        
        # Table for options trades
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS posted_options_trades (
                order_id TEXT PRIMARY KEY,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                trade_type TEXT DEFAULT 'options'
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized: {self.db_file}")
    
    def is_posted(self, order_id: str) -> bool:
        """Check if a trade has already been posted"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        table = 'posted_options_trades' if self.is_options else 'posted_trades'
        cursor.execute(f'SELECT 1 FROM {table} WHERE order_id = ?', (order_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def mark_posted(self, order_id: str):
        """Mark a trade as posted"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        table = 'posted_options_trades' if self.is_options else 'posted_trades'
        trade_type = 'options' if self.is_options else 'equity'
        cursor.execute(f'INSERT OR REPLACE INTO {table} (order_id, trade_type) VALUES (?, ?)', (order_id, trade_type))
        conn.commit()
        conn.close()
        logger.info(f"Marked trade {order_id} as posted ({trade_type})")


class RobinhoodAuth:
    """Handles Robinhood authentication with persistent sessions"""
    
    def __init__(self, session_file: str = SESSION_FILE):
        self.session_file = session_file
        self.username = os.getenv("ROBINHOOD_USERNAME")
        self.password = os.getenv("ROBINHOOD_PASSWORD")
        self.mfa_code = os.getenv("ROBINHOOD_MFA_CODE", "")
        
        if not self.username or not self.password:
            raise ValueError("ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD must be set in .env")
    
    def load_session(self) -> Optional[Dict[str, Any]]:
        """Load saved session from pickle file"""
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'rb') as f:
                    session = pickle.load(f)
                    logger.info("Loaded saved session from file")
                    return session
            except Exception as e:
                logger.warning(f"Failed to load session: {e}")
        return None
    
    def save_session(self, session: Dict[str, Any]):
        """Save session to pickle file"""
        try:
            with open(self.session_file, 'wb') as f:
                pickle.dump(session, f)
            logger.info("Session saved successfully")
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
    
    def login(self) -> bool:
        """Authenticate with Robinhood, using saved session if available"""
        # Check if session file exists (robin_stocks will auto-load it)
        pickle_name = os.path.splitext(self.session_file)[0]
        session_exists = os.path.exists(self.session_file)
        
        if session_exists:
            try:
                # robin_stocks will automatically load the session if pickle_name matches
                # Try to verify session is still valid
                account_info = rh.account.load_account_profile()
                if account_info:
                    logger.info("Successfully authenticated using saved session")
                    return True
            except Exception as e:
                logger.warning(f"Saved session invalid, re-authenticating: {e}")
                # Remove invalid session file
                try:
                    os.remove(self.session_file)
                except:
                    pass
        
        # Fresh login required
        try:
            login_response = rh.login(
                username=self.username,
                password=self.password,
                mfa_code=self.mfa_code if self.mfa_code else None,
                pickle_path=".",
                pickle_name=pickle_name
            )
            
            if login_response:
                logger.info("Successfully authenticated with fresh login")
                return True
            else:
                logger.error("Login failed - check credentials")
                return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False


class TradeCardGenerator:
    """Generates trade card images using Pillow"""
    
    def __init__(self, assets_dir: Path = ASSETS_DIR):
        self.assets_dir = assets_dir
        self.assets_dir.mkdir(exist_ok=True)
    
    def generate_card(self, trade_data: Dict[str, Any], output_path: str = "trade_card.png") -> str:
        """
        Generate a trade card image
        
        trade_data should contain:
        - symbol: Stock symbol
        - side: 'buy' or 'sell'
        - quantity: Number of shares
        - price: Execution price
        - order_id: Order ID
        - timestamp: Trade timestamp
        """
        # Image dimensions
        width, height = 1200, 675
        
        # Create image with background
        img = Image.new('RGB', (width, height), color='#1a1a1a')
        draw = ImageDraw.Draw(img)
        
        # Try to load custom font, fallback to default
        try:
            font_large = ImageFont.truetype(str(self.assets_dir / "font.ttf"), 72)
            font_medium = ImageFont.truetype(str(self.assets_dir / "font.ttf"), 48)
            font_small = ImageFont.truetype(str(self.assets_dir / "font.ttf"), 36)
        except:
            try:
                font_large = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 72)
                font_medium = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
                font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
            except:
                font_large = ImageFont.load_default()
                font_medium = ImageFont.load_default()
                font_small = ImageFont.load_default()
        
        # Determine trade type color
        trade_type = trade_data.get('side', 'buy').upper()
        if trade_type == 'BUY':
            color = '#00d4aa'  # Green for buy
            action_text = "BOUGHT"
        else:
            color = '#ff0054'  # Red for sell
            action_text = "SOLD"
        
        # Draw header
        draw.text((width // 2, 100), action_text, fill=color, font=font_large, anchor="mm")
        
        # Draw symbol
        symbol = trade_data.get('symbol', 'N/A')
        draw.text((width // 2, 200), symbol, fill='#ffffff', font=font_large, anchor="mm")
        
        # Draw quantity
        quantity = trade_data.get('quantity', 0)
        draw.text((width // 2, 300), f"{quantity} shares", fill='#ffffff', font=font_medium, anchor="mm")
        
        # Draw price
        price = trade_data.get('price', 0)
        price_str = f"${price:.2f}"
        draw.text((width // 2, 380), price_str, fill='#ffffff', font=font_medium, anchor="mm")
        
        # Draw total value
        total = quantity * price
        draw.text((width // 2, 450), f"Total: ${total:.2f}", fill='#888888', font=font_small, anchor="mm")
        
        # Draw timestamp
        timestamp = trade_data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        draw.text((width // 2, 550), timestamp, fill='#666666', font=font_small, anchor="mm")
        
        # Save image
        img.save(output_path)
        logger.info(f"Trade card generated: {output_path}")
        return output_path


class TwitterPoster:
    """Handles posting to X (Twitter) using Tweepy"""
    
    def __init__(self):
        self.api_key = os.getenv("TWITTER_API_KEY")
        self.api_secret = os.getenv("TWITTER_API_SECRET")
        self.access_token = os.getenv("TWITTER_ACCESS_TOKEN")
        self.access_token_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
        self.bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
        
        # Validate credentials are present
        missing = []
        if not self.api_key or self.api_key.strip() == "":
            missing.append("TWITTER_API_KEY")
        if not self.api_secret or self.api_secret.strip() == "":
            missing.append("TWITTER_API_SECRET")
        if not self.access_token or self.access_token.strip() == "":
            missing.append("TWITTER_ACCESS_TOKEN")
        if not self.access_token_secret or self.access_token_secret.strip() == "":
            missing.append("TWITTER_ACCESS_TOKEN_SECRET")
        
        if missing:
            raise ValueError(f"Missing Twitter API credentials in .env: {', '.join(missing)}")
        
        # Log credential status (without showing actual values)
        logger.info("Twitter credentials loaded:")
        logger.info(f"  API Key: {'✓' if self.api_key else '✗'} ({len(self.api_key) if self.api_key else 0} chars)")
        logger.info(f"  API Secret: {'✓' if self.api_secret else '✗'} ({len(self.api_secret) if self.api_secret else 0} chars)")
        logger.info(f"  Access Token: {'✓' if self.access_token else '✗'} ({len(self.access_token) if self.access_token else 0} chars)")
        logger.info(f"  Access Token Secret: {'✓' if self.access_token_secret else '✗'} ({len(self.access_token_secret) if self.access_token_secret else 0} chars)")
        logger.info(f"  Bearer Token: {'✓' if self.bearer_token else '✗'} ({len(self.bearer_token) if self.bearer_token else 0} chars)")
        
        # Initialize Tweepy client (v2 API)
        self.client = tweepy.Client(
            bearer_token=self.bearer_token,
            consumer_key=self.api_key,
            consumer_secret=self.api_secret,
            access_token=self.access_token,
            access_token_secret=self.access_token_secret,
            wait_on_rate_limit=True
        )
        
        # For media upload, we need API v1.1
        auth = tweepy.OAuth1UserHandler(
            self.api_key,
            self.api_secret,
            self.access_token,
            self.access_token_secret
        )
        self.api_v1 = tweepy.API(auth, wait_on_rate_limit=True)
    
    def post_trade(self, image_path: str, text: str = "") -> bool:
        """Post trade card to Twitter"""
        try:
            # First, verify credentials by testing API v1.1 authentication
            logger.info("Verifying Twitter API v1.1 authentication...")
            try:
                # Test v1.1 API access
                user = self.api_v1.verify_credentials()
                if user:
                    logger.info(f"✓ API v1.1 authentication successful (user: @{user.screen_name})")
                else:
                    logger.warning("API v1.1 verification returned no user data")
            except tweepy.Unauthorized as e:
                logger.error(f"✗ API v1.1 authentication failed (401 Unauthorized)")
                logger.error("  This usually means:")
                logger.error("  1. Access Token and Access Token Secret don't match the API Key/Secret")
                logger.error("  2. Credentials are incorrect or have been regenerated")
                logger.error("  3. App permissions are insufficient (need 'Read and Write')")
                logger.error(f"  Error details: {e}")
                return False
            except Exception as e:
                logger.warning(f"API v1.1 verification warning: {e}")
            
            # Upload media
            logger.info(f"Uploading media: {image_path}")
            try:
                media = self.api_v1.media_upload(image_path)
                logger.info(f"✓ Media uploaded successfully (ID: {media.media_id})")
            except tweepy.Unauthorized as e:
                logger.error(f"✗ Media upload failed (401 Unauthorized)")
                logger.error("  Check that your Access Token and Access Token Secret are correct")
                logger.error(f"  Error details: {e}")
                return False
            except Exception as e:
                logger.error(f"Media upload error: {e}")
                return False
            
            # Post tweet with media
            logger.info(f"Posting tweet with text: {text[:50]}..." if len(text) > 50 else f"Posting tweet with text: {text}")
            try:
                if text:
                    response = self.client.create_tweet(text=text, media_ids=[media.media_id])
                else:
                    response = self.client.create_tweet(media_ids=[media.media_id])
                
                if response:
                    tweet_id = response.data.get('id') if hasattr(response, 'data') else None
                    logger.info(f"✓ Successfully posted to Twitter (Tweet ID: {tweet_id})")
                    return True
                else:
                    logger.error("Failed to post to Twitter - no response data")
                    return False
            except tweepy.Unauthorized as e:
                logger.error(f"✗ Tweet creation failed (401 Unauthorized)")
                logger.error("  Check that your API credentials are correct and have write permissions")
                logger.error(f"  Error details: {e}")
                return False
            except Exception as e:
                logger.error(f"Tweet creation error: {e}")
                if hasattr(e, 'response'):
                    logger.error(f"  Response status: {e.response.status_code if hasattr(e.response, 'status_code') else 'N/A'}")
                    logger.error(f"  Response text: {e.response.text if hasattr(e.response, 'text') else 'N/A'}")
                return False
        except Exception as e:
            logger.error(f"Twitter posting error: {e}")
            logger.error(f"  Error type: {type(e).__name__}")
            return False


def get_recent_trades(limit: int = 10) -> list:
    """Get recent trades from Robinhood"""
    try:
        # get_all_stock_orders doesn't support limit parameter, so we get all and limit in Python
        orders = rh.get_all_stock_orders()
        if not orders:
            logger.warning("No orders found")
            return []
        
        logger.info(f"Fetched {len(orders)} total orders from Robinhood")
        
        # Filter for filled orders only FIRST (before extracting symbols)
        filled_orders = [order for order in orders if order.get('state') == 'filled']
        logger.info(f"Found {len(filled_orders)} filled orders")
        
        # Sort by most recent first (by created_at or last_transaction_at)
        filled_orders.sort(
            key=lambda x: x.get('last_transaction_at', x.get('created_at', '')),
            reverse=True
        )
        
        # Limit to only the orders we'll actually process (plus a small buffer for safety)
        # We'll extract symbols only for these orders to save time
        max_orders_to_process = limit * 2  # Get 2x the limit to account for already-posted trades
        orders_to_extract = filled_orders[:max_orders_to_process]
        
        # Extract symbols from instrument URLs ONLY for orders we'll process
        logger.info(f"Extracting symbols from instrument URLs for {len(orders_to_extract)} recent filled orders...")
        extracted_count = 0
        failed_count = 0
        
        for i, order in enumerate(orders_to_extract):
            instrument_url = order.get('instrument', '')
            if instrument_url:
                try:
                    # Use info='symbol' to get just the symbol string directly
                    symbol = rh.get_instrument_by_url(instrument_url, info='symbol')
                    if symbol:
                        order['symbol'] = symbol
                        extracted_count += 1
                    else:
                        failed_count += 1
                        logger.debug(f"No symbol returned for instrument URL {instrument_url}")
                except Exception as e:
                    failed_count += 1
                    # Try getting full instrument data as fallback
                    try:
                        instrument_data = rh.get_instrument_by_url(instrument_url)
                        if instrument_data and 'symbol' in instrument_data:
                            order['symbol'] = instrument_data['symbol']
                            extracted_count += 1
                            failed_count -= 1  # Adjust count since we succeeded
                        else:
                            logger.debug(f"Instrument data missing symbol for URL {instrument_url}: {e}")
                    except Exception as e2:
                        logger.debug(f"Could not get symbol for instrument URL {instrument_url}: {e2}")
            else:
                failed_count += 1
                logger.debug(f"Order {order.get('id', 'unknown')} has no instrument URL")
        
        logger.info(f"Successfully extracted symbols for {extracted_count} orders, {failed_count} failed")
        
        # Check if any orders still missing symbols and log full data
        orders_without_symbols = [order for order in orders_to_extract if not order.get('symbol')]
        if orders_without_symbols:
            logger.error("=" * 80)
            logger.error("SYMBOL EXTRACTION FAILED - Full order data below:")
            logger.error("=" * 80)
            for order in orders_without_symbols:
                logger.error(f"\nOrder ID: {order.get('id', 'N/A')}")
                logger.error("Full order data:")
                # Print in a readable format
                for key, value in order.items():
                    # Truncate very long values
                    if isinstance(value, str) and len(value) > 200:
                        value = value[:200] + "... (truncated)"
                    elif isinstance(value, (list, dict)) and value:
                        value = str(value)[:200] + "... (truncated)" if len(str(value)) > 200 else value
                    logger.error(f"  {key}: {value}")
                logger.error("-" * 80)
            logger.error("=" * 80)
            raise ValueError(
                f"Could not extract symbol for {len(orders_without_symbols)} order(s). "
                "Please check the logged order data above to identify which field contains the symbol."
            )
        
        # Return only the limited number of orders (symbols already extracted)
        return orders_to_extract[:limit]
    except Exception as e:
        logger.error(f"Error fetching trades: {e}")
        return []


def format_trade_data(order: Dict[str, Any]) -> Dict[str, Any]:
    """Format order data for trade card generation"""
    # Symbol should already be extracted in get_recent_trades, but verify it exists
    symbol = order.get('symbol', '')
    
    if not symbol:
        # This should not happen if get_recent_trades worked correctly
        # But provide helpful error message
        order_id = order.get('id', 'unknown')
        logger.error(f"Symbol missing for order {order_id} in format_trade_data")
        logger.error("This indicates a bug - symbol should have been extracted earlier")
        raise ValueError(f"Symbol not found for order {order_id}. Order should have been filtered out earlier.")
    
    side = order.get('side', 'buy')
    quantity = float(order.get('quantity', 0))
    price = float(order.get('average_price', 0))
    order_id = order.get('id', '')
    timestamp = order.get('last_transaction_at', order.get('created_at', ''))
    
    # Parse timestamp
    try:
        if timestamp:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    return {
        'symbol': symbol,
        'side': side,
        'quantity': quantity,
        'price': price,
        'order_id': order_id,
        'timestamp': timestamp
    }


def main():
    """Main execution function"""
    try:
        logger.info("=" * 50)
        logger.info("Starting Robinhood Trade Bot")
        logger.info("=" * 50)
        
        # Initialize components
        tracker = TradeTracker()
        auth = RobinhoodAuth()
        card_generator = TradeCardGenerator()
        twitter_poster = TwitterPoster()
        
        # Authenticate with Robinhood
        if not auth.login():
            logger.error("Failed to authenticate with Robinhood")
            return False
        
        # Get recent trades
        logger.info("Fetching recent trades...")
        trades = get_recent_trades(limit=10)
        
        if not trades:
            logger.info("No trades found to process")
            return True
        
        # Process trades (newest first)
        trades.reverse()
        posted_count = 0
        failed_count = 0
        skipped_count = 0
        
        # Limit number of trades processed per run to avoid rate limits
        # Twitter allows ~300 tweets per 3 hours, so we'll process max 5 per run
        MAX_TRADES_PER_RUN = int(os.getenv("MAX_TRADES_PER_RUN", "5"))
        trades_to_process = trades[:MAX_TRADES_PER_RUN]
        
        if len(trades) > MAX_TRADES_PER_RUN:
            logger.info(f"Limiting to {MAX_TRADES_PER_RUN} trades per run (found {len(trades)} total). Remaining will be processed on next run.")
        
        for order in trades_to_process:
            order_id = order.get('id', '')
            
            # Skip if already posted
            if tracker.is_posted(order_id):
                logger.info(f"Trade {order_id} already posted, skipping")
                skipped_count += 1
                continue
            
            # Format trade data
            trade_data = format_trade_data(order)
            
            # Generate trade card
            image_path = f"trade_card_{order_id[:8]}.png"
            card_generator.generate_card(trade_data, image_path)
            
            # Create tweet text
            action = "Bought" if trade_data['side'] == 'buy' else "Sold"
            tweet_text = f"{action} {trade_data['quantity']} shares of {trade_data['symbol']} at ${trade_data['price']:.2f}"
            
            # Post to Twitter
            try:
                if twitter_poster.post_trade(image_path, tweet_text):
                    tracker.mark_posted(order_id)
                    posted_count += 1
                    logger.info(f"Successfully posted trade: {order_id}")
                    
                    # Add a small delay between posts to avoid rate limits
                    # Twitter allows ~300 tweets per 3 hours = ~1 per 36 seconds
                    # We'll wait 2 seconds between posts to be safe
                    if posted_count < len(trades_to_process):
                        time.sleep(2)
                else:
                    failed_count += 1
                    logger.error(f"Failed to post trade: {order_id}")
            except tweepy.TooManyRequests as e:
                # Rate limit exceeded
                reset_time = e.response.headers.get('x-rate-limit-reset', 'unknown')
                logger.error(f"Rate limit exceeded. Waiting for rate limit reset...")
                logger.error(f"Reset time: {reset_time}")
                logger.error(f"Stopping processing. {posted_count} trades posted, {len(trades_to_process) - posted_count - skipped_count} remaining.")
                break
            except Exception as e:
                failed_count += 1
                logger.error(f"Error posting trade {order_id}: {e}")
            
            # Clean up image file
            try:
                os.remove(image_path)
            except:
                pass
        
        logger.info(f"Processing complete:")
        logger.info(f"  Posted: {posted_count}")
        logger.info(f"  Failed: {failed_count}")
        logger.info(f"  Skipped (already posted): {skipped_count}")
        logger.info(f"  Remaining: {len(trades) - posted_count - skipped_count - failed_count}")
        logger.info("=" * 50)
        return True
        
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

