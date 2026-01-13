#!/bin/bash
# Setup script to create .env file from template

if [ ! -f .env ]; then
    cat > .env << 'EOF'
# Robinhood Credentials
ROBINHOOD_USERNAME=your_robinhood_username
ROBINHOOD_PASSWORD=your_robinhood_password
# Optional: If you have 2FA enabled, you can set this (or it will prompt)
ROBINHOOD_MFA_CODE=

# Twitter/X API Credentials
# Get these from https://developer.twitter.com/en/portal/dashboard
TWITTER_API_KEY=your_api_key
TWITTER_API_SECRET=your_api_secret
TWITTER_ACCESS_TOKEN=your_access_token
TWITTER_ACCESS_TOKEN_SECRET=your_access_token_secret
TWITTER_BEARER_TOKEN=your_bearer_token
EOF
    echo ".env file created! Please edit it with your credentials."
else
    echo ".env file already exists."
fi

