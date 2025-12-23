import asyncio
import json
import time
from collections import deque

import websockets

from get_market import get_tokens_for_market

# -------------------------------------------------
# CONFIG (same logic as your bot)
# -------------------------------------------------
WSS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

MARKET_SLUG = "lol-lng-wb-2025-12-23"

tokens = get_tokens_for_market(MARKET_SLUG)

HOME_YES = tokens.get("home-yes")
AWAY_YES = tokens.get("away-yes")
HOME_NO = tokens.get("home-no")
AWAY_NO = tokens.get("away-no")

ASSET_IDS = [
    HOME_NO,
    AWAY_NO,
]

ROLLING_WINDOW_SEC = 6.0
TRIGGER_JUMP_USD = 0.01

POST_TRIGGER_OBSERVE_SEC = 180  # 3 minutes
PRINT_INTERVAL_SEC = 0.5        # print twice per second

PING_INTERVAL = 20
PING_TIMEOUT = 20

# -------------------------------------------------
# SAFETY GATE (prevents nonsense markets like bid=0.01 / ask=0.99)
# -------------------------------------------------
MIN_BID_FOR_SIGNAL = 0.10       # require at least some real demand; blocks dust bids (0.01)
MAX_SPREAD_FOR_SIGNAL = 0.20    # require reasonably tight market; blocks huge spreads (0.98)
MIN_SEEN_SNAPSHOTS = 2          # require we have both assets populated before triggering

# -------------------------------------------------
# "ECONOMIC" PRICE SETTINGS (to emulate client.get_price side=BUY/SELL)
# -------------------------------------------------
MIN_LEVEL_SIZE_FOR_ECON_PRICE = 20.0  # ignore tiny/dust levels when building marketable prices

# -------------------------------------------------
# STATE
# -------------------------------------------------
ROLLING = {}          # asset_id -> deque[(ts, signal_price)]
LATEST = {}           # asset_id -> snapshot dict
TRIGGERED_AT = None
TRIGGER_ASSET = None

LAST_PRINT = 0.0

# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def _first_price_above_size(levels, min_size: float):
    """
    levels: list[{"price": "...", "size": "..."}] from websocket
    returns first (best) price where size >= min_size, else None
    """
    if not levels:
        return None
    for lvl in levels:
        try:
            p = float(lvl.get("price"))
            s = float(lvl.get("size", 0) or 0)
        except Exception:
            continue
        if p > 0 and s >= min_size:
            return p
    return None


def best_price(levels):
    """Raw top-of-book price (may be polluted by dust/anchors)."""
    if not levels:
        return None
    try:
        return float(levels[0]["price"])
    except Exception:
        return None


def rolling_jump_detected(asset_id: str, price: float) -> bool:
    now = time.time()
    dq = ROLLING.setdefault(asset_id, deque())
    dq.append((now, price))

    while dq and now - dq[0][0] > ROLLING_WINDOW_SEC:
        dq.popleft()

    if len(dq) < 2:
        return False

    min_price = min(p for _, p in dq)
    return (price - min_price) >= TRIGGER_JUMP_USD


def market_quotes_look_sane() -> bool:
    """
    Gate is about "can I plausibly execute here?"
    We gate using ECONOMIC buy/sell prices (dust-filtered), not raw best bid/ask.
    This makes the gate match what you'd actually get filled at.
    """
    if len(LATEST) < MIN_SEEN_SNAPSHOTS:
        return False

    for aid in ASSET_IDS:
        snap = LATEST.get(aid)
        if not snap:
            return False

        econ_sell = snap.get("econ_sell")  # like get_price(side=SELL) ~ bid-side marketable
        econ_buy = snap.get("econ_buy")    # like get_price(side=BUY)  ~ ask-side marketable
        if econ_sell is None or econ_buy is None:
            return False
        if econ_sell <= 0 or econ_buy <= 0:
            return False

        spread = econ_buy - econ_sell
        if econ_sell < MIN_BID_FOR_SIGNAL:
            return False
        if spread > MAX_SPREAD_FOR_SIGNAL:
            return False

    return True


def compute_prices_from_event(ev: dict):
    """
    Build economically meaningful prices from websocket book:
    - econ_buy  ~ marketable buy price (ask side), dust-filtered
    - econ_sell ~ marketable sell price (bid side), dust-filtered
    - last_trade (executed price if present)
    - signal_price: prefer last_trade, else midpoint of econ prices
    """
    bids = ev.get("bids") or []
    asks = ev.get("asks") or []

    # Raw top-of-book (for debugging)
    raw_bid = best_price(bids)
    raw_ask = best_price(asks)

    # Dust-filtered "marketable" prices (this is the websocket analogue of client.get_price)
    econ_sell = _first_price_above_size(bids, MIN_LEVEL_SIZE_FOR_ECON_PRICE)  # like SELL-side quote
    econ_buy = _first_price_above_size(asks, MIN_LEVEL_SIZE_FOR_ECON_PRICE)   # like BUY-side quote

    # last trade
    last_trade_raw = ev.get("last_trade_price")
    last_trade = None
    if last_trade_raw is not None:
        try:
            last_trade = float(last_trade_raw)
        except Exception:
            last_trade = None

    # Trigger signal: prefer actual executed trades; else use midpoint of marketable prices
    if last_trade is not None and last_trade > 0:
        signal_price = last_trade
    elif econ_buy is not None and econ_sell is not None and econ_buy > 0 and econ_sell > 0:
        signal_price = (econ_buy + econ_sell) / 2.0
    else:
        signal_price = None

    return {
        "raw_bid": raw_bid,
        "raw_ask": raw_ask,
        "econ_sell": econ_sell,
        "econ_buy": econ_buy,
        "last": last_trade,
        "signal": signal_price,
    }


def maybe_print_snapshot():
    global LAST_PRINT
    now = time.time()
    if now - LAST_PRINT < PRINT_INTERVAL_SEC:
        return
    LAST_PRINT = now

    ts = time.strftime("%H:%M:%S", time.localtime(now))
    lines = [f"{ts}"]

    for aid in ASSET_IDS:
        snap = LATEST.get(aid)
        if not snap:
            lines.append(f"  {aid[-6:]}  econSell=?   econBuy=?   spr=?     last=?   rawBid=? rawAsk=?  sig=?")
            continue

        econ_sell = snap.get("econ_sell")
        econ_buy = snap.get("econ_buy")
        last = snap.get("last")
        raw_bid = snap.get("raw_bid")
        raw_ask = snap.get("raw_ask")
        sig = snap.get("signal")

        if econ_sell is not None and econ_buy is not None:
            spr = econ_buy - econ_sell
            spr_str = f"{spr:.3f}"
        else:
            spr_str = "?"

        def fmt(x):
            return f"{x:.3f}" if isinstance(x, (float, int)) and x > 0 else "?"

        lines.append(
            f"  {aid[-6:]}  econSell={fmt(econ_sell)} econBuy={fmt(econ_buy)} spr={spr_str} "
            f" last={fmt(last)} rawBid={fmt(raw_bid)} rawAsk={fmt(raw_ask)} sig={fmt(sig)}"
        )

    lines.append(f"  gate={'OK' if market_quotes_look_sane() else 'BLOCK'}")
    print("\n".join(lines))


# -------------------------------------------------
# MAIN
# -------------------------------------------------
async def main():
    global TRIGGERED_AT, TRIGGER_ASSET

    print(f"Connecting to {WSS_MARKET_URL} ...")

    async with websockets.connect(
        WSS_MARKET_URL,
        ping_interval=PING_INTERVAL,
        ping_timeout=PING_TIMEOUT,
        max_size=10_000_000,
    ) as ws:
        await ws.send(json.dumps({"assets_ids": ASSET_IDS, "type": "market"}))
        print("Subscribed. Waiting for rolling trigger...\n")

        while True:
            raw = await ws.recv()
            try:
                data = json.loads(raw)
            except Exception:
                continue

            events = data if isinstance(data, list) else [data]

            for ev in events:
                if not isinstance(ev, dict):
                    continue
                if ev.get("event_type") != "book":
                    continue

                asset_id = ev.get("asset_id")
                if asset_id not in ASSET_IDS:
                    continue

                prices = compute_prices_from_event(ev)

                # Need at least economic prices to store a meaningful snapshot
                if prices["econ_buy"] is None or prices["econ_sell"] is None:
                    # still store raw + last for debugging if you want
                    LATEST[asset_id] = prices
                    maybe_print_snapshot()
                    continue

                LATEST[asset_id] = prices

                # ALWAYS print snapshots (before & after trigger)
                maybe_print_snapshot()

                # -------------------------------------------------
                # BEFORE TRIGGER
                # -------------------------------------------------
                if TRIGGERED_AT is None:
                    if not market_quotes_look_sane():
                        continue

                    signal_price = prices.get("signal")
                    if signal_price is None or signal_price <= 0:
                        continue

                    if rolling_jump_detected(asset_id, signal_price):
                        TRIGGERED_AT = time.time()
                        TRIGGER_ASSET = asset_id
                        print(
                            f"\n🚨 TRIGGER FIRED on {asset_id}\n"
                            f"    signal jumped >= {TRIGGER_JUMP_USD:.2f} within {ROLLING_WINDOW_SEC:.0f}s\n"
                            f"    (signal uses last_trade if available, else midpoint of dust-filtered marketable prices)\n"
                            f"    Entering 3-minute observation window...\n"
                        )

                # -------------------------------------------------
                # AFTER TRIGGER: EXIT AFTER 3 MIN
                # -------------------------------------------------
                if TRIGGERED_AT is not None:
                    if time.time() - TRIGGERED_AT >= POST_TRIGGER_OBSERVE_SEC:
                        print("\n⏱️ Observation window finished (3 minutes). Exiting.\n")
                        return


if __name__ == "__main__":
    asyncio.run(main())
