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
    
    def __init__(self, db_file: str = DB_FILE):
        self.db_file = db_file
        self._init_db()
    
    def _init_db(self):
        """Initialize the database table"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS posted_trades (
                order_id TEXT PRIMARY KEY,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logger.info(f"Database initialized: {self.db_file}")
    
    def is_posted(self, order_id: str) -> bool:
        """Check if a trade has already been posted"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM posted_trades WHERE order_id = ?', (order_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def mark_posted(self, order_id: str):
        """Mark a trade as posted"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO posted_trades (order_id) VALUES (?)', (order_id,))
        conn.commit()
        conn.close()
        logger.info(f"Marked trade {order_id} as posted")


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
        # Try to load existing session
        session = self.load_session()
        if session:
            try:
                # Attempt to use saved session
                rh.set_login_state(session)
                # Verify session is still valid
                account_info = rh.account.load_account_profile()
                if account_info:
                    logger.info("Successfully authenticated using saved session")
                    return True
            except Exception as e:
                logger.warning(f"Saved session invalid, re-authenticating: {e}")
        
        # Fresh login required
        try:
            login_response = rh.login(
                username=self.username,
                password=self.password,
                mfa_code=self.mfa_code if self.mfa_code else None
            )
            
            if login_response:
                # Save the session for next time
                current_session = rh.get_login_state()
                if current_session:
                    self.save_session(current_session)
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
        
        if not all([self.api_key, self.api_secret, self.access_token, self.access_token_secret]):
            raise ValueError("Twitter API credentials must be set in .env")
        
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
        self.api_v1 = tweepy.API(auth)
    
    def post_trade(self, image_path: str, text: str = "") -> bool:
        """Post trade card to Twitter"""
        try:
            # Upload media
            media = self.api_v1.media_upload(image_path)
            
            # Post tweet with media
            if text:
                response = self.client.create_tweet(text=text, media_ids=[media.media_id])
            else:
                response = self.client.create_tweet(media_ids=[media.media_id])
            
            if response:
                logger.info(f"Successfully posted to Twitter: {response.data.get('id')}")
                return True
            else:
                logger.error("Failed to post to Twitter")
                return False
        except Exception as e:
            logger.error(f"Twitter posting error: {e}")
            return False


def get_recent_trades(limit: int = 10) -> list:
    """Get recent trades from Robinhood"""
    try:
        orders = rh.get_all_stock_orders(limit=limit)
        if not orders:
            logger.warning("No orders found")
            return []
        
        # Filter for filled orders only
        filled_orders = [order for order in orders if order.get('state') == 'filled']
        return filled_orders
    except Exception as e:
        logger.error(f"Error fetching trades: {e}")
        return []


def format_trade_data(order: Dict[str, Any]) -> Dict[str, Any]:
    """Format order data for trade card generation"""
    symbol = order.get('symbol', 'N/A')
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
        
        for order in trades:
            order_id = order.get('id', '')
            
            # Skip if already posted
            if tracker.is_posted(order_id):
                logger.info(f"Trade {order_id} already posted, skipping")
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
            if twitter_poster.post_trade(image_path, tweet_text):
                tracker.mark_posted(order_id)
                posted_count += 1
                logger.info(f"Successfully posted trade: {order_id}")
            else:
                logger.error(f"Failed to post trade: {order_id}")
            
            # Clean up image file
            try:
                os.remove(image_path)
            except:
                pass
        
        logger.info(f"Processed {posted_count} new trade(s)")
        logger.info("=" * 50)
        return True
        
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

