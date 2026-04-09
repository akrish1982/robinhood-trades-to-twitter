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
        
        # Table for options positions analysis
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS options_positions (
                position_id TEXT PRIMARY KEY,
                open_date TEXT,
                symbol TEXT,
                trade_type TEXT,
                status TEXT,
                num_contracts REAL,
                strike REAL,
                expiration TEXT,
                delta REAL,
                put_premium REAL,
                call_premium REAL,
                option_id TEXT,
                position_type TEXT,
                average_price REAL,
                quantity REAL,
                chain_symbol TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


def get_recent_options_trades(limit: int = 10) -> list:
    """Get recent options trades from Robinhood"""
    try:
        orders = rh.get_all_option_orders()
        if not orders:
            logger.warning("No options orders found")
            return []
        
        logger.info(f"Fetched {len(orders)} total options orders from Robinhood")
        
        # Filter for filled orders only
        filled_orders = [order for order in orders if order.get('state') == 'filled']
        logger.info(f"Found {len(filled_orders)} filled options orders")
        
        # Sort by most recent first
        filled_orders.sort(
            key=lambda x: x.get('last_transaction_at', x.get('created_at', '')),
            reverse=True
        )
        
        # Limit to orders we'll process
        max_orders_to_process = limit * 2
        orders_to_process = filled_orders[:max_orders_to_process]
        
        logger.info(f"Processing {len(orders_to_process)} recent options orders")
        
        return orders_to_process[:limit]
    except Exception as e:
        logger.error(f"Error fetching options trades: {e}")
        return []


def format_options_trade_data(order: Dict[str, Any]) -> Dict[str, Any]:
    """Format options order data for trade card generation"""
    # Options orders have different structure
    order_id = order.get('id', '')
    side = order.get('side', 'buy')
    quantity = float(order.get('quantity', 0))
    price = float(order.get('average_price', 0))
    timestamp = order.get('last_transaction_at', order.get('created_at', ''))
    
    # Get option details - options orders have 'legs' with option details
    legs = order.get('legs', [])
    option_symbol = 'N/A'
    if legs:
        # Get the first leg's option details
        leg = legs[0]
        option_id = leg.get('option', '')
        if option_id:
            try:
                # Extract option ID from URL if it's a URL
                if isinstance(option_id, str) and 'options' in option_id:
                    option_id = option_id.rstrip('/').split('/')[-1]
                
                # Try to get option instrument data
                try:
                    # Extract option ID from URL if needed
                    if isinstance(option_id, str) and 'options' in option_id:
                        option_id_clean = option_id.rstrip('/').split('/')[-1]
                    else:
                        option_id_clean = option_id
                    
                    # Get option market data
                    option_data = rh.get_option_market_data_by_id(option_id_clean)
                    if option_data:
                        # Try to get underlying symbol
                        instrument_url = option_data.get('instrument', '')
                        if instrument_url:
                            instrument_data = rh.get_instrument_by_url(instrument_url)
                            if instrument_data:
                                underlying_symbol = instrument_data.get('symbol', 'N/A')
                                # Get strike and expiration from option data
                                strike = option_data.get('strike_price', '')
                                expiration = option_data.get('expiration_date', '')
                                option_symbol = f"{underlying_symbol} {strike} {expiration}" if strike else underlying_symbol
                    else:
                        option_symbol = f"OPTION-{option_id_clean[:8]}"
                except Exception as e:
                    logger.debug(f"Could not get option details: {e}")
                    option_symbol = f"OPTION-{order_id[:8]}"
            except Exception as e:
                logger.debug(f"Could not get option symbol: {e}")
    
    # Parse timestamp
    try:
        if timestamp:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    return {
        'symbol': option_symbol,
        'side': side,
        'quantity': quantity,
        'price': price,
        'order_id': order_id,
        'timestamp': timestamp,
        'type': 'options'
    }


def get_option_instrument_details_from_db(option_id: str, db_file: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """Check if we already have option details in the database to avoid API calls"""
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT strike, expiration, option_id
            FROM options_positions
            WHERE option_id = ?
            LIMIT 1
        ''', (option_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0] and row[0] > 0:  # If strike exists and is valid
            # We have the data, but we need to determine option_type
            # For now, return None to fetch from API (we can enhance this later)
            return None
        return None
    except:
        return None


def get_option_instrument_details(option_id: str, use_cache: bool = True) -> Dict[str, Any]:
    """Get detailed option instrument information including strike, delta, etc."""
    try:
        # Extract option ID from URL if needed
        if isinstance(option_id, str) and 'options' in option_id:
            option_id_clean = option_id.rstrip('/').split('/')[-1]
        else:
            option_id_clean = option_id
        
        # Check database cache first
        if use_cache:
            cached_data = get_option_instrument_details_from_db(option_id_clean)
            # For now, we'll still fetch to get option_type and delta
            # Can be optimized later to store option_type in DB
        
        # Get option instrument data using the correct robin_stocks method
        try:
            # Use r.options.get_option_instrument_data_by_id as specified
            option_instrument = rh.options.get_option_instrument_data_by_id(option_id_clean)
            
            # Handle case where API returns a list instead of dict
            if isinstance(option_instrument, list):
                if len(option_instrument) > 0:
                    option_instrument = option_instrument[0]  # Take first element
                else:
                    option_instrument = {}
        except AttributeError:
            # Fallback to direct method if options module doesn't exist
            try:
                option_instrument = rh.get_option_instrument_data_by_id(option_id_clean)
                # Handle list response
                if isinstance(option_instrument, list):
                    if len(option_instrument) > 0:
                        option_instrument = option_instrument[0]
                    else:
                        option_instrument = {}
            except:
                try:
                    option_instrument = rh.get_option_instrument_data(option_id_clean)
                    # Handle list response
                    if isinstance(option_instrument, list):
                        if len(option_instrument) > 0:
                            option_instrument = option_instrument[0]
                        else:
                            option_instrument = {}
                except:
                    option_instrument = {}
        except Exception as e:
            logger.debug(f"Error fetching option instrument: {e}")
            option_instrument = {}
        
        if not option_instrument or not isinstance(option_instrument, dict):
            return {}
        
        # Get option market data for delta and current prices
        try:
            option_market_data = rh.get_option_market_data_by_id(option_id_clean)
            # Handle case where API returns a list instead of dict
            if isinstance(option_market_data, list):
                if len(option_market_data) > 0:
                    option_market_data = option_market_data[0]  # Take first element
                else:
                    option_market_data = {}
        except:
            option_market_data = {}
        
        # Ensure option_market_data is a dict before using .get()
        if not isinstance(option_market_data, dict):
            option_market_data = {}
        
        # Combine data - Robinhood returns strike_price as string, convert to float
        strike_price_str = option_instrument.get('strike_price', '0')
        try:
            strike_price = float(strike_price_str)
        except (ValueError, TypeError):
            strike_price = 0
        
        # Safely extract market data values
        delta = 0
        bid = 0
        ask = 0
        mark = 0
        
        if isinstance(option_market_data, dict):
            try:
                delta = float(option_market_data.get('delta', 0))
            except (ValueError, TypeError):
                delta = 0
            try:
                bid = float(option_market_data.get('bid_price', 0))
            except (ValueError, TypeError):
                bid = 0
            try:
                ask = float(option_market_data.get('ask_price', 0))
            except (ValueError, TypeError):
                ask = 0
            try:
                mark = float(option_market_data.get('mark_price', 0))
            except (ValueError, TypeError):
                mark = 0
        
        result = {
            'strike_price': strike_price,
            'expiration_date': option_instrument.get('expiration_date', ''),
            'option_type': option_instrument.get('type', ''),  # 'call' or 'put'
            'delta': delta,
            'bid': bid,
            'ask': ask,
            'mark': mark,
        }
        
        return result
    except Exception as e:
        logger.error(f"Failed to fetch details for {option_id}: {e}")
        import traceback
        logger.debug(f"Traceback: {traceback.format_exc()}")
        return {}


def determine_trade_type(position: Dict[str, Any], all_positions: list, equity_positions: list) -> str:
    """Determine the trade type based on position and related positions"""
    chain_symbol = position.get('chain_symbol', '')
    position_type = position.get('type', '')  # 'long' or 'short'
    option_id = position.get('option_id', '')
    expiration_date = position.get('expiration_date', '')
    
    # Get option details to determine if it's a call or put and strike
    option_details = get_option_instrument_details(option_id)
    option_type = option_details.get('option_type', '')
    strike = option_details.get('strike_price', 0)
    
    # Find related positions (same symbol, same expiration)
    related_positions = [
        p for p in all_positions 
        if p.get('chain_symbol') == chain_symbol 
        and p.get('id') != position.get('id')
        and p.get('expiration_date') == expiration_date
    ]
    
    # Check for vertical spreads (same expiration, different strikes, opposite types)
    if related_positions:
        for related_pos in related_positions:
            related_option_details = get_option_instrument_details(related_pos.get('option_id', ''))
            related_strike = related_option_details.get('strike_price', 0)
            related_option_type = related_option_details.get('option_type', '')
            related_position_type = related_pos.get('type', '')
            
            # Check if it's a vertical spread (same option type, different strikes, opposite positions)
            if (related_option_type == option_type and 
                related_strike != strike and 
                related_strike > 0 and 
                strike > 0 and
                related_position_type != position_type):
                
                if option_type == 'put':
                    if position_type == 'short':
                        return 'Vertical Put Credit Spread'
                    else:
                        return 'Vertical Put Debit Spread'
                else:  # call
                    if position_type == 'short':
                        return 'Vertical Call Credit Spread'
                    else:
                        return 'Vertical Call Debit Spread'
    
    # Check for covered call (short call + long stock)
    if option_type == 'call' and position_type == 'short':
        # Check if there's a long stock position
        stock_positions = [
            p for p in equity_positions 
            if p.get('symbol', '').upper() == chain_symbol.upper()
            and float(p.get('quantity', 0)) > 0
        ]
        if stock_positions:
            return 'Covered Call'
    
    # Check for cash-secured put (short put, no corresponding long put with same expiration)
    if option_type == 'put' and position_type == 'short':
        # Check if there's a corresponding long put with same expiration
        long_puts = [
            p for p in all_positions 
            if p.get('chain_symbol') == chain_symbol 
            and p.get('type') == 'long'
            and p.get('expiration_date') == expiration_date
            and p.get('option_id') != option_id
        ]
        if not long_puts:
            return 'Cash Secured Put'
    
    # Default based on position type
    if position_type == 'long':
        return f'Long {option_type.capitalize()}' if option_type else 'Long Option'
    else:
        return f'Short {option_type.capitalize()}' if option_type else 'Short Option'


def export_options_positions_to_csv(output_file: str = "options_positions.csv") -> bool:
    """Export options positions from database to CSV file"""
    try:
        import csv
        # Ensure table exists
        init_options_positions_table()
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT open_date, symbol, trade_type, status, num_contracts, strike,
                   expiration, delta, put_premium, call_premium
            FROM options_positions
            ORDER BY open_date DESC, symbol
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            logger.warning("No options positions found in database")
            return False
        
        with open(output_file, 'w', newline='') as f:
            writer = csv.writer(f)
            # Write header
            writer.writerow([
                'Open Date', 'Symbol', 'Trade Type', 'Status', '# of Contracts',
                'Strike', 'Expiration', 'Delta', 'Put Premium', 'Call Premium'
            ])
            # Write data
            for row in rows:
                writer.writerow(row)
        
        logger.info(f"Exported {len(rows)} options positions to {output_file}")
        return True
        
    except Exception as e:
        logger.error(f"Error exporting options positions: {e}")
        return False


def init_options_positions_table(db_file: str = DB_FILE):
    """Initialize the options_positions table if it doesn't exist"""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS options_positions (
            position_id TEXT PRIMARY KEY,
            open_date TEXT,
            symbol TEXT,
            trade_type TEXT,
            status TEXT,
            num_contracts REAL,
            strike REAL,
            expiration TEXT,
            delta REAL,
            put_premium REAL,
            call_premium REAL,
            option_id TEXT,
            position_type TEXT,
            average_price REAL,
            quantity REAL,
            chain_symbol TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.debug("Options positions table initialized")


def parse_and_store_options_positions(option_positions: list, equity_positions: list = None) -> bool:
    """Parse options positions and store in database with calculated trade types"""
    try:
        if equity_positions is None:
            equity_positions = []
        
        # Ensure table exists
        init_options_positions_table()
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Before the loop, get all existing option_ids with strikes from database to avoid API calls
        cursor.execute('''
            SELECT DISTINCT option_id, strike, expiration
            FROM options_positions 
            WHERE option_id IS NOT NULL AND strike > 0
        ''')
        cached_options = {row[0]: {'strike': row[1], 'expiration': row[2]} for row in cursor.fetchall()}
        logger.info(f"Found {len(cached_options)} cached option_ids in database")
        
        logger.info(f"Parsing {len(option_positions)} options positions...")
        
        api_calls_made = 0
        api_calls_skipped = 0
        
        for position in option_positions:
            position_id = position.get('id', '')
            option_id = position.get('option_id', '')
            chain_symbol = position.get('chain_symbol', '')
            position_type = position.get('type', '')
            quantity = float(position.get('quantity', 0))
            average_price = float(position.get('average_price', 0))
            expiration_date = position.get('expiration_date', '')
            opened_at = position.get('opened_at', position.get('created_at', ''))
            
            # Check if we already have this option_id in database with valid strike
            option_details = {}
            strike = 0
            delta = 0
            option_type = ''
            
            if option_id in cached_options:
                # We have cached data - use it to avoid API call
                cached = cached_options[option_id]
                strike = cached['strike']
                expiration_date = cached['expiration'] if cached['expiration'] else expiration_date
                api_calls_skipped += 1
                logger.debug(f"Using cached data for option_id {option_id}, strike: {strike}")
                
                # Still need to fetch option_type for trade type determination
                # This is a minimal API call - we could cache option_type too in the future
                option_details = get_option_instrument_details(option_id, use_cache=False)
                if option_details:
                    option_type = option_details.get('option_type', '')
                    delta = option_details.get('delta', 0)
            else:
                # New option_id, fetch from API
                option_details = get_option_instrument_details(option_id, use_cache=False)
                api_calls_made += 1
                if option_details:
                    strike = option_details.get('strike_price', 0)
                    delta = option_details.get('delta', 0)
                    option_type = option_details.get('option_type', '')
                    # Cache it for future iterations
                    cached_options[option_id] = {'strike': strike, 'expiration': expiration_date}
            
            # Determine trade type (only if we have option type)
            if option_type:
                trade_type = determine_trade_type(position, option_positions, equity_positions)
            else:
                # Fallback: use position type
                trade_type = f"{position_type.capitalize()} Option"
            
            # Calculate premiums based on average_price
            # Negative average_price = credit (money received)
            # Positive average_price = debit (money paid)
            # Premium = num_contracts * average_price 
            # Example: average_price = -131.00, quantity = 12 contracts
            #   Put Premium = 12 * (-131) = -1572 (negative = credit received)
            put_premium = 0
            call_premium = 0
            
            # Calculate total premium (average_price is per share, no need to multiply by 100)
            # Keep the sign: negative = credit, positive = debit
            total_premium = -1 *average_price * quantity
            
            # Update premiums based on position_type (long or short)
            if position_type == 'long':
                # Long position: update only call premium, leave put premium as 0
                call_premium = total_premium
                put_premium = 0
            elif position_type == 'short':
                # Short position: update only put premium, leave call premium as 0
                put_premium = total_premium
                call_premium = 0
            else:
                # Fallback: if position_type is unknown, use option_type
                if option_type == 'put':
                    put_premium = total_premium
                    call_premium = 0
                elif option_type == 'call':
                    call_premium = total_premium
                    put_premium = 0
            
            # Determine status (Open, Closed, Expired)
            status = 'Open'
            if quantity == 0:
                status = 'Closed'
            # Could add expiration check here
            
            # Parse open date
            open_date = ''
            if opened_at:
                try:
                    dt = datetime.fromisoformat(opened_at.replace('Z', '+00:00'))
                    open_date = dt.strftime('%Y-%m-%d')
                except:
                    open_date = opened_at[:10] if len(opened_at) >= 10 else ''
            
            # Insert or update position
            cursor.execute('''
                INSERT OR REPLACE INTO options_positions (
                    position_id, open_date, symbol, trade_type, status, num_contracts,
                    strike, expiration, delta, put_premium, call_premium, option_id,
                    position_type, average_price, quantity, chain_symbol, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                position_id, open_date, chain_symbol, trade_type, status, quantity,
                strike, expiration_date, delta, put_premium, call_premium, option_id,
                position_type, average_price, quantity, chain_symbol
            ))
            
            logger.debug(f"Stored position: {chain_symbol} {trade_type} - {quantity} contracts @ ${strike}")
        
        conn.commit()
        conn.close()
        
        logger.info(f"Successfully parsed and stored {len(option_positions)} options positions")
        logger.info(f"API calls made: {api_calls_made}, API calls skipped (cached): {api_calls_skipped}")
        return True
        
    except Exception as e:
        logger.error(f"Error parsing and storing options positions: {e}", exc_info=True)
        return False


def get_account_balance_and_positions() -> Dict[str, Any]:
    """Get account balance and open positions"""
    try:
        # Get account profile
        profile = rh.account.load_account_profile()
        
        # Get portfolio
        portfolio = rh.account.load_portfolio_profile()
        
        # Get positions
        positions = rh.account.get_all_positions()
        
        # Get open option positions
        option_positions = []
        try:
            option_positions = rh.get_open_option_positions()
            logger.info(f"Fetched {len(option_positions) if option_positions else 0} option positions from API")
        except Exception as e:
            logger.warning(f"Error fetching option positions: {e}")
            logger.debug(f"Exception details: {type(e).__name__}: {e}")
            # Try alternative method
            try:
                option_positions = rh.get_all_option_positions()
                logger.info(f"Fetched {len(option_positions) if option_positions else 0} option positions using alternative method")
            except Exception as e2:
                logger.warning(f"Alternative method also failed: {e2}")
                option_positions = []
        
        # Parse and store options positions in database
        if option_positions and len(option_positions) > 0:
            logger.info(f"Parsing and storing {len(option_positions)} options positions...")
            success = parse_and_store_options_positions(option_positions, positions)
            if success:
                # Export to CSV
                csv_file = Path("test_output/options_positions.csv")
                csv_file.parent.mkdir(exist_ok=True)
                export_options_positions_to_csv(str(csv_file))
            else:
                logger.error("Failed to parse and store options positions")
        else:
            logger.warning(f"No option positions to parse (got {len(option_positions) if option_positions else 0} positions)")
        
        result = {
            'account_profile': profile,
            'portfolio': portfolio,
            'equity_positions': positions,
            'option_positions': option_positions,
            'timestamp': datetime.now().isoformat()
        }
        
        return result
    except Exception as e:
        logger.error(f"Error fetching account balance and positions: {e}")
        return {}


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


def save_test_data(image_path: str, tweet_text: str, order_id: str, output_dir: Path = Path("test_output")):
    """Save test data locally instead of posting to Twitter"""
    output_dir.mkdir(exist_ok=True)
    
    # Copy image to test output directory
    test_image_path = output_dir / f"trade_card_{order_id[:8]}.png"
    try:
        import shutil
        shutil.copy2(image_path, test_image_path)
        logger.info(f"Saved test image: {test_image_path}")
    except Exception as e:
        logger.error(f"Failed to copy image: {e}")
    
    # Save tweet text to file
    text_file = output_dir / f"tweet_{order_id[:8]}.txt"
    try:
        with open(text_file, 'w') as f:
            f.write(tweet_text)
        logger.info(f"Saved tweet text: {text_file}")
    except Exception as e:
        logger.error(f"Failed to save tweet text: {e}")


def main():
    """Main execution function"""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Robinhood Trade Bot - Post trades to Twitter')
    parser.add_argument('--test-mode', action='store_true', 
                       help='Test mode: save images and text locally instead of posting to Twitter')
    parser.add_argument('--options-orders', action='store_true',
                       help='Get options trades instead of equity trades')
    parser.add_argument('--balance', action='store_true',
                       help='Get account balance and overview of open positions')
    parser.add_argument('--export-options', action='store_true',
                       help='Export options positions to CSV file')
    
    args = parser.parse_args()
    
    try:
        logger.info("=" * 50)
        logger.info("Starting Robinhood Trade Bot")
        if args.test_mode:
            logger.info("TEST MODE: Will save images/text locally, not post to Twitter")
        if args.options_orders:
            logger.info("OPTIONS MODE: Processing options trades")
        if args.balance:
            logger.info("BALANCE MODE: Fetching account balance and positions")
        logger.info("=" * 50)
        
        # Authenticate with Robinhood
        auth = RobinhoodAuth()
        if not auth.login():
            logger.error("Failed to authenticate with Robinhood")
            return False
        
        # Handle export options mode
        if args.export_options:
            logger.info("Exporting options positions to CSV...")
            csv_file = Path("test_output/options_positions.csv")
            csv_file.parent.mkdir(exist_ok=True)
            if export_options_positions_to_csv(str(csv_file)):
                logger.info(f"Options positions exported to: {csv_file}")
                return True
            else:
                logger.error("Failed to export options positions")
                return False
        
        # Handle balance mode
        if args.balance:
            logger.info("Fetching account balance and positions...")
            account_data = get_account_balance_and_positions()
            
            if account_data:
                # Save to JSON file
                output_file = Path("test_output/account_balance.json")
                with open(output_file, 'w') as f:
                    json.dump(account_data, f, indent=2, default=str)
                logger.info(f"Account data saved to: {output_file}")
                
                # Print summary
                profile = account_data.get('account_profile', {})
                portfolio = account_data.get('portfolio', {})
                
                logger.info("\n" + "=" * 50)
                logger.info("ACCOUNT SUMMARY")
                logger.info("=" * 50)
                if profile:
                    logger.info(f"Account Number: {profile.get('account_number', 'N/A')}")
                    logger.info(f"Username: {profile.get('username', 'N/A')}")
                if portfolio:
                    logger.info(f"Equity: ${float(portfolio.get('equity', 0)):,.2f}")
                    logger.info(f"Buying Power: ${float(portfolio.get('buying_power', 0)):,.2f}")
                    logger.info(f"Cash: ${float(portfolio.get('cash', 0)):,.2f}")
                
                positions = account_data.get('equity_positions', [])
                logger.info(f"\nOpen Equity Positions: {len(positions)}")
                for pos in positions[:10]:  # Show first 10
                    symbol = pos.get('symbol', 'N/A')
                    quantity = pos.get('quantity', 0)
                    logger.info(f"  {symbol}: {quantity} shares")
                
                option_positions = account_data.get('option_positions', [])
                logger.info(f"\nOpen Option Positions: {len(option_positions)}")
                
                # Show summary from database if available
                try:
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT symbol, trade_type, num_contracts, strike, expiration, 
                               delta, put_premium, call_premium
                        FROM options_positions
                        WHERE status = 'Open'
                        ORDER BY symbol, expiration
                        LIMIT 20
                    ''')
                    db_positions = cursor.fetchall()
                    conn.close()
                    
                    if db_positions:
                        logger.info("\nOptions Positions Summary (from database):")
                        logger.info(f"{'Symbol':<8} {'Type':<30} {'Contracts':<10} {'Strike':<10} {'Exp':<12} {'Delta':<8}")
                        logger.info("-" * 90)
                        for pos in db_positions:
                            symbol, trade_type, contracts, strike, exp, delta, put_prem, call_prem = pos
                            logger.info(f"{symbol:<8} {trade_type:<30} {contracts:<10.1f} ${strike:<9.2f} {exp:<12} {delta:<8.4f}")
                    else:
                        logger.info("  (No positions in database yet)")
                except Exception as e:
                    logger.debug(f"Could not fetch positions from database: {e}")
                
                logger.info("=" * 50)
            return True
        
        # Initialize components
        is_options = args.options_orders
        tracker = TradeTracker(is_options=is_options)
        card_generator = TradeCardGenerator()
        
        # Only initialize Twitter poster if not in test mode
        twitter_poster = None
        if not args.test_mode:
            twitter_poster = TwitterPoster()
        
        # Get recent trades
        if is_options:
            logger.info("Fetching recent options trades...")
            trades = get_recent_options_trades(limit=10)
        else:
            logger.info("Fetching recent equity trades...")
            trades = get_recent_trades(limit=10)
        
        if not trades:
            logger.info("No trades found to process")
            return True
        
        # Process trades (newest first)
        trades.reverse()
        posted_count = 0
        failed_count = 0
        skipped_count = 0
        saved_count = 0
        
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
            if is_options:
                trade_data = format_options_trade_data(order)
            else:
                trade_data = format_trade_data(order)
            
            # Generate trade card
            image_path = f"trade_card_{order_id[:8]}.png"
            card_generator.generate_card(trade_data, image_path)
            
            # Create tweet text
            action = "Bought" if trade_data['side'] == 'buy' else "Sold"
            if is_options:
                tweet_text = f"{action} {trade_data['quantity']} {trade_data['symbol']} options contracts at ${trade_data['price']:.2f}"
            else:
                tweet_text = f"{action} {trade_data['quantity']} shares of {trade_data['symbol']} at ${trade_data['price']:.2f}"
            
            # Test mode: save locally
            if args.test_mode:
                save_test_data(image_path, tweet_text, order_id)
                tracker.mark_posted(order_id)
                saved_count += 1
                logger.info(f"Saved test data for trade: {order_id}")
            else:
                # Post to Twitter
                try:
                    if twitter_poster.post_trade(image_path, tweet_text):
                        tracker.mark_posted(order_id)
                        posted_count += 1
                        logger.info(f"Successfully posted trade: {order_id}")
                        
                        # Add a small delay between posts to avoid rate limits
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
            
            # Clean up image file (unless in test mode)
            if not args.test_mode:
                try:
                    os.remove(image_path)
                except:
                    pass
        
        logger.info(f"Processing complete:")
        if args.test_mode:
            logger.info(f"  Saved (test mode): {saved_count}")
        else:
            logger.info(f"  Posted: {posted_count}")
        logger.info(f"  Failed: {failed_count}")
        logger.info(f"  Skipped (already posted): {skipped_count}")
        logger.info(f"  Remaining: {len(trades) - posted_count - skipped_count - failed_count - saved_count}")
        logger.info("=" * 50)
        return True
        
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

