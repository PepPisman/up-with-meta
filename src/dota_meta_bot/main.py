import time
import requests

URL = "https://api.opendota.com/api/heroStats"


def fetch_hero_stats(timeout=(3, 10)):
    try:
        response = requests.get(URL, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        print(f"Fetch failed: {type(err).__name__}: {err}")
        return None


def main():
    data = fetch_hero_stats()
    print(data)

if __name__ == "__main__":
    main()