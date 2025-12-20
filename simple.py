import os
import time
import threading
from collections import deque
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

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

HOME_TOKEN_ID = "37254017702181124531908733301847933711412517831469874147555123684166471909898"
AWAY_TOKEN_ID = "2326153255715579520413107440146878950099595845198944727703127772117025827352"

# The amount of USDC you want to spend (approximate, since orders are in shares)
TRADE_AMOUNT_USDC = 2

class PriceTracker:
    """Polls prices every few seconds and keeps ~1 minute of history per token."""

    @staticmethod
    def _as_float(price):
        """Normalize price response to float; handles dict payloads."""
        if isinstance(price, (int, float)):
            return float(price)
        if isinstance(price, str):
            return float(price)
        if isinstance(price, dict):
            for key in ("p", "price"):
                if key in price:
                    return float(price[key])
        raise ValueError(f"Unsupported price format: {price}")

    def __init__(self, client, token_ids, poll_interval=5, lookback_seconds=60):
        self.client = client
        self.token_ids = token_ids
        self.poll_interval = poll_interval
        self.lookback_seconds = lookback_seconds
        self.history = {tid: deque() for tid in token_ids}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.is_set():
            now = int(time.time())
            for token_id in self.token_ids:
                try:
                    raw_price = self.client.get_price(token_id, side=BUY)
                    price = self._as_float(raw_price)
                except Exception:
                    continue  # Skip this token if the price fetch fails
                with self._lock:
                    dq = self.history[token_id]
                    dq.append((now, price))
                    # Drop entries older than lookback window
                    while dq and now - dq[0][0] > self.lookback_seconds:
                        dq.popleft()
            self._stop.wait(self.poll_interval)

    def get_prices(self, token_id):
        """Return (current_price, ~1-minute-ago price) using cached history."""
        now = int(time.time())
        with self._lock:
            dq = self.history.get(token_id)
            if not dq:
                return None, None
            # Trim stale entries
            while dq and now - dq[0][0] > self.lookback_seconds:
                dq.popleft()
            if not dq:
                return None, None
            current_price = dq[-1][1]
            old_price = dq[0][1]
            for ts, price in dq:
                if now - ts >= self.lookback_seconds:
                    old_price = price
                else:
                    break
            return current_price, old_price

def get_market_data(client, token_id, tracker):
    """Fetches current price and price from ~1 minute ago using the tracker."""
    current_price, old_price = tracker.get_prices(token_id)
    if current_price is None:
        # Tracker has no data yet; fetch directly as a fallback
        try:
            current_price = PriceTracker._as_float(client.get_price(token_id, side=BUY))
            old_price = current_price
        except Exception:
            return None, None
    return current_price, old_price

def execute_trade(client, token_id, tracker):
    current_price, old_price = get_market_data(client, token_id, tracker)
    
    # Check if price changed by more than 2%
    price_change = abs((current_price - old_price) / old_price)
    
    print(f"Current Price: {current_price:.4f} | 1m Ago: {old_price:.4f}")
    print(f"Price Change: {price_change*100:.2f}%")

    if price_change > 0.02:
        print("Price changed by > 2% in the last 1 min. Trade aborted.")
        return

    # Set limit price 2% over market (Aggressive Fill)
    limit_price = round(current_price * 1.02, 2)
    if limit_price > 0.9: limit_price = 0.9 # Cap at 0.9
    
    # Calculate shares: (Amount / Price)
    shares = round(TRADE_AMOUNT_USDC / limit_price, 2)

    print(f"Placing Limit Order: {shares} shares at ${limit_price}...")
    
    order_args = OrderArgs(
        price=limit_price,
        size=shares,
        side=BUY,
        token_id=token_id
    )
    
    signed_order = client.create_order(order_args)
    resp = client.post_order(signed_order, OrderType.GTC)
    print("Order Response:", resp)

def main():
    # Initialize Client
    client = ClobClient(HOST, key=PRIVATE_KEY, chain_id=CHAIN_ID, signature_type=1, funder=POLYMARKET_PROXY_ADDRESS)
    client.set_api_creds(client.create_or_derive_api_creds())

    tracker = PriceTracker(client, token_ids=[HOME_TOKEN_ID, AWAY_TOKEN_ID])
    tracker.start()
    
    print(f"--- Soccer Live Trader ---")
    print(f"Target Trade Amount: ${TRADE_AMOUNT_USDC}")
    
    try:
        while True:
            val = input("\nWho scored? (1=Home, 2=Away, q=Quit): ")
            
            if val == '1':
                print("Processing Home Team goal...")
                execute_trade(client, HOME_TOKEN_ID, tracker)
            elif val == '2':
                print("Processing Away Team goal...")
                execute_trade(client, AWAY_TOKEN_ID, tracker)
            elif val.lower() == 'q':
                break
            else:
                print("Invalid input.")
    finally:
        tracker.stop()

if __name__ == "__main__":
    main()
