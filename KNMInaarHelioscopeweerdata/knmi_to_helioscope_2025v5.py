import io
import os
import re
import sys
from math import asin, cos, radians, sin, sqrt

import numpy as np
import pandas as pd
import pvlib
import requests
import tkinter as tk
import tkintermapview

from tkinter import messagebox


# ============================================================
# INSTELLINGEN
# ============================================================

KNMI_HOURLY_URL = (
    "https://www.daggegevens.knmi.nl/"
    "klimatologie/uurgegevens"
)

TILE_SERVER = (
    "https://a.tile.openstreetmap.fr/hot/"
    "{z}/{x}/{y}.png"
)

STATIONS_FILE = "knmi_stations.csv"
TEMPLATE_FILE = "tmy3_sample.csv"

DEFAULT_YEAR = 2025

# KNMI-uurgegevens worden als UTC verwerkt.
TZ = "UTC"

REQUEST_TIMEOUT = 120

MAIN_VARIABLES = "Q:T"
WIND_VARIABLES = "FH:FF"

# Minimaal aandeel geldige waarden in de testperiode.
MIN_VALID_RATIO = 0.95

# Maximaal aantal stations dat automatisch wordt getest.
MAX_STATIONS_TO_TEST = 30

# Testperiode om te bepalen of variabelen beschikbaar zijn.
TEST_MONTH = 6
TEST_DAYS = 14


# ============================================================
# BESTANDSPADEN
# ============================================================

def get_script_directory():
    """
    Geeft de map van het Python-script of de PyInstaller-exe.
    """

    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)

    return os.path.dirname(
        os.path.abspath(__file__)
    )


def get_local_path(filename):
    """
    Maakt een volledig pad naast het script of de exe.
    """

    return os.path.join(
        get_script_directory(),
        filename,
    )


# ============================================================
# ALGEMENE HULPFUNCTIES
# ============================================================

def slugify(value):
    """
    Maakt tekst geschikt voor een bestandsnaam.
    """

    value = value.strip().replace(" ", "_")

    value = re.sub(
        r"[^A-Za-z0-9_-]",
        "",
        value,
    )

    return value or "project"


def parse_integer(value, default):
    """
    Geeft een ingevoerd geheel getal of de standaardwaarde.
    """

    value = value.strip()

    if not value:
        return default

    return int(value)


# ============================================================
# KAART
# ============================================================

def select_location_on_map():
    """
    Opent een kaart en geeft de aangeklikte latitude/longitude terug.
    """

    selection = {
        "latitude": None,
        "longitude": None,
        "confirmed": False,
    }

    marker_holder = {
        "marker": None,
    }

    root = tk.Tk()

    root.title(
        "Selecteer projectlocatie"
    )

    root.geometry("550x450")
    root.minsize(500, 400)

    title_label = tk.Label(
        root,
        text="Klik op de projectlocatie",
        font=("Arial", 16, "bold"),
    )

    title_label.pack(
        pady=(12, 3)
    )

    instruction_label = tk.Label(
        root,
        text=(
            "Zoom in en klik zo nauwkeurig mogelijk "
            "op de gewenste locatie."
        ),
        font=("Arial", 10),
    )

    instruction_label.pack(
        pady=(0, 8)
    )

    coordinate_label = tk.Label(
        root,
        text="Latitude: -     Longitude: -",
        font=("Consolas", 11),
    )

    coordinate_label.pack(
        pady=4
    )

    map_widget = tkintermapview.TkinterMapView(
        root,
        corner_radius=0,
    )

    map_widget.set_tile_server(
        TILE_SERVER,
        max_zoom=11,
    )

    map_widget.pack(
        fill="both",
        expand=True,
        padx=12,
        pady=10,
    )

    # Midden van Nederland.
    map_widget.set_position(
        52.1326,
        5.2913,
    )

    map_widget.set_zoom(9)

    def map_clicked(coordinates):
        latitude, longitude = coordinates

        selection["latitude"] = float(latitude)
        selection["longitude"] = float(longitude)

        if marker_holder["marker"] is not None:
            marker_holder["marker"].delete()

        marker_holder["marker"] = map_widget.set_marker(
            latitude,
            longitude,
            text="Projectlocatie",
        )

        coordinate_label.config(
            text=(
                f"Latitude: {latitude:.6f}     "
                f"Longitude: {longitude:.6f}"
            )
        )

    map_widget.add_left_click_map_command(
        map_clicked
    )

    def confirm_location():
        if selection["latitude"] is None:
            messagebox.showwarning(
                "Geen locatie geselecteerd",
                "Klik eerst op een locatie op de kaart.",
                parent=root,
            )
            return

        selection["confirmed"] = True
        root.quit()

    def cancel_location():
        selection["confirmed"] = False
        root.quit()

    button_frame = tk.Frame(root)

    button_frame.pack(
        pady=(0, 12)
    )

    confirm_button = tk.Button(
        button_frame,
        text="Gebruik deze locatie",
        command=confirm_location,
        font=("Arial", 11, "bold"),
        padx=20,
        pady=8,
    )

    confirm_button.pack(
        side="left",
        padx=8,
    )

    cancel_button = tk.Button(
        button_frame,
        text="Annuleren",
        command=cancel_location,
        font=("Arial", 11),
        padx=20,
        pady=8,
    )

    cancel_button.pack(
        side="left",
        padx=8,
    )

    root.protocol(
        "WM_DELETE_WINDOW",
        cancel_location,
    )

    root.mainloop()

    # Openstaande callbacks van tkintermapview annuleren.
    try:
        after_ids = root.tk.call(
            "after",
            "info",
        )

        for after_id in after_ids:
            try:
                root.after_cancel(after_id)
            except Exception:
                pass

    except Exception:
        pass

    try:
        map_widget.destroy()
    except Exception:
        pass

    try:
        root.destroy()
    except Exception:
        pass

    if not selection["confirmed"]:
        raise RuntimeError(
            "Geen projectlocatie geselecteerd."
        )

    return (
        selection["latitude"],
        selection["longitude"],
    )


# ============================================================
# STATIONLIJST LADEN
# ============================================================

def load_station_list():
    """
    Leest knmi_stations.csv.

    Vereiste kolommen:
    STN, STARTT, STOPT, LOCATIE, TYPE, HOOGTE, POS_NB, POS_OL.
    """

    path = get_local_path(
        STATIONS_FILE
    )

    print()
    print("Stationsbestand:")
    print(path)

    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Stationsbestand niet gevonden:\n{path}"
        )

    stations = pd.read_csv(
        path,
        sep=",",
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )

    stations.columns = [
        str(column).strip()
        for column in stations.columns
    ]

    required_columns = [
        "STN",
        "STARTT",
        "STOPT",
        "LOCATIE",
        "TYPE",
        "HOOGTE",
        "POS_NB",
        "POS_OL",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in stations.columns
    ]

    if missing_columns:
        raise ValueError(
            "Stationsbestand mist kolommen:\n"
            + ", ".join(missing_columns)
            + "\n\nGevonden kolommen:\n"
            + ", ".join(stations.columns)
        )

    text_columns = [
        "STARTT",
        "STOPT",
        "LOCATIE",
        "TYPE",
    ]

    for column in text_columns:
        stations[column] = (
            stations[column]
            .astype(str)
            .str.strip()
        )

    numeric_columns = [
        "STN",
        "HOOGTE",
        "POS_NB",
        "POS_OL",
    ]

    for column in numeric_columns:
        stations[column] = pd.to_numeric(
            stations[column],
            errors="coerce",
        )

    stations = stations.dropna(
        subset=[
            "STN",
            "POS_NB",
            "POS_OL",
        ]
    ).copy()

    stations["STN"] = (
        stations["STN"].astype(int)
    )

    stations = stations.reset_index(
        drop=True
    )

    print(
        f"Aantal ingelezen stations: {len(stations)}"
    )

    return stations


# ============================================================
# ACTIEVE STATIONS
# ============================================================

def filter_stations_active_in_year(
    stations,
    year,
):
    """
    Houdt stations over die in het gekozen jaar actief waren.
    """

    result = stations.copy()

    result["START_YEAR"] = pd.to_numeric(
        result["STARTT"].str[:4],
        errors="coerce",
    )

    result["STOP_YEAR"] = pd.to_numeric(
        result["STOPT"].str[:4],
        errors="coerce",
    )

    mask = (
        result["START_YEAR"].notna()
        & result["STOP_YEAR"].notna()
        & (result["START_YEAR"] <= year)
        & (result["STOP_YEAR"] >= year)
    )

    active = (
        result.loc[mask]
        .copy()
        .reset_index(drop=True)
    )

    print(
        f"Stations actief in {year}: {len(active)}"
    )

    return active


# ============================================================
# AFSTANDSBEREKENING
# ============================================================

def haversine_distance_km(
    latitude_1,
    longitude_1,
    latitude_2,
    longitude_2,
):
    """
    Berekent de afstand over het aardoppervlak in kilometer.
    """

    earth_radius_km = 6371.0088

    latitude_1 = radians(latitude_1)
    longitude_1 = radians(longitude_1)

    latitude_2 = radians(latitude_2)
    longitude_2 = radians(longitude_2)

    latitude_difference = (
        latitude_2 - latitude_1
    )

    longitude_difference = (
        longitude_2 - longitude_1
    )

    value = (
        sin(latitude_difference / 2.0) ** 2
        + cos(latitude_1)
        * cos(latitude_2)
        * sin(longitude_difference / 2.0) ** 2
    )

    value = min(
        1.0,
        max(0.0, value),
    )

    angular_distance = (
        2.0 * asin(sqrt(value))
    )

    return (
        earth_radius_km
        * angular_distance
    )


def add_station_distances(
    stations,
    project_latitude,
    project_longitude,
):
    """
    Voegt AFSTAND_KM toe en sorteert van dichtbij naar ver.
    """

    result = stations.copy()

    result["AFSTAND_KM"] = result.apply(
        lambda row: haversine_distance_km(
            latitude_1=project_latitude,
            longitude_1=project_longitude,
            latitude_2=float(row["POS_NB"]),
            longitude_2=float(row["POS_OL"]),
        ),
        axis=1,
    )

    return (
        result.sort_values(
            "AFSTAND_KM"
        )
        .reset_index(drop=True)
    )


# ============================================================
# KNMI-DOWNLOAD
# ============================================================

def download_knmi_period(
    station,
    variables,
    start,
    end,
):
    """
    Downloadt KNMI-uurgegevens voor een bepaalde periode.

    start en end hebben formaat YYYYMMDDHH.
    """

    payload = {
        "start": start,
        "end": end,
        "stns": str(station),
        "vars": variables,
        "fmt": "csv",
    }

    response = requests.post(
        KNMI_HOURLY_URL,
        data=payload,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    if not response.text.strip():
        raise ValueError(
            f"Lege KNMI-response voor station {station}."
        )

    return response.text


def download_knmi_hourly(
    station,
    year,
    variables,
):
    """
    Downloadt een volledig kalenderjaar.
    """

    return download_knmi_period(
        station=station,
        variables=variables,
        start=f"{year}010101",
        end=f"{year}123124",
    )


# ============================================================
# KNMI PARSEN
# ============================================================

def parse_knmi(
    text,
    expected_variables,
):
    """
    Leest de CSV-response van de KNMI-uurgegevens.
    """

    lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        lines.append(line)

    if not lines:
        raise ValueError(
            "Geen KNMI-dataregels gevonden."
        )

    raw_data = "\n".join(lines)

    expected_columns = [
        "STN",
        "YYYYMMDD",
        "HH",
        *expected_variables.split(":"),
    ]

    first_line = lines[0]

    has_header = any(
        character.isalpha()
        for character in first_line
    )

    if has_header:
        dataframe = pd.read_csv(
            io.StringIO(raw_data),
            sep=",",
            skipinitialspace=True,
        )

        dataframe.columns = [
            str(column).strip()
            for column in dataframe.columns
        ]

    else:
        dataframe = pd.read_csv(
            io.StringIO(raw_data),
            sep=",",
            header=None,
            skipinitialspace=True,
        )

        if dataframe.shape[1] != len(
            expected_columns
        ):
            raise ValueError(
                "Onverwacht aantal KNMI-kolommen. "
                f"Ontvangen: {dataframe.shape[1]}, "
                f"verwacht: {len(expected_columns)}."
            )

        dataframe.columns = expected_columns

    for column in dataframe.columns:
        dataframe[column] = pd.to_numeric(
            dataframe[column],
            errors="coerce",
        )

    required_columns = [
        "STN",
        "YYYYMMDD",
        "HH",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing_columns:
        raise ValueError(
            "KNMI-response mist kolommen: "
            + ", ".join(missing_columns)
        )

    dataframe = dataframe.dropna(
        subset=[
            "STN",
            "YYYYMMDD",
            "HH",
        ]
    ).copy()

    dataframe["STN"] = (
        dataframe["STN"].astype(int)
    )

    dataframe["YYYYMMDD"] = (
        dataframe["YYYYMMDD"].astype(int)
    )

    dataframe["HH"] = (
        dataframe["HH"].astype(int)
    )

    return dataframe


def clean_knmi_column(series):
    """
    Maakt een KNMI-kolom numeriek en verwijdert placeholders.
    """

    numeric = pd.to_numeric(
        series,
        errors="coerce",
    )

    numeric = numeric.replace(
        {
            77777: np.nan,
            7777.7: np.nan,
        }
    )

    return numeric


# ============================================================
# TESTPERIODE
# ============================================================

def get_test_period(year):
    """
    Gebruikt de eerste TEST_DAYS dagen van juni.
    """

    start_date = pd.Timestamp(
        year=year,
        month=TEST_MONTH,
        day=1,
    )

    end_date = (
        start_date
        + pd.Timedelta(days=TEST_DAYS - 1)
    )

    return (
        start_date.strftime("%Y%m%d01"),
        end_date.strftime("%Y%m%d24"),
    )


# ============================================================
# STATION TESTEN OP Q EN T
# ============================================================

def test_station_variables(
    station,
    year,
    variables,
):
    """
    Test of gevraagde variabelen voldoende geldige data bevatten.
    """

    start, end = get_test_period(
        year
    )

    try:
        text = download_knmi_period(
            station=station,
            variables=variables,
            start=start,
            end=end,
        )

        dataframe = parse_knmi(
            text,
            variables,
        )

        if dataframe.empty:
            return False, {}

        ratios = {}

        for variable in variables.split(":"):
            if variable not in dataframe.columns:
                ratios[variable] = 0.0
                continue

            cleaned = clean_knmi_column(
                dataframe[variable]
            )

            ratios[variable] = float(
                cleaned.notna().mean()
            )

        suitable = all(
            ratio >= MIN_VALID_RATIO
            for ratio in ratios.values()
        )

        return suitable, ratios

    except Exception as error:
        return False, {
            "error": str(error),
        }


# ============================================================
# WINDSTATION TESTEN
# ============================================================

def test_wind_station(
    station,
    year,
):
    """
    Test of FH of FF voldoende geldige windwaarden oplevert.
    """

    start, end = get_test_period(
        year
    )

    try:
        text = download_knmi_period(
            station=station,
            variables=WIND_VARIABLES,
            start=start,
            end=end,
        )

        dataframe = parse_knmi(
            text,
            WIND_VARIABLES,
        )

        if dataframe.empty:
            return False, {}

        if "FH" in dataframe.columns:
            fh = clean_knmi_column(
                dataframe["FH"]
            )
        else:
            fh = pd.Series(
                np.nan,
                index=dataframe.index,
                dtype=float,
            )

        if "FF" in dataframe.columns:
            ff = clean_knmi_column(
                dataframe["FF"]
            )
        else:
            ff = pd.Series(
                np.nan,
                index=dataframe.index,
                dtype=float,
            )

        combined = fh.combine_first(ff)

        ratio = float(
            combined.notna().mean()
        )

        return (
            ratio >= MIN_VALID_RATIO,
            {
                "FH/FF": ratio,
            },
        )

    except Exception as error:
        return False, {
            "error": str(error),
        }


# ============================================================
# AUTOMATISCH TWEE STATIONS ZOEKEN
# ============================================================

def find_two_suitable_stations(
    stations_by_distance,
    year,
    purpose,
):
    """
    Test stations op afstand en stopt zodra twee geschikte
    stations zijn gevonden.
    """

    suitable_stations = []

    print()
    print(
        f"Zoeken naar stations voor {purpose}..."
    )
    print("-" * 86)

    candidates = stations_by_distance.head(
        MAX_STATIONS_TO_TEST
    )

    for _, station_row in candidates.iterrows():
        station_number = int(
            station_row["STN"]
        )

        station_name = str(
            station_row["LOCATIE"]
        )

        distance_km = float(
            station_row["AFSTAND_KM"]
        )

        if purpose == "zon en temperatuur":
            suitable, ratios = test_station_variables(
                station=station_number,
                year=year,
                variables=MAIN_VARIABLES,
            )

        elif purpose == "wind":
            suitable, ratios = test_wind_station(
                station=station_number,
                year=year,
            )

        else:
            raise ValueError(
                f"Onbekend doel: {purpose}"
            )

        if "error" in ratios:
            status = (
                "fout: "
                + str(ratios["error"])[:100]
            )

        else:
            ratio_text = ", ".join(
                f"{name}={ratio:.1%}"
                for name, ratio in ratios.items()
            )

            status = (
                f"geschikt ({ratio_text})"
                if suitable
                else f"ongeschikt ({ratio_text})"
            )

        print(
            f"{station_number:>3} | "
            f"{station_name:<30.30} | "
            f"{distance_km:>7.1f} km | "
            f"{status}"
        )

        if suitable:
            suitable_stations.append(
                station_row.copy()
            )

        if len(suitable_stations) == 2:
            break

    if not suitable_stations:
        raise ValueError(
            f"Geen geschikt station voor {purpose} gevonden."
        )

    primary = suitable_stations[0]

    backup = (
        suitable_stations[1]
        if len(suitable_stations) > 1
        else None
    )

    return primary, backup


def print_station_selection(
    title,
    primary,
    backup,
):
    """
    Toont primair station en back-up.
    """

    print()
    print(title)
    print("-" * len(title))

    print(
        "Eerste keuze: "
        f"{int(primary['STN'])} - "
        f"{primary['LOCATIE']} "
        f"({float(primary['AFSTAND_KM']):.1f} km)"
    )

    if backup is not None:
        print(
            "Back-up     : "
            f"{int(backup['STN'])} - "
            f"{backup['LOCATIE']} "
            f"({float(backup['AFSTAND_KM']):.1f} km)"
        )
    else:
        print(
            "Back-up     : niet gevonden"
        )


# ============================================================
# STATION OP NUMMER OPZOEKEN
# ============================================================

def get_station_by_number(
    stations_by_distance,
    station_number,
):
    """
    Zoekt een station in de actieve stationslijst.
    """

    match = stations_by_distance.loc[
        stations_by_distance["STN"]
        == station_number
    ]

    if match.empty:
        raise ValueError(
            f"Station {station_number} staat niet in "
            "de actieve KNMI-stationslijst."
        )

    return match.iloc[0].copy()


# ============================================================
# HANDMATIGE KEUZE ZON/TEMPERATUUR
# ============================================================

def ask_manual_main_station(
    stations_by_distance,
    year,
):
    """
    Vraagt en valideert een handmatig Q/T-station.
    """

    print()
    print(
        "Handmatige keuze voor zon en temperatuur"
    )
    print("----------------------------------------")

    while True:
        station_input = input(
            "Stationnummer voor zon/temperatuur: "
        ).strip()

        if station_input.lower() in {
            "stop",
            "annuleren",
            "cancel",
        }:
            raise RuntimeError(
                "Stationselectie geannuleerd."
            )

        try:
            station_number = int(
                station_input
            )

        except ValueError:
            print(
                "Voer een geldig numeriek stationnummer in."
            )
            continue

        try:
            station_row = get_station_by_number(
                stations_by_distance,
                station_number,
            )

        except ValueError as error:
            print(error)
            continue

        print(
            f"Gevonden: {station_number} - "
            f"{station_row['LOCATIE']} "
            f"({float(station_row['AFSTAND_KM']):.1f} km)"
        )

        print(
            "Q- en T-beschikbaarheid controleren..."
        )

        suitable, ratios = test_station_variables(
            station=station_number,
            year=year,
            variables=MAIN_VARIABLES,
        )

        if "error" in ratios:
            print(
                "Controle mislukt:",
                ratios["error"],
            )
            continue

        ratio_text = ", ".join(
            f"{name}={ratio:.1%}"
            for name, ratio in ratios.items()
        )

        if not suitable:
            print(
                f"Station is niet geschikt: {ratio_text}"
            )
            continue

        print(
            f"Station is geschikt: {ratio_text}"
        )

        return station_row


# ============================================================
# HANDMATIGE KEUZE WIND
# ============================================================

def ask_manual_wind_station(
    stations_by_distance,
    year,
):
    """
    Vraagt en valideert een handmatig windstation.
    """

    print()
    print("Handmatige keuze voor wind")
    print("---------------------------")

    while True:
        station_input = input(
            "Stationnummer voor wind: "
        ).strip()

        if station_input.lower() in {
            "stop",
            "annuleren",
            "cancel",
        }:
            raise RuntimeError(
                "Stationselectie geannuleerd."
            )

        try:
            station_number = int(
                station_input
            )

        except ValueError:
            print(
                "Voer een geldig numeriek stationnummer in."
            )
            continue

        try:
            station_row = get_station_by_number(
                stations_by_distance,
                station_number,
            )

        except ValueError as error:
            print(error)
            continue

        print(
            f"Gevonden: {station_number} - "
            f"{station_row['LOCATIE']} "
            f"({float(station_row['AFSTAND_KM']):.1f} km)"
        )

        print(
            "FH- en FF-beschikbaarheid controleren..."
        )

        suitable, ratios = test_wind_station(
            station=station_number,
            year=year,
        )

        if "error" in ratios:
            print(
                "Controle mislukt:",
                ratios["error"],
            )
            continue

        ratio_text = ", ".join(
            f"{name}={ratio:.1%}"
            for name, ratio in ratios.items()
        )

        if not suitable:
            print(
                f"Station is niet geschikt: {ratio_text}"
            )
            continue

        print(
            f"Station is geschikt: {ratio_text}"
        )

        return station_row


# ============================================================
# AUTOMATISCHE OF HANDMATIGE KEUZE
# ============================================================

def choose_stations(
    stations_by_distance,
    year,
    main_primary,
    main_backup,
    wind_primary,
    wind_backup,
):
    """
    Laat de automatische voorstellen accepteren of handmatig
    andere stations kiezen.
    """

    while True:
        answer = input(
            "\nDeze voorgestelde stations gebruiken? "
            "[Enter=ja / n=eigen invoer]: "
        ).strip().lower()

        if answer in {
            "",
            "j",
            "ja",
            "y",
            "yes",
        }:
            print()
            print(
                "De voorgestelde stations worden gebruikt."
            )

            return (
                main_primary,
                main_backup,
                wind_primary,
                wind_backup,
            )

        if answer in {
            "n",
            "nee",
            "no",
        }:
            print()
            print(
                "Je kunt nu zelf de stations invoeren."
            )

            manual_main = ask_manual_main_station(
                stations_by_distance,
                year,
            )

            manual_wind = ask_manual_wind_station(
                stations_by_distance,
                year,
            )

            return (
                manual_main,
                main_backup,
                manual_wind,
                wind_backup,
            )

        print(
            "Ongeldige keuze. Druk op Enter voor ja "
            "of typ n voor eigen invoer."
        )


# ============================================================
# INVOER EN STATIONSELECTIE
# ============================================================

def ask_inputs():
    """
    Verzamelt projectgegevens en bepaalt de weerstations.
    """

    print("=== KNMI naar HelioScope TMY3 ===")
    print()
    print(
        "Selecteer de projectlocatie op de kaart."
    )

    latitude, longitude = (
        select_location_on_map()
    )

    print()
    print("Geselecteerde locatie:")
    print(f"Latitude : {latitude:.6f}")
    print(f"Longitude: {longitude:.6f}")
    print()

    project_name = input(
        "Projectnaam (standaard Project): "
    ).strip()

    if not project_name:
        project_name = "Project"

    year = parse_integer(
        input(
            f"Jaar (standaard {DEFAULT_YEAR}): "
        ),
        DEFAULT_YEAR,
    )

    print()
    print("KNMI-stationlijst laden...")

    stations = load_station_list()

    active_stations = filter_stations_active_in_year(
        stations,
        year,
    )

    if active_stations.empty:
        raise ValueError(
            f"Geen actieve stations gevonden voor {year}."
        )

    stations_by_distance = add_station_distances(
        active_stations,
        project_latitude=latitude,
        project_longitude=longitude,
    )

    print()
    print("Tien dichtstbijzijnde stations:")
    print("--------------------------------")

    print(
        stations_by_distance[
            [
                "STN",
                "LOCATIE",
                "TYPE",
                "AFSTAND_KM",
            ]
        ]
        .head(10)
        .to_string(
            index=False,
            formatters={
                "AFSTAND_KM": (
                    lambda value: f"{value:.1f}"
                )
            },
        )
    )

    main_primary, main_backup = (
        find_two_suitable_stations(
            stations_by_distance,
            year,
            purpose="zon en temperatuur",
        )
    )

    print_station_selection(
        "Stations voor zon en temperatuur",
        main_primary,
        main_backup,
    )

    wind_primary, wind_backup = (
        find_two_suitable_stations(
            stations_by_distance,
            year,
            purpose="wind",
        )
    )

    print_station_selection(
        "Stations voor wind",
        wind_primary,
        wind_backup,
    )

    (
        main_primary,
        main_backup,
        wind_primary,
        wind_backup,
    ) = choose_stations(
        stations_by_distance,
        year,
        main_primary,
        main_backup,
        wind_primary,
        wind_backup,
    )

    main_backup_number = (
        int(main_backup["STN"])
        if main_backup is not None
        else None
    )

    wind_backup_number = (
        int(wind_backup["STN"])
        if wind_backup is not None
        else None
    )

    if pd.notna(main_primary["HOOGTE"]):
        elevation = float(
            main_primary["HOOGTE"]
        )
    else:
        elevation = 0.0

    print()
    print("Definitieve stationkeuze:")

    print(
        f"Zon/temperatuur: "
        f"{int(main_primary['STN'])} - "
        f"{main_primary['LOCATIE']} "
        f"({float(main_primary['AFSTAND_KM']):.1f} km)"
    )

    print(
        f"Wind          : "
        f"{int(wind_primary['STN'])} - "
        f"{wind_primary['LOCATIE']} "
        f"({float(wind_primary['AFSTAND_KM']):.1f} km)"
    )

    return {
        "project_name": project_name,
        "year": year,
        "latitude": latitude,
        "longitude": longitude,
        "elevation": elevation,

        "main_station": int(
            main_primary["STN"]
        ),
        "main_station_name": str(
            main_primary["LOCATIE"]
        ),
        "main_distance_km": float(
            main_primary["AFSTAND_KM"]
        ),
        "main_backup_station": (
            main_backup_number
        ),

        "wind_station": int(
            wind_primary["STN"]
        ),
        "wind_station_name": str(
            wind_primary["LOCATIE"]
        ),
        "wind_distance_km": float(
            wind_primary["AFSTAND_KM"]
        ),
        "wind_backup_station": (
            wind_backup_number
        ),
    }


# ============================================================
# HOOFDSTATION VOORBEREIDEN
# ============================================================

def prepare_main_station(dataframe):
    """
    Converteert Q naar GHI en T naar DryBulb.

    KNMI Q is globale straling in J/cm² gedurende het uurvak.
    De conversie naar W/m² wordt:

        Q * 10.000 / 3.600
    """

    result = dataframe.copy()

    if "Q" not in result.columns:
        result["Q"] = np.nan

    if "T" not in result.columns:
        result["T"] = np.nan

    result["Q"] = clean_knmi_column(
        result["Q"]
    )

    result["T"] = clean_knmi_column(
        result["T"]
    )

    result["GHI"] = (
        result["Q"]
        * 10000.0
        / 3600.0
    ).clip(lower=0)

    result["DryBulb"] = (
        result["T"] / 10.0
    )

    return result


# ============================================================
# WINDSTATION VOORBEREIDEN
# ============================================================

def prepare_wind_station(dataframe):
    """
    Converteert FH/FF naar windsnelheid in m/s.
    """

    result = dataframe.copy()

    if "FH" not in result.columns:
        result["FH"] = np.nan

    if "FF" not in result.columns:
        result["FF"] = np.nan

    result["FH"] = clean_knmi_column(
        result["FH"]
    )

    result["FF"] = clean_knmi_column(
        result["FF"]
    )

    result["Wspd"] = (
        result["FH"]
        .combine_first(result["FF"])
        / 10.0
    )

    return result[
        [
            "YYYYMMDD",
            "HH",
            "Wspd",
        ]
    ].copy()


# ============================================================
# SAMENVOEGEN
# ============================================================

def merge_main_and_wind(
    main_data,
    wind_data,
):
    """
    Voegt hoofd- en windstation samen op datum en uur.
    """

    if main_data.duplicated(
        ["YYYYMMDD", "HH"]
    ).any():
        raise ValueError(
            "Hoofdstation bevat dubbele uurrecords."
        )

    if wind_data.duplicated(
        ["YYYYMMDD", "HH"]
    ).any():
        raise ValueError(
            "Windstation bevat dubbele uurrecords."
        )

    return main_data.merge(
        wind_data,
        on=["YYYYMMDD", "HH"],
        how="left",
        validate="one_to_one",
    )


# ============================================================
# TIJDINDEXEN
# ============================================================

def build_times(dataframe):
    """
    Maakt twee tijdassen uit KNMI-uurgegevens.

    KNMI HH is het einde van het uurvak:

        HH=1  -> 00:00 tot 01:00
        HH=2  -> 01:00 tot 02:00
        ...
        HH=24 -> 23:00 tot 24:00

    midpoint_times:
        Midden van ieder uurvak.
        Wordt gebruikt voor de zonnepositie en Erbs-decompositie.

    hour_beginning_times:
        Begin van ieder uurvak.
        Wordt geschreven naar het HelioScope TMY3-bestand.
    """

    dates = (
        dataframe["YYYYMMDD"]
        .astype(int)
        .astype(str)
    )

    hours = (
        dataframe["HH"].astype(int)
    )

    if not hours.between(1, 24).all():
        raise ValueError(
            "KNMI HH bevat waarden buiten 1 t/m 24."
        )

    base = pd.to_datetime(
        dates,
        format="%Y%m%d",
        utc=True,
        errors="raise",
    )

    # Tijdstip aan het einde van ieder KNMI-uurvak.
    end_times = (
        base
        + pd.to_timedelta(
            hours,
            unit="h",
        )
    )

    # Representatief tijdstip voor zonnepositie.
    midpoint_times = (
        end_times
        - pd.Timedelta(minutes=30)
    )

    # Tijdstip dat het begin van het uurvak aangeeft.
    hour_beginning_times = (
        end_times
        - pd.Timedelta(hours=1)
    )

    midpoint_times = pd.DatetimeIndex(
        midpoint_times
    ).tz_convert(TZ)

    hour_beginning_times = pd.DatetimeIndex(
        hour_beginning_times
    ).tz_convert(TZ)

    return (
        midpoint_times,
        hour_beginning_times,
    )


def remove_february_29(
    dataframe,
    midpoint_times,
    hour_beginning_times,
):
    """
    Verwijdert alle uurvakken van 29 februari.

    Het filter gebruikt hour_beginning_times, omdat die tijdas
    bepaalt bij welke kalenderdag het uurvak hoort.
    """

    february_29 = (
        (hour_beginning_times.month == 2)
        & (hour_beginning_times.day == 29)
    )

    number_removed = int(
        february_29.sum()
    )

    if number_removed:
        print(
            f"Schrikkeljaar: {number_removed} uren "
            "van 29 februari verwijderd."
        )

        dataframe = (
            dataframe.loc[~february_29]
            .reset_index(drop=True)
        )

        midpoint_times = (
            midpoint_times[~february_29]
        )

        hour_beginning_times = (
            hour_beginning_times[~february_29]
        )

    return (
        dataframe,
        midpoint_times,
        hour_beginning_times,
    )


def validate_time_axes(
    midpoint_times,
    hour_beginning_times,
    expected_year,
):
    """
    Controleert dat beide tijdassen volledig en correct uitgelijnd zijn.
    """

    if len(midpoint_times) != 8760:
        raise ValueError(
            "Midpoint-tijdas bevat "
            f"{len(midpoint_times)} waarden; verwacht 8760."
        )

    if len(hour_beginning_times) != 8760:
        raise ValueError(
            "Hour-beginning-tijdas bevat "
            f"{len(hour_beginning_times)} waarden; verwacht 8760."
        )

    if hour_beginning_times.has_duplicates:
        raise ValueError(
            "Hour-beginning-tijdas bevat dubbele tijdstippen."
        )

    if midpoint_times.has_duplicates:
        raise ValueError(
            "Midpoint-tijdas bevat dubbele tijdstippen."
        )

    offsets = (
        midpoint_times
        - hour_beginning_times
    )

    expected_offset = pd.Timedelta(
        minutes=30
    )

    if not all(
        offset == expected_offset
        for offset in offsets
    ):
        raise ValueError(
            "Niet ieder midpoint ligt precies 30 minuten "
            "na het begin van het uurvak."
        )

    if not (
        hour_beginning_times.year
        == expected_year
    ).all():
        raise ValueError(
            "De hour-beginning-tijdas bevat waarden "
            f"buiten {expected_year}."
        )

    expected_first = pd.Timestamp(
        year=expected_year,
        month=1,
        day=1,
        hour=0,
        tz=TZ,
    )

    expected_last = pd.Timestamp(
        year=expected_year,
        month=12,
        day=31,
        hour=23,
        tz=TZ,
    )

    if hour_beginning_times[0] != expected_first:
        raise ValueError(
            "Eerste uurvak is niet "
            f"{expected_first} maar "
            f"{hour_beginning_times[0]}."
        )

    if hour_beginning_times[-1] != expected_last:
        raise ValueError(
            "Laatste uurvak is niet "
            f"{expected_last} maar "
            f"{hour_beginning_times[-1]}."
        )


# ============================================================
# DNI EN DHI
# ============================================================

def add_dni_dhi(
    ghi,
    times,
    latitude,
    longitude,
):
    """
    Leidt DNI en DHI af met het pvlib Erbs-model.

    times moet de midpoint-tijdas zijn.
    """

    location = pvlib.location.Location(
        latitude=latitude,
        longitude=longitude,
        tz=TZ,
    )

    solar_position = (
        location.get_solarposition(times)
    )

    decomposition = pvlib.irradiance.erbs(
        ghi=ghi,
        zenith=solar_position[
            "zenith"
        ].to_numpy(),
        datetime_or_doy=times,
    )

    result = pd.DataFrame(
        {
            "DNI": pd.to_numeric(
                decomposition["dni"],
                errors="coerce",
            ).to_numpy(),

            "DHI": pd.to_numeric(
                decomposition["dhi"],
                errors="coerce",
            ).to_numpy(),
        }
    )

    result["DNI"] = result["DNI"].clip(
        lower=0
    )

    result["DHI"] = result["DHI"].clip(
        lower=0
    )

    nighttime = ghi <= 0

    result.loc[
        nighttime,
        ["DNI", "DHI"],
    ] = 0.0

    return result


# ============================================================
# FYSIEKE DATA VALIDEREN
# ============================================================

def validate_physical_data(dataframe):
    """
    Controleert de fysieke kolommen voordat de TMY3 wordt geschreven.
    """

    if len(dataframe) != 8760:
        raise ValueError(
            f"Dataset bevat {len(dataframe)} regels; "
            "verwacht 8760."
        )

    required_columns = [
        "GHI",
        "DNI",
        "DHI",
        "DryBulb",
        "Wspd",
    ]

    for column in required_columns:
        if column not in dataframe.columns:
            raise ValueError(
                f"Kolom ontbreekt: {column}"
            )

        numeric = pd.to_numeric(
            dataframe[column],
            errors="coerce",
        )

        missing = int(
            numeric.isna().sum()
        )

        if missing:
            missing_rows = dataframe.loc[
                numeric.isna(),
                ["YYYYMMDD", "HH"],
            ].head(10)

            raise ValueError(
                f"{column} bevat {missing} lege waarden.\n"
                f"Eerste ontbrekende momenten:\n"
                f"{missing_rows.to_string(index=False)}"
            )

        if np.isinf(
            numeric.to_numpy()
        ).any():
            raise ValueError(
                f"{column} bevat oneindige waarden."
            )

        if (numeric == 77777).any():
            raise ValueError(
                f"{column} bevat nog 77777."
            )

    if (dataframe["GHI"] < 0).any():
        raise ValueError(
            "GHI bevat negatieve waarden."
        )

    if (dataframe["DNI"] < 0).any():
        raise ValueError(
            "DNI bevat negatieve waarden."
        )

    if (dataframe["DHI"] < 0).any():
        raise ValueError(
            "DHI bevat negatieve waarden."
        )

    if (dataframe["Wspd"] < 0).any():
        raise ValueError(
            "Windsnelheid bevat negatieve waarden."
        )


# ============================================================
# HELIOSCOPE-TEMPLATE LADEN
# ============================================================

def load_template():
    """
    Leest het HelioScope-TMY3-template.
    """

    path = get_local_path(
        TEMPLATE_FILE
    )

    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Template niet gevonden:\n{path}"
        )

    with open(
        path,
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        metadata_line = (
            file.readline().rstrip("\r\n")
        )

        header_line = (
            file.readline().rstrip("\r\n")
        )

        remaining_data = file.read()

    columns = [
        column.strip()
        for column in header_line.split(",")
    ]

    dataframe = pd.read_csv(
        io.StringIO(remaining_data),
        names=columns,
        header=None,
    )

    if len(dataframe) != 8760:
        raise ValueError(
            f"Template heeft {len(dataframe)} regels; "
            "verwacht 8760."
        )

    required_columns = [
        "Date (MM/DD/YYYY)",
        "Time (HH:MM)",
        "GHI (W/m^2)",
        "DNI (W/m^2)",
        "DHI (W/m^2)",
        "Dry-bulb (C)",
        "Wspd (m/s)",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing_columns:
        raise ValueError(
            "Template mist kolommen: "
            + ", ".join(missing_columns)
        )

    return (
        metadata_line,
        header_line,
        dataframe,
    )


# ============================================================
# TEMPLATE OPSLAAN
# ============================================================

def save_template(
    output_path,
    metadata_line,
    header_line,
    dataframe,
):
    """
    Schrijft de uiteindelijke CSV.
    """

    with open(
        output_path,
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        file.write(
            metadata_line + "\n"
        )

        file.write(
            header_line + "\n"
        )

        dataframe.to_csv(
            file,
            index=False,
            header=False,
            lineterminator="\n",
            na_rep="",
        )


# ============================================================
# UITVOER VALIDEREN
# ============================================================

def validate_output_file(
    output_path,
    expected_year,
):
    """
    Leest de geschreven CSV opnieuw in en controleert de uitvoer.
    """

    dataframe = pd.read_csv(
        output_path,
        skiprows=1,
    )

    if len(dataframe) != 8760:
        raise ValueError(
            f"Geschreven bestand bevat "
            f"{len(dataframe)} regels; verwacht 8760."
        )

    required_columns = [
        "Date (MM/DD/YYYY)",
        "Time (HH:MM)",
        "GHI (W/m^2)",
        "DNI (W/m^2)",
        "DHI (W/m^2)",
        "Dry-bulb (C)",
        "Wspd (m/s)",
    ]

    for column in required_columns:
        if column not in dataframe.columns:
            raise ValueError(
                f"Uitvoer mist kolom {column}."
            )

        if dataframe[column].isna().any():
            raise ValueError(
                f"Uitvoer bevat lege waarden in {column}."
            )

    duplicate_times = dataframe.duplicated(
        subset=[
            "Date (MM/DD/YYYY)",
            "Time (HH:MM)",
        ]
    )

    if duplicate_times.any():
        raise ValueError(
            "Uitvoer bevat dubbele tijdstippen."
        )

    output_times = pd.to_datetime(
        dataframe["Date (MM/DD/YYYY)"].astype(str)
        + " "
        + dataframe["Time (HH:MM)"].astype(str),
        format="%m/%d/%Y %H:%M",
        errors="raise",
    )

    expected_first = pd.Timestamp(
        year=expected_year,
        month=1,
        day=1,
        hour=0,
    )

    expected_last = pd.Timestamp(
        year=expected_year,
        month=12,
        day=31,
        hour=23,
    )

    if output_times.iloc[0] != expected_first:
        raise ValueError(
            "Eerste tijdstip in de uitvoer is niet "
            f"{expected_first}, maar {output_times.iloc[0]}."
        )

    if output_times.iloc[-1] != expected_last:
        raise ValueError(
            "Laatste tijdstip in de uitvoer is niet "
            f"{expected_last}, maar {output_times.iloc[-1]}."
        )

    time_differences = output_times.diff().dropna()

    # Bij een schrikkeljaar is er na het verwijderen van 29 februari
    # één sprong van 25 uur. Alle andere stappen moeten één uur zijn.
    allowed_differences = {
        pd.Timedelta(hours=1),
        pd.Timedelta(hours=25),
    }

    invalid_differences = (
        ~time_differences.isin(
            allowed_differences
        )
    )

    if invalid_differences.any():
        invalid_rows = dataframe.loc[
            invalid_differences[
                invalid_differences
            ].index,
            [
                "Date (MM/DD/YYYY)",
                "Time (HH:MM)",
            ],
        ].head(10)

        raise ValueError(
            "Uitvoer bevat onverwachte tijdsprongen.\n"
            f"{invalid_rows.to_string(index=False)}"
        )


# ============================================================
# HOOFDPROGRAMMA
# ============================================================

def main():
    settings = ask_inputs()

    project_name = settings[
        "project_name"
    ]

    year = settings["year"]

    latitude = settings["latitude"]
    longitude = settings["longitude"]
    elevation = settings["elevation"]

    main_station = settings[
        "main_station"
    ]

    wind_station = settings[
        "wind_station"
    ]

    output_filename = (
        f"tmy3_{main_station}_"
        f"{slugify(project_name)}_"
        f"{year}.csv"
    )

    output_path = get_local_path(
        output_filename
    )

    print()
    print("Definitieve instellingen:")
    print(f"Project         : {project_name}")
    print(f"Jaar            : {year}")

    print(
        f"Zon/temperatuur : "
        f"{main_station} - "
        f"{settings['main_station_name']} "
        f"({settings['main_distance_km']:.1f} km)"
    )

    print(
        f"Back-up zon     : "
        f"{settings['main_backup_station']}"
    )

    print(
        f"Wind            : "
        f"{wind_station} - "
        f"{settings['wind_station_name']} "
        f"({settings['wind_distance_km']:.1f} km)"
    )

    print(
        f"Back-up wind    : "
        f"{settings['wind_backup_station']}"
    )

    print(
        f"Projectlocatie  : "
        f"{latitude:.6f}, {longitude:.6f}"
    )

    print(f"Tijdzone        : {TZ}")

    print()
    print(
        "1) Volledig jaar Q en T downloaden..."
    )

    main_text = download_knmi_hourly(
        station=main_station,
        year=year,
        variables=MAIN_VARIABLES,
    )

    print(
        "2) Volledig jaar FH en FF downloaden..."
    )

    wind_text = download_knmi_hourly(
        station=wind_station,
        year=year,
        variables=WIND_VARIABLES,
    )

    print("3) KNMI-data parsen...")

    main_raw = parse_knmi(
        main_text,
        MAIN_VARIABLES,
    )

    wind_raw = parse_knmi(
        wind_text,
        WIND_VARIABLES,
    )

    print(
        f"   Hoofdstation: {len(main_raw)} regels"
    )

    print(
        f"   Windstation : {len(wind_raw)} regels"
    )

    print("4) Eenheden converteren...")

    main_data = prepare_main_station(
        main_raw
    )

    wind_data = prepare_wind_station(
        wind_raw
    )

    print(
        f"   Geldige windwaarden: "
        f"{int(wind_data['Wspd'].notna().sum())}"
    )

    print(
        f"   Lege windwaarden: "
        f"{int(wind_data['Wspd'].isna().sum())}"
    )

    print("5) Data samenvoegen...")

    physical = merge_main_and_wind(
        main_data,
        wind_data,
    )

    print("6) Tijdindexen maken...")

    (
        midpoint_times,
        hour_beginning_times,
    ) = build_times(
        physical
    )

    # Het kalenderjaar wordt bepaald op basis van het begin
    # van het uurvak.
    year_mask = (
        hour_beginning_times.year == year
    )

    physical = (
        physical.loc[year_mask]
        .reset_index(drop=True)
    )

    midpoint_times = (
        midpoint_times[year_mask]
    )

    hour_beginning_times = (
        hour_beginning_times[year_mask]
    )

    (
        physical,
        midpoint_times,
        hour_beginning_times,
    ) = remove_february_29(
        physical,
        midpoint_times,
        hour_beginning_times,
    )

    print(
        f"   Aantal regels: {len(physical)}"
    )

    print()
    print("   Controle tijdassen:")

    print(
        f"   Eerste HelioScope-uur : "
        f"{hour_beginning_times[0]}"
    )

    print(
        f"   Eerste midpoint       : "
        f"{midpoint_times[0]}"
    )

    print(
        f"   Laatste HelioScope-uur: "
        f"{hour_beginning_times[-1]}"
    )

    print(
        f"   Laatste midpoint      : "
        f"{midpoint_times[-1]}"
    )

    validate_time_axes(
        midpoint_times=midpoint_times,
        hour_beginning_times=hour_beginning_times,
        expected_year=year,
    )

    print("7) DNI en DHI berekenen...")

    decomposition = add_dni_dhi(
        ghi=physical[
            "GHI"
        ].to_numpy(dtype=float),
        times=midpoint_times,
        latitude=latitude,
        longitude=longitude,
    )

    physical["DNI"] = (
        decomposition["DNI"].to_numpy()
    )

    physical["DHI"] = (
        decomposition["DHI"].to_numpy()
    )

    print("8) Fysieke data controleren...")

    validate_physical_data(
        physical
    )

    print("9) HelioScope-template laden...")

    _, header_line, template = (
        load_template()
    )

    print("10) Template vullen...")

    # De TMY3-uitvoer gebruikt het begin van ieder uurvak.
    template["Date (MM/DD/YYYY)"] = [
        timestamp.strftime("%m/%d/%Y")
        for timestamp in hour_beginning_times
    ]

    template["Time (HH:MM)"] = [
        timestamp.strftime("%H:%M")
        for timestamp in hour_beginning_times
    ]

    template["GHI (W/m^2)"] = (
        np.rint(
            physical["GHI"].to_numpy()
        ).astype(int)
    )

    template["DNI (W/m^2)"] = (
        np.rint(
            physical["DNI"].to_numpy()
        ).astype(int)
    )

    template["DHI (W/m^2)"] = (
        np.rint(
            physical["DHI"].to_numpy()
        ).astype(int)
    )

    template["Dry-bulb (C)"] = np.round(
        physical["DryBulb"].to_numpy(),
        1,
    )

    template["Wspd (m/s)"] = np.round(
        physical["Wspd"].to_numpy(),
        1,
    )

    # UTC-offset 0.0 hoort bij TZ = "UTC".
    metadata_line = ",".join(
        [
            str(main_station),
            f'"{project_name} {year}"',
            "NL",
            "0.0",
            f"{latitude:.6f}",
            f"{longitude:.6f}",
            f"{elevation:.1f}",
        ]
    )

    print("11) TMY3-CSV schrijven...")

    save_template(
        output_path=output_path,
        metadata_line=metadata_line,
        header_line=header_line,
        dataframe=template,
    )

    print("12) Geschreven bestand controleren...")

    validate_output_file(
        output_path=output_path,
        expected_year=year,
    )

    print()
    print("Gereed.")
    print(f"Bestand: {output_path}")
    print(f"Aantal uurregels: {len(template)}")

    print(
        "Eerste uitvoertijd:",
        template.iloc[0][
            "Date (MM/DD/YYYY)"
        ],
        template.iloc[0][
            "Time (HH:MM)"
        ],
    )

    print(
        "Laatste uitvoertijd:",
        template.iloc[-1][
            "Date (MM/DD/YYYY)"
        ],
        template.iloc[-1][
            "Time (HH:MM)"
        ],
    )

    print(
        "Lege windsnelheden:",
        int(
            template[
                "Wspd (m/s)"
            ].isna().sum()
        ),
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print()
        print("Script afgebroken.")
        sys.exit(130)

    except Exception as error:
        print()
        print(f"Fout: {error}")

        try:
            input(
                "\nDruk op Enter om af te sluiten..."
            )
        except EOFError:
            pass

        sys.exit(1)