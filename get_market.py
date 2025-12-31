import requests
import json

def get_market_by_slug(market_slug):
    """
    Fetches market data from the Polymarket API using a market slug.
    """
    url = f"https://gamma-api.polymarket.com/events/slug/{market_slug}"

    try:
        response = requests.get(url)
        # Raise an exception for bad status codes (4XX or 5XX)
        response.raise_for_status()
        market_data = response.json()
        return market_data
    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None


def extract_questions_outcomes_token_ids(market_data):
    """
    Returns a list of dicts with question, outcomes, and token IDs for each market.
    The API returns some fields as JSON strings; we normalize them to lists.
    """
    markets = market_data.get("markets", [])
    results = []

    for market in markets:
        outcomes_raw = market.get("outcomes", [])
        token_ids_raw = market.get("clobTokenIds", [])

        # Outcomes and clobTokenIds are sometimes stored as JSON strings.
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw

        results.append(
            {
                "question": market.get("question"),
                "outcomes": outcomes,
                "tokenIds": token_ids,
            }
        )

    return results


def format_market_tokens(markets):
    """
    Formats markets into a home/draw/away token mapping:
    {
        home-yes: <token>,
        home-no: <token>,
        draw-yes: <token>,
        draw-no: <token>,
        away-yes: <token>,
        away-no: <token>
    }
    """
    formatted = {}
    team_slots = ["home", "away"]

    for market in markets:
        question = market.get("question", "")
        outcomes = market.get("outcomes", [])
        token_ids = market.get("tokenIds", [])

        # Align tokens with outcomes in case the API returns them in a different order.
        outcome_token_map = {
            outcome.lower(): token for outcome, token in zip(outcomes, token_ids)
        }
        yes_token = outcome_token_map.get("yes") or (token_ids[0] if token_ids else None)
        no_token = outcome_token_map.get("no") or (token_ids[1] if len(token_ids) > 1 else None)

        if "draw" in question.lower():
            prefix = "draw"
        else:
            prefix = team_slots.pop(0) if team_slots else f"team-{len(formatted)//2}"

        if yes_token:
            formatted[f"{prefix}-yes"] = yes_token
        if no_token:
            formatted[f"{prefix}-no"] = no_token

    return formatted


def get_tokens_for_market(slug, raw=False):
    """
    Returns token ids for a market slug.

    By default this returns a dict mapping each outcome bucket (home/away/draw)
    to its yes/no token ids, e.g.:
      {"home-yes": "...", "home-no": "...", "draw-yes": "...", "draw-no": "...", ...}

    Passing raw=True returns the raw list of markets with question/outcomes/tokenIds.
    """
    market_info = get_market_by_slug(slug)
    if not market_info:
        print(f"Could not retrieve market for slug: {slug}")
        return None

    trimmed = extract_questions_outcomes_token_ids(market_info)
    if raw:
        return trimmed

    return format_market_tokens(trimmed)


if __name__ == "__main__":
    # Example usage
    example_slug = "elc-cov-ips-2025-12-29"
    raw_markets = get_tokens_for_market(example_slug, raw=True)
    print("Raw markets:")
    print(raw_markets)

    formatted = get_tokens_for_market(example_slug)
    print("\nFormatted tokens:")
    print(json.dumps(formatted, indent=4))
