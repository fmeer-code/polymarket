import os
import time
import threading
from collections import deque
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.exceptions import PolyApiException
import requests
from get_market import get_market_by_slug

# Always re-read .env so edits take effect even if variables are already set
load_dotenv(override=True)

# --- CONFIGURATION ---
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon Mainnet
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "").strip()
print(PRIVATE_KEY,"   " ,POLYMARKET_PROXY_ADDRESS)

if not PRIVATE_KEY or not POLYMARKET_PROXY_ADDRESS:
    raise ValueError("PRIVATE_KEY and POLYMARKET_PROXY_ADDRESS must be set in the environment")

slug = "soccer-world-cup-2022-final-match-winner"

tokens = get_market_by_slug(slug)

HOME_TOKEN_ID = tokens.get("home-yes")
AWAY_TOKEN_ID = tokens.get("away-yes")

# The amount of USDC you want to spend (approximate, since orders are in shares)
TRADE_AMOUNT_USDC = 2

