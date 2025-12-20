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


# Example Usage: Replace with the actual market slug
# Example slug format is typically 'will-biden-be-reelected-in-2024'
slug = "lal-lev-vil-2025-12-14"
market_info = get_market_by_slug(slug)

if market_info:
    trimmed = extract_questions_outcomes_token_ids(market_info)
    print(json.dumps(trimmed, indent=4))
else:
    print(f"Could not retrieve market for slug: {slug}")
