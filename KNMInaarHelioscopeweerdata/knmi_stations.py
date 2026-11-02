import io
import os
import requests
import pandas as pd
from dotenv import load_dotenv


# ============================================================
# INSTELLINGEN
# ============================================================
load_dotenv()

KNMI_API_KEY = os.getenv("KNMI_API_KEY")

DATASET = "waarneemstations_csv"
VERSION = "1.0"

BASE_URL = "https://api.dataplatform.knmi.nl/open-data/v1"

FILES_URL = (
    f"{BASE_URL}/datasets/"
    f"{DATASET}/versions/{VERSION}/files"
)

REQUEST_TIMEOUT = 30


# ============================================================
# API KEY
# ============================================================

def get_api_key():
    """
    Leest de KNMI API-key uit een environment variable.

    Windows CMD:
        set KNMI_API_KEY=jouw_key

    PowerShell:
        $env:KNMI_API_KEY="jouw_key"

    .env wordt hier bewust niet automatisch geladen.
    """

    api_key = os.getenv("KNMI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Environment variable KNMI_API_KEY is niet ingesteld."
        )

    return api_key


# ============================================================
# HEADERS
# ============================================================

def get_headers():
    """
    Headers voor de KNMI Open Data API.
    """

    return {
        "Authorization": get_api_key(),
        "Accept": "application/json",
    }


# ============================================================
# BESCHIKBARE BESTANDEN OPHALEN
# ============================================================

def get_station_files():
    """
    Haalt de bestanden op die KNMI voor waarneemstations_csv
    beschikbaar stelt.
    """

    response = requests.get(
        FILES_URL,
        headers=get_headers(),
        params={
            "maxKeys": 100,
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    files = data.get("files", [])

    if not files:
        raise RuntimeError(
            "KNMI retourneerde geen bestanden voor "
            f"{DATASET}/{VERSION}.\n\n"
            f"Response:\n{data}"
        )

    return files


# ============================================================
# BESTANDSNAAM BEPALEN
# ============================================================

def get_latest_station_file():
    """
    Selecteert een bestand uit de response.

    Bij meerdere bestanden wordt de laatste entry gebruikt.
    """

    files = get_station_files()

    print()
    print("Beschikbare KNMI-stationbestanden:")
    print("---------------------------------")

    for index, file_info in enumerate(files, start=1):

        filename = file_info.get("filename", "onbekend")

        print(
            f"{index:>2}. {filename}"
        )

    # Laatste bestand uit de lijst
    selected_file = files[-1]

    filename = selected_file.get("filename")

    if not filename:
        raise RuntimeError(
            "KNMI-response bevat geen filename."
        )

    print()
    print("Geselecteerd bestand:")
    print(filename)

    return filename


# ============================================================
# DOWNLOAD-URL OPVRAGEN
# ============================================================

def get_download_url(filename):
    """
    Vraagt bij KNMI een tijdelijke download-URL voor één bestand aan.
    """

    file_url = (
        f"{BASE_URL}/datasets/"
        f"{DATASET}/versions/{VERSION}/files/"
        f"{filename}/url"
    )

    response = requests.get(
        file_url,
        headers=get_headers(),
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    download_url = data.get("temporaryDownloadUrl")

    if not download_url:
        raise RuntimeError(
            "KNMI retourneerde geen temporaryDownloadUrl.\n\n"
            f"Response:\n{data}"
        )

    return download_url


# ============================================================
# CSV DOWNLOADEN
# ============================================================

def download_station_csv(filename):
    """
    Downloadt het geselecteerde stationsbestand.
    """

    download_url = get_download_url(filename)

    response = requests.get(
        download_url,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            "Gedownload KNMI-stationsbestand is leeg."
        )

    return response.content


# ============================================================
# CSV INLEZEN
# ============================================================

def read_station_csv(csv_content):
    """
    Probeert het KNMI CSV-bestand als pandas DataFrame in te lezen.
    """

    text = csv_content.decode(
        "utf-8-sig",
        errors="replace",
    )

    # Python/pandas probeert het scheidingsteken te detecteren.
    dataframe = pd.read_csv(
        io.StringIO(text),
        sep=None,
        engine="python",
    )

    # Eventuele spaties rond kolomnamen verwijderen
    dataframe.columns = [
        str(column).strip()
        for column in dataframe.columns
    ]

    return dataframe


# ============================================================
# COMPLETE STATIONLIJST
# ============================================================

def get_knmi_stations():
    """
    Hoofdfunctie.

    Geeft de KNMI-stationlijst terug als pandas DataFrame.
    """

    print("KNMI-stationlijst ophalen...")
    print()

    filename = get_latest_station_file()

    print()
    print("Download-URL aanvragen...")

    csv_content = download_station_csv(
        filename
    )

    print("CSV gedownload.")

    dataframe = read_station_csv(
        csv_content
    )

    print()
    print(
        f"Aantal stations/records: "
        f"{len(dataframe)}"
    )

    print()
    print("Kolommen:")
    print(list(dataframe.columns))

    return dataframe


# ============================================================
# OPSLAAN
# ============================================================

def save_station_list(dataframe):
    """
    Slaat de stationlijst lokaal op naast het script.
    """

    if getattr(__import__("sys"), "frozen", False):
        import sys

        directory = os.path.dirname(
            sys.executable
        )

    else:
        directory = os.path.dirname(
            os.path.abspath(__file__)
        )

    output_path = os.path.join(
        directory,
        "knmi_stations.csv",
    )

    dataframe.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
    )

    return output_path


# ============================================================
# START
# ============================================================

def main():

    stations = get_knmi_stations()

    print()
    print("Eerste records:")
    print("---------------")

    print(
        stations.head(10).to_string(
            index=False
        )
    )

    output_path = save_station_list(
        stations
    )

    print()
    print("Stationlijst opgeslagen:")
    print(output_path)


if __name__ == "__main__":

    try:

        main()

    except requests.HTTPError as error:

        print()
        print("KNMI API HTTP-fout:")
        print(error)

        if error.response is not None:

            print()
            print(
                "Statuscode:",
                error.response.status_code,
            )

            print()
            print(
                error.response.text[:1000]
            )

    except Exception as error:

        print()
        print("FOUT:")
        print(error)