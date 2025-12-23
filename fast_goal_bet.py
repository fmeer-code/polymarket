from get_market import get_tokens_for_market
from helpers import PriceTracker, execute_trade, create_client


SLUG = "epl-mun-new-2025-12-26"

tokens = get_tokens_for_market(SLUG) or {}

HOME_YES = tokens.get("home-yes")
AWAY_YES = tokens.get("away-yes")


if not HOME_YES or not AWAY_YES:
    raise ValueError(f"Could not resolve token ids for slug '{SLUG}'. Got: {tokens}")

# The amount of USDC you want to spend (approximate, since orders are in shares)
TRADE_AMOUNT_USDC = 9



def main():
    # Initialize Client
    client = create_client()

    tracker = PriceTracker(client, token_ids=[HOME_YES, AWAY_YES])
    tracker.start()
    
    print(f"--- Soccer Live Trader ---")
    print(f"Target Trade Amount: ${TRADE_AMOUNT_USDC}")
    
    try:
        while True:
            val = input("\nWho scored? (1=Home, 2=Away, q=Quit): ")
            
            if val == '1':
                print("Processing Home Team goal...")
                execute_trade(client, HOME_YES, tracker, TRADE_AMOUNT_USDC)
            elif val == '2':
                print("Processing Away Team goal...")
                execute_trade(client, AWAY_YES, tracker, TRADE_AMOUNT_USDC)
            elif val.lower() == 'q':
                break
            else:
                print("Invalid input.")
    finally:
        tracker.stop()

if __name__ == "__main__":
    main()
