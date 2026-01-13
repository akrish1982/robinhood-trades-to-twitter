#!/usr/bin/env python3
"""
Reset the trade tracking database to reprocess all trades
"""

import os
from pathlib import Path

DB_FILE = "last_trade.db"

if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
    print(f"✓ Deleted {DB_FILE}")
    print("All trades will be reprocessed on the next run.")
else:
    print(f"Database file {DB_FILE} does not exist.")

