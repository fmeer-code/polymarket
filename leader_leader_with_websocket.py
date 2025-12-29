#!/usr/bin/env python3
"""
Polymarket CLOB WebSocket Price Tracker (market channel)

What it does (matches your REST tracker logic, but WS-fast):
- Subscribes to the public market websocket for two token IDs (asset_ids)
- Maintains ~60s rolling history per token of the last trade price
- Prints: current last trade, ~1m_ago last trade, Δ1m in cents (if no trade in 1m, old=current)
- Also tracks best_bid/best_ask and last_trade metadata when available
- Handles the important gotcha: the initial "book" snapshot may arrive as a LIST of events.

Requirements:
  pip install websockets python-dotenv

Assumptions:
- get_tokens_for_market(SLUG) returns {"home-yes": "...", "away-yes": "..."}
- fast_goal_bet.py provides SLUG
"""

import os
import json
import random
import time
import asyncio
from collections import deque
from typing import Dict, Optional, Tuple, List

import websockets
from dotenv import load_dotenv
from helpers import create_client, execute_trade_leader_leader


WSS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

LOOKBACK_SECONDS = 60
TRADE_HISTORY_SECONDS = LOOKBACK_SECONDS * 2  # keep enough history to cover the capture window
PRINT_INTERVAL = 0.5        # how often to refresh the screen
PING_INTERVAL = 10          # keepalive
RECONNECT_MAX_BACKOFF = 30  # seconds

TRIGGER_AMOUNT = 0.1      # USD price move on leader to trigger capture
TRADE_AMOUNT_USDC = 10.0    # notional USD amount to trade on lagger when triggered
ORDER_MONITOR_TIMEOUT = 8  # seconds to wait before resuming price checks if not filled
TRADE_COOLDOWN_SECONDS = 180  # prevent repeated orders off same move window
STALE_OLD_SECONDS = 300  # only mark "old" as unavailable after 5 minutes without trades

def _now_s() -> int:
    return int(time.time())


def _ts_ms_to_s(ts_ms: Optional[str]) -> int:
    try:
        return int(int(ts_ms) / 1000)
    except Exception:
        return _now_s()


def _to_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(str(x))
    except Exception:
        return None


class TokenState:
    """
    Keeps best bid/ask + last trade + rolling history of last trade prices (and best_ask for reference).
    """
    def __init__(self, lookback_seconds: int = LOOKBACK_SECONDS):
        self.lookback_seconds = lookback_seconds

        self.best_bid: Optional[float] = None
        self.best_ask: Optional[float] = None

        self.last_trade_price: Optional[float] = None
        self.last_trade_side: Optional[str] = None
        self.last_trade_size: Optional[float] = None  # notional USD
        self.last_trade_ts_s: Optional[int] = None

        # Rolling history of last trades: deque[(ts_s, price, notional_usd)]
        self.trade_history: deque[Tuple[int, float, Optional[float]]] = deque()

        # Rolling history of BUY price proxy (best_ask): deque[(ts_s, best_ask)]
        self.ask_history: deque[Tuple[int, float]] = deque()

        # When the last update arrived
        self.last_update_ts_s: Optional[int] = None

    def _trim(self, now_s: int):
        while self.ask_history and (now_s - self.ask_history[0][0] > self.lookback_seconds):
            self.ask_history.popleft()

    def _record_best_ask(self, ts_s: int, best_ask: float):
        self.best_ask = best_ask
        self.ask_history.append((ts_s, best_ask))
        self._trim(ts_s)

    def _trim_trades(self, now_s: int):
        # keep enough history to cover capture window (~2 minutes)
        while len(self.trade_history) > 1 and (now_s - self.trade_history[1][0] > TRADE_HISTORY_SECONDS):
            self.trade_history.popleft()

    def _record_trade(self, ts_s: int, price: float, side: Optional[str], size: Optional[float]):
        notional = price * size if (price is not None and size is not None) else size
        self.last_trade_price = price
        self.last_trade_side = side
        self.last_trade_size = notional
        self.last_trade_ts_s = ts_s
        self.trade_history.append((ts_s, price, notional))
        self._trim_trades(ts_s)
        return {"ts": ts_s, "price": price, "size": notional}

    def update_from_book(self, ts_s: int, bids, asks, last_trade_price=None):
        """
        book message includes full aggregated levels. We take top-of-book.
        """
        self.last_update_ts_s = ts_s

        bb = _to_float(bids[0]["price"]) if bids else None
        ba = _to_float(asks[0]["price"]) if asks else None

        if bb is not None:
            self.best_bid = bb
        if ba is not None:
            # only append if changed to reduce noise
            if self.best_ask is None or abs(ba - self.best_ask) > 1e-12:
                self._record_best_ask(ts_s, ba)

        # Some book snapshots include last_trade_price (as seen in your output)
        ltp = _to_float(last_trade_price)
        if ltp is not None:
            return self._record_trade(ts_s, ltp, None, None)
        return None

    def update_from_price_change(self, ts_s: int, best_bid, best_ask):
        """
        price_change messages include best_bid/best_ask in each price_changes entry.
        """
        self.last_update_ts_s = ts_s

        bb = _to_float(best_bid)
        ba = _to_float(best_ask)

        if bb is not None:
            self.best_bid = bb
        if ba is not None:
            if self.best_ask is None or abs(ba - self.best_ask) > 1e-12:
                self._record_best_ask(ts_s, ba)

    def update_from_last_trade(self, ts_s: int, price, side, size):
        self.last_update_ts_s = ts_s

        p = _to_float(price)
        s = _to_float(size)
        if p is not None:
            return self._record_trade(ts_s, p, str(side) if side is not None else None, s)
        return None

    def get_current_and_old_ask(self) -> Tuple[Optional[float], Optional[float]]:
        """
        Returns (current_best_ask, ~1m_ago_best_ask) using the deque.
        """
        now_s = _now_s()
        self._trim(now_s)

        cur = self.best_ask
        if not self.ask_history:
            return cur, None

        # oldest point that is >= lookback seconds ago if available; else oldest in deque
        target = self.lookback_seconds
        old = self.ask_history[0][1]
        for ts_s, price in self.ask_history:
            if now_s - ts_s >= target:
                old = price
            else:
                break
        return cur, old

    def get_current_and_old_trade(self) -> Tuple[Optional[float], Optional[float]]:
        """
        Returns (last_trade_price, trade_price_1m_ago). Only set old=None if no trade for
        at least STALE_OLD_SECONDS.
        """
        now_s = _now_s()
        self._trim_trades(now_s)

        if not self.trade_history:
            return None, None

        cur_ts_s, cur_price, _cur_size = self.trade_history[-1]

        # No trade in the stale window; we consider "old" unavailable.
        if now_s - cur_ts_s >= STALE_OLD_SECONDS:
            return cur_price, None

        # Default to current when we have activity in the last 5 minutes.
        old = cur_price
        for ts_s, price, _size in self.trade_history:
            if now_s - ts_s >= self.lookback_seconds:
                old = price
            else:
                break
        return cur_price, old

    def get_recent_trades(self, since_ts: int, until_ts: int) -> List[Tuple[int, float, Optional[float]]]:
        """
        Returns trades between since_ts and until_ts (inclusive).
        """
        return [(ts, price, size) for ts, price, size in self.trade_history if since_ts <= ts <= until_ts]


async def ping_loop(ws: websockets.WebSocketClientProtocol, interval: int = PING_INTERVAL):
    while True:
        try:
            await ws.send("PING")
        except Exception:
            return
        await asyncio.sleep(interval)


async def printer_loop(labels: Dict[str, str], state: Dict[str, TokenState], lock: asyncio.Lock, order_state: Dict[str, str], name: str):
    while True:
        os.system("clear" if os.name == "posix" else "cls")
        print(f"--- WS Price Tracker: {name} ---")
        print(f"WS: {WSS_URL}")
        print(f"Lookback: {LOOKBACK_SECONDS}s | Print: {PRINT_INTERVAL}s")
        print()

        async with lock:
            if order_state.get("active"):
                print("Order: active (monitoring lagger order, price updates paused)")
            elif order_state.get("last_status"):
                last_status = order_state["last_status"]
                if last_status == "skipped_limit":
                    print("Order: limit_price capped at $0.90")
                else:
                    print(f"Order: last status = {last_status}")

            for label, asset_id in labels.items():
                st = state.get(asset_id)
                if not st:
                    print(f"{label}: no state")
                    continue

                spread = None
                if st.best_bid is not None and st.best_ask is not None:
                    spread = st.best_ask - st.best_bid
                spread_str = f"{spread:.4f}" if spread is not None else "—"

                cur, old = st.get_current_and_old_trade()
                bb, ba = st.best_bid, st.best_ask

                if cur is None or old is None:
                    print(
                        f"{label}: cur={cur} old={old} | best_bid={bb} best_ask={ba} | spread={spread_str}"
                    )
                    continue

                delta_cents = (cur - old) * 100.0

                trade_str = ""
                if st.last_trade_price is not None and st.last_trade_ts_s is not None:
                    trade_age = _now_s() - st.last_trade_ts_s
                    side = st.last_trade_side or "?"
                    size_str = f"${st.last_trade_size:.2f}" if st.last_trade_size is not None else "?"
                    trade_str = f" | last_trade={st.last_trade_price:.4f} ({side}, notional={size_str}, age={trade_age}s)"

                print(
                    f"{label}: current={cur:.4f} | 1m_ago={old:.4f} | Δ1m={delta_cents:+.2f}¢"
                    f" | best_bid={bb if bb is not None else '—'} best_ask={ba if ba is not None else '—'}"
                    f" | spread={spread_str}"
                    f"{trade_str}"
                )

        await asyncio.sleep(PRINT_INTERVAL)


def _ts_to_iso(ts_s: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_s))


async def ws_run(asset_ids, labels: Dict[str, str], client, name: str):
    # shared state
    state: Dict[str, TokenState] = {aid: TokenState(LOOKBACK_SECONDS) for aid in asset_ids}
    lock = asyncio.Lock()
    initial_cooldown_until = _now_s() + TRADE_COOLDOWN_SECONDS
    order_state: Dict[str, str] = {
        "active": False,
        "last_status": "",
        "cooldown_until": initial_cooldown_until,
    }

    leader_aid = labels.get("LEADER")
    lagger_aid = labels.get("LAGGER")
    if not leader_aid or not lagger_aid:
        raise ValueError("Both LEADER and LAGGER token ids are required.")

    async def run_lagger_trade(lagger_reference_price: float, cur_leader_price: float):
        loop = asyncio.get_running_loop()
        try:
            status, filled = await loop.run_in_executor(
                None,
                lambda: execute_trade_leader_leader(
                    client,
                    lagger_aid,
                    lagger_reference_price,
                    TRADE_AMOUNT_USDC,
                    cur_leader_price,
                    TRIGGER_AMOUNT,
                    monitor_timeout=ORDER_MONITOR_TIMEOUT,
                ),
            )
        except Exception as exc:
            print(f"Lagger trade task failed: {exc}")
            status, filled = "error", 0.0
        async with lock:
            order_state["active"] = False
            order_state["last_status"] = status or "timeout"
        return status, filled

    printer_task = asyncio.create_task(printer_loop(labels, state, lock, order_state, name))
    current_order_task: Optional[asyncio.Task] = None
    def _on_order_done(task: asyncio.Task):
        nonlocal current_order_task
        try:
            task.result()
        except Exception as exc:
            print(f"Lagger order task error: {exc}")
        current_order_task = None

    backoff = 1
    try:
        while True:
            try:
                async with websockets.connect(
                    WSS_URL,
                    ping_interval=None,  # manual PING
                    ping_timeout=None,
                    close_timeout=5,
                    max_queue=4096,
                ) as ws:
                    sub_msg = {"assets_ids": asset_ids, "type": "market"}
                    await ws.send(json.dumps(sub_msg))
                    pinger = asyncio.create_task(ping_loop(ws))

                    backoff = 1
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue

                        # IMPORTANT GOTCHA:
                        # sometimes Polymarket sends a LIST of events (your initial book snapshot was a list)
                        events = data if isinstance(data, list) else [data]

                        async with lock:
                            for msg in events:
                                if not isinstance(msg, dict):
                                    continue

                                et = msg.get("event_type")
                                ts_s = _ts_ms_to_s(msg.get("timestamp"))

                                if et == "book":
                                    aid = msg.get("asset_id")
                                    if aid not in state:
                                        continue
                                    bids = msg.get("bids") or msg.get("buys") or []
                                    asks = msg.get("asks") or msg.get("sells") or []
                                    rec = state[aid].update_from_book(
                                        ts_s,
                                        bids=bids,
                                        asks=asks,
                                        last_trade_price=msg.get("last_trade_price"),
                                    )

                                elif et == "price_change":
                                    pcs = msg.get("price_changes") or []
                                    for pc in pcs:
                                        if not isinstance(pc, dict):
                                            continue
                                        aid = pc.get("asset_id")
                                        if aid not in state:
                                            continue
                                        state[aid].update_from_price_change(
                                            ts_s,
                                            best_bid=pc.get("best_bid"),
                                            best_ask=pc.get("best_ask"),
                                        )

                                elif et == "last_trade_price":
                                    aid = msg.get("asset_id")
                                    if aid not in state:
                                        continue
                                    state[aid].update_from_last_trade(
                                        ts_s,
                                        price=msg.get("price"),
                                        side=msg.get("side"),
                                        size=msg.get("size"),
                                    )

                                else:
                                    # tick_size_change or unknown event types: ignore
                                    pass

                            cur, old = state[leader_aid].get_current_and_old_trade()
                            now_s = _now_s()
                            if (
                                (not order_state["active"])
                                and now_s >= order_state.get("cooldown_until", 0)
                                and cur is not None
                                and old is not None
                                and (cur - old) > TRIGGER_AMOUNT
                            ):
                                lagger_cur, lagger_old = state[lagger_aid].get_current_and_old_trade()
                                lagger_reference_price = lagger_old
                                if lagger_reference_price is not None:
                                    order_state["active"] = True
                                    order_state["last_status"] = "submitting"
                                    order_state["cooldown_until"] = now_s + TRADE_COOLDOWN_SECONDS
                                    current_order_task = asyncio.create_task(run_lagger_trade(lagger_reference_price, cur))
                                    current_order_task.add_done_callback(_on_order_done)
                                    

                    # socket ended
                    pinger.cancel()

            except KeyboardInterrupt:
                raise
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_BACKOFF)

    finally:
        printer_task.cancel()
        if current_order_task:
            current_order_task.cancel()


def main():
    load_dotenv(override=True)
    client = create_client()

    name = "atalanta_win"  # example market slug
    leader_token = "9194885300383673299933778185576642916418573596607129598755350159961658345425"
    lagger_token = leader_token


    labels = {"LEADER": leader_token, "LAGGER": lagger_token}
    asset_ids = list(labels.values())

    asyncio.run(ws_run(asset_ids, labels, client, name))


if __name__ == "__main__":
    main()
