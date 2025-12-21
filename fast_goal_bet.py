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

    def __init__(self, client, token_ids, poll_interval=1, lookback_seconds=60):
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

def _num(val):
    try:
        return float(val)
    except Exception:
        return 0.0

def extract_order_id(resp):
    if not isinstance(resp, dict):
        return None
    for key in ("orderId", "orderID", "id", "orderHash"):
        if resp.get(key):
            return resp[key]
    for key, val in resp.items():
        if "order" in key.lower() and "id" in key.lower() and val:
            return val
    return None

def parse_order_info(order_info, original_size):
    status = str(order_info.get("status", "")).lower()
    fill_candidates = [
        order_info.get("filledSize"),
        order_info.get("filled"),
        order_info.get("totalFilled"),
        order_info.get("filledAmount"),
        order_info.get("fillAmount"),
    ]

    fills_list = order_info.get("fills") or order_info.get("recentFills") or []
    if isinstance(fills_list, list):
        fills_sum = sum(
            _num(fill.get("size") or fill.get("filled") or fill.get("makerAmount") or fill.get("takerAmount"))
            for fill in fills_list
            if isinstance(fill, dict)
        )
        fill_candidates.append(fills_sum)

    filled_size = max((_num(v) for v in fill_candidates), default=0.0)

    remaining_size = order_info.get("remainingSize") or order_info.get("remaining")
    if remaining_size is None:
        base_size = order_info.get("size") or order_info.get("quantity") or order_info.get("amount")
        if base_size is not None:
            try:
                remaining_size = _num(base_size) - filled_size
            except Exception:
                remaining_size = None

    if filled_size <= 0 and original_size:
        filled_size = original_size if status in ("filled", "closed", "matched") else filled_size

    return status, filled_size, remaining_size

def monitor_order(client, order_id, original_size, timeout=180):
    deadline = time.time() + timeout
    last_status = None
    last_filled = 0.0

    while time.time() < deadline:
        order_info = None

        try:
            if hasattr(client, "get_order"):
                order_info = client.get_order(order_id)
            elif hasattr(client, "get_order_status"):
                order_info = client.get_order_status(order_id)
        except Exception:
            order_info = None

        if order_info is None:
            try:
                resp = requests.get(f"{HOST}/orders/{order_id}", timeout=10)
                if resp.ok:
                    order_info = resp.json()
            except Exception:
                order_info = None

        if not order_info:
            time.sleep(1)
            continue

        status, filled_size, remaining_size = parse_order_info(order_info, original_size)
        last_status, last_filled = status, filled_size

        if status in ("filled", "closed", "matched"):
            return status, filled_size
        if status in ("cancelled", "expired"):
            return status, filled_size
        if remaining_size is not None and remaining_size <= 0:
            return status or "filled", filled_size or original_size

        time.sleep(1)

    return last_status, last_filled

def cash_out(client, token_id, filled_shares, tracker=None):
    """Submit a simple limit-sell to exit the filled position."""
    if filled_shares <= 0:
        print("No filled size to cash out.")
        return

    current_price, _ = get_market_data(client, token_id, tracker) if tracker else (None, None)
    if current_price is None:
        try:
            current_price = PriceTracker._as_float(client.get_price(token_id, side=SELL))
        except Exception:
            print("Cannot fetch price to cash out.")
            return

    # Slightly undercut the market to improve fill odds
    limit_price = round(max(0.01, current_price * 0.97), 2)
    print(f"Submitting cash-out sell: {filled_shares} shares at ${limit_price}...")

    order_args = OrderArgs(
        price=limit_price,
        size=filled_shares,
        side=SELL,
        token_id=token_id
    )

    try:
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        order_id = extract_order_id(resp)
        if not order_id:
            print("Cash-out submission failed (no order id).")
            return
        print(f"Cash-out submitted (id: {order_id})")
        status, filled = monitor_order(client, order_id, filled_shares, timeout=120)
        if status in ("filled", "closed", "matched"):
            print(f"Cash-out filled ({filled} shares).")
        elif status in ("cancelled", "expired"):
            print(f"Cash-out {status}. Filled {filled} shares.")
        else:
            print(f"Cash-out monitoring ended without fill. Last status: {status or 'unknown'}.")
    except PolyApiException as exc:
        msg = str(getattr(exc, "error_message", "")) or str(exc)
        if "not enough balance" in msg.lower():
            print("Cash-out skipped: no balance/allowance (likely already sold).")
        else:
            print(f"Cash-out order failed: {msg}")
    except Exception as exc:
        print(f"Cash-out order failed: {exc}")

def execute_trade(client, token_id, tracker):
    current_price, old_price = get_market_data(client, token_id, tracker)

    if current_price is None or old_price is None or abs(old_price) < 1e-9:
        print("Price data unavailable or invalid; skipping trade.")
        return
    
    # Check if price changed by more than 2%
    price_change = abs((current_price - old_price) / old_price)
    
    print(f"Current Price: {current_price:.4f} | 1m Ago: {old_price:.4f}")
    print(f"Price Change: {price_change*100:.2f}%")

    if price_change > 0.1:
        print("Price changed by > 10% in the last 1 min. Trade aborted.")
        return

    # Set limit price 2% over market (Aggressive Fill)
    limit_price = round(current_price * 1.05, 2)
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
    order_id = extract_order_id(resp)
    if not order_id:
        print("Order submission failed: no order id returned.")
        return
    print(f"Order submitted (id: {order_id})")

    status, filled = monitor_order(client, order_id, shares)
    if status in ("filled", "closed", "matched"):
        print(f"Order filled ({filled} shares).")
        user_choice = input("Press 's' to cash out now, anything else to continue: ").strip().lower()
        if user_choice == "s":
            cash_out(client, token_id, filled, tracker)
    elif status in ("cancelled", "expired"):
        print(f"Order {status}. Filled {filled} shares.")
    else:
        print(f"Order monitoring ended without fill. Last status: {status or 'unknown'}.")

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
