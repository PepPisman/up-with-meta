import requests


def get_data_from_dota():
    try:
        response = requests.get('https://api.opendota.com/api/heroStats', timeout=1)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as err:
        print("Something went wrong while fetching data. Please try again.")
        return None


def main():
    data = get_data_from_dota()
    if data is not None:
        print(type(data))
        print(len(data))
        print(data[0])

if __name__ == "__main__":
    main()