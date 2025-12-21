import os
import time
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from get_market import get_tokens_for_market
from fast_goal_bet import (
    PriceTracker,
    get_market_data,
    HOST,
    CHAIN_ID,
    PRIVATE_KEY,
    POLYMARKET_PROXY_ADDRESS,
    SLUG
)


def main():
    # Ensure env vars are loaded in case this file is run directly
    load_dotenv(override=True)

    tokens = get_tokens_for_market(SLUG) or {}

    HOME_TOKEN_ID = tokens.get("home-yes")
    AWAY_TOKEN_ID = tokens.get("away-yes")
    if not HOME_TOKEN_ID or not AWAY_TOKEN_ID:
        raise ValueError(f"Could not resolve token ids for slug '{SLUG}'. Got: {tokens}")

    client = ClobClient(
        HOST,
        key=PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=1,
        funder=POLYMARKET_PROXY_ADDRESS,
    )
    client.set_api_creds(client.create_or_derive_api_creds())

    tokens = {
        "HOME": HOME_TOKEN_ID,
        "AWAY": AWAY_TOKEN_ID,
    }

    tracker = PriceTracker(client, token_ids=list(tokens.values()))
    tracker.start()

    try:
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            print("--- Price Tracker ---")
            for label, token_id in tokens.items():
                current, old = get_market_data(client, token_id, tracker)
                if current is None or old is None:
                    print(f"{label}: price unavailable (network error?)")
                    continue
                change_cents = (current - old) * 100  # show move in cents instead of percent
                print(f"{label}: current={current:.4f} | 1m_ago={old:.4f} | Δ1m={change_cents:+.2f}¢")
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        tracker.stop()


if __name__ == "__main__":
    main()
