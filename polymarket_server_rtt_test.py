#!/usr/bin/env python3
"""
Round-trip-time (RTT) test against the Polymarket CLOB websocket endpoint.

Uses websocket ping/pong frames to measure latency. Example:
  python polymarket_server_rtt_test.py --count 30 --interval 0.5
"""

import argparse
import asyncio
import statistics
import time

import websockets


DEFAULT_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


async def measure_rtt(url: str, count: int, interval: float, timeout: float):
    rtts_ms = []
    async with websockets.connect(
        url,
        ping_interval=None,  # manual pings for precise timing
        ping_timeout=None,
        close_timeout=5,
        max_queue=64,
    ) as ws:
        print(f"Connected to {url}")
        try:
            print(f"Local/remote: {ws.local_address} -> {ws.remote_address}")
        except Exception:
            pass
        for i in range(1, count + 1):
            start = time.perf_counter()
            waiter = ws.ping()
            try:
                await asyncio.wait_for(waiter, timeout=timeout)
                rtt_ms = (time.perf_counter() - start) * 1000.0
                rtts_ms.append(rtt_ms)
                print(f"{i:03d}: {rtt_ms:.2f} ms")
            except asyncio.TimeoutError:
                print(f"{i:03d}: timeout after {timeout}s waiting for pong")
            except Exception as exc:
                print(f"{i:03d}: ping failed: {exc}")
            await asyncio.sleep(interval)

    if not rtts_ms:
        print("No RTT samples collected.")
        return

    print("\nRTT stats (ms):")
    print(f"  min: {min(rtts_ms):.2f}")
    print(f"  max: {max(rtts_ms):.2f}")
    print(f"  avg: {statistics.mean(rtts_ms):.2f}")
    if len(rtts_ms) >= 2:
        print(f"  stdev: {statistics.pstdev(rtts_ms):.2f}")


def main():
    parser = argparse.ArgumentParser(description="RTT tester for Polymarket websocket.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Websocket URL to test.")
    parser.add_argument("--count", type=int, default=50, help="Number of ping/pong samples to collect.")
    parser.add_argument("--interval", type=float, default=0.1, help="Seconds between pings.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Seconds to wait for each pong.")
    args = parser.parse_args()

    asyncio.run(measure_rtt(args.url, args.count, args.interval, args.timeout))


if __name__ == "__main__":
    main()
