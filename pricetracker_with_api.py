import os
import time
from dotenv import load_dotenv
from get_market import get_tokens_for_market
from helpers import PriceTracker, get_market_data, create_client
from fast_goal_bet import (

    SLUG
)


def main():
    # Ensure env vars are loaded in case this file is run directly
    load_dotenv(override=True)

    tokens = get_tokens_for_market(SLUG) or {}

    HOME_YES = tokens.get("home-yes")
    AWAY_YES = tokens.get("away-yes")
    if not HOME_YES or not AWAY_YES:
        raise ValueError(f"Could not resolve token ids for slug '{SLUG}'. Got: {tokens}")

    client = create_client()

    tokens = {
        "HOME": HOME_YES,
        "AWAY": AWAY_YES,
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
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        tracker.stop()


if __name__ == "__main__":
    main()
