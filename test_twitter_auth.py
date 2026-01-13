#!/usr/bin/env python3
"""
Test script to verify Twitter API credentials
This helps identify which credential might be causing the 401 error
"""

import os
import sys
from dotenv import load_dotenv
import tweepy

# Load environment variables
load_dotenv()

def test_twitter_credentials():
    """Test Twitter API credentials step by step"""
    
    print("=" * 60)
    print("Twitter API Credentials Test")
    print("=" * 60)
    print()
    
    # Load credentials
    api_key = os.getenv("TWITTER_API_KEY")
    api_secret = os.getenv("TWITTER_API_SECRET")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN")
    access_token_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    
    # Check if credentials are loaded
    print("1. Checking if credentials are loaded from .env:")
    credentials = {
        "TWITTER_API_KEY": api_key,
        "TWITTER_API_SECRET": api_secret,
        "TWITTER_ACCESS_TOKEN": access_token,
        "TWITTER_ACCESS_TOKEN_SECRET": access_token_secret,
        "TWITTER_BEARER_TOKEN": bearer_token
    }
    
    all_present = True
    for name, value in credentials.items():
        if value and value.strip():
            print(f"   ✓ {name}: Present ({len(value)} characters)")
        else:
            print(f"   ✗ {name}: MISSING or EMPTY")
            all_present = False
    
    if not all_present:
        print("\n❌ Some credentials are missing. Please check your .env file.")
        return False
    
    print("\n2. Testing API v1.1 Authentication (OAuth 1.0a):")
    try:
        auth = tweepy.OAuth1UserHandler(
            api_key,
            api_secret,
            access_token,
            access_token_secret
        )
        api_v1 = tweepy.API(auth, wait_on_rate_limit=True)
        
        # Test authentication
        user = api_v1.verify_credentials()
        if user:
            print(f"   ✓ Authentication successful!")
            print(f"   ✓ Authenticated as: @{user.screen_name} ({user.name})")
            print(f"   ✓ User ID: {user.id}")
        else:
            print("   ✗ Authentication failed - no user data returned")
            return False
            
    except tweepy.Unauthorized as e:
        print(f"   ✗ Authentication failed (401 Unauthorized)")
        print(f"   Error: {e}")
        print("\n   Possible causes:")
        print("   - Access Token and Access Token Secret don't match the API Key/Secret")
        print("   - Credentials are incorrect or have been regenerated")
        print("   - App permissions are insufficient (need 'Read and Write')")
        return False
    except Exception as e:
        print(f"   ✗ Authentication error: {e}")
        print(f"   Error type: {type(e).__name__}")
        return False
    
    print("\n3. Testing API v2 Client:")
    try:
        client = tweepy.Client(
            bearer_token=bearer_token,
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
            wait_on_rate_limit=True
        )
        
        # Test by getting user info
        me = client.get_me()
        if me and me.data:
            print(f"   ✓ API v2 client authenticated successfully!")
            print(f"   ✓ User: @{me.data.username} ({me.data.name})")
            print(f"   ✓ User ID: {me.data.id}")
        else:
            print("   ✗ API v2 authentication failed - no user data")
            return False
            
    except tweepy.Unauthorized as e:
        print(f"   ✗ API v2 authentication failed (401 Unauthorized)")
        print(f"   Error: {e}")
        return False
    except Exception as e:
        print(f"   ✗ API v2 error: {e}")
        print(f"   Error type: {type(e).__name__}")
        return False
    
    print("\n4. Testing Write Permissions:")
    try:
        # Try to post a test tweet (you can delete it manually)
        test_text = "🧪 Test tweet from robinhood-trades-to-twitter bot - please ignore"
        print(f"   Attempting to post test tweet...")
        response = client.create_tweet(text=test_text)
        
        if response and response.data:
            tweet_id = response.data.get('id')
            print(f"   ✓ Write permissions verified!")
            print(f"   ✓ Test tweet posted: https://twitter.com/i/web/status/{tweet_id}")
            print(f"   ⚠️  Please delete this test tweet manually if desired")
        else:
            print("   ✗ Failed to post test tweet")
            return False
            
    except tweepy.Forbidden as e:
        print(f"   ✗ Write permissions denied (403 Forbidden)")
        print(f"   Error: {e}")
        print("\n   Your app needs 'Read and Write' permissions.")
        print("   Go to https://developer.twitter.com/en/portal/dashboard")
        print("   → Your App → Settings → User authentication settings")
        print("   → App permissions: Set to 'Read and Write'")
        return False
    except tweepy.Unauthorized as e:
        print(f"   ✗ Write test failed (401 Unauthorized)")
        print(f"   Error: {e}")
        return False
    except Exception as e:
        print(f"   ✗ Write test error: {e}")
        print(f"   Error type: {type(e).__name__}")
        return False
    
    print("\n" + "=" * 60)
    print("✅ All tests passed! Your Twitter credentials are working.")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = test_twitter_credentials()
    sys.exit(0 if success else 1)

