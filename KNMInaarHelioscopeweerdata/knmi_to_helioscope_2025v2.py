import io
import re
import sys
import requests
import pandas as pd
import numpy as np
import pvlib
import os

# =========================
# Defaults
# =========================
DEFAULT_STATION = 260      # De Bilt
DEFAULT_YEAR = 2025
DEFAULT_NAME = "De Bilt"

# Benadering coördinaten voor solar position.
# Voor NL is dit meestal "goed genoeg" als je geen station-coördinaten automatisch ophaalt.
# Je kunt hieronder uitbreiden met meer stations indien gewenst.
STATION_META = {
    260: {"name": "De Bilt", "lat": 52.10, "lon": 5.18},
    265: {"name": "Soesterberg", "lat": 52.13, "lon": 5.28},
    275: {"name": "Deelen", "lat": 52.06, "lon": 5.87},
    269: {"name": "Lelystad", "lat": 52.46, "lon": 5.52},
    235: {"name": "De Kooy", "lat": 52.92, "lon": 4.78},
    240: {"name": "Schiphol", "lat": 52.31, "lon": 4.76},
}

TZ = "UTC"  # KNMI uurgegevens geven HH in UT/UTC. [3](https://netzerogo.org.uk/resources/PVGIS/)[5](https://essd.copernicus.org/articles/16/5243/2024/)

KNMI_URL = "https://daggegevens.knmi.nl/klimatologie/uurgegevens"  # [2](https://pvgis.com/en/manual-5-3)[1](https://photovoltaic-software.com/pv-softwares-calculators/online-free-photovoltaic-software/pvgis)
VARS = "Q:T:FH"  # Q, T, FH zoals in KNMI uurgegevenslijst. [2](https://pvgis.com/en/manual-5-3)[3](https://netzerogo.org.uk/resources/PVGIS/)


def slugify(s: str) -> str:
    """Maak een veilige bestandsnaam-component."""
    s = s.strip().replace(" ", "_")
    s = re.sub(r"[^A-Za-z0-9_\-]", "", s)
    return s or "unknown"


def ask_user_inputs():
    print("=== KNMI → HelioScope weather CSV generator ===")
    print("Je kunt Enter drukken om de default te gebruiken.\n")

    stn_in = input(f"Stationnummer (default {DEFAULT_STATION}={DEFAULT_NAME}): ").strip()
    if stn_in == "":
        station = DEFAULT_STATION
    else:
        station = int(stn_in)

    year_in = input(f"Jaar (default {DEFAULT_YEAR}): ").strip()
    if year_in == "":
        year = DEFAULT_YEAR
    else:
        year = int(year_in)

    # Stationsnaam: uit mapping of vragen
    meta = STATION_META.get(station, None)
    if meta:
        station_name = meta["name"]
        lat = meta["lat"]
        lon = meta["lon"]
        print(f"✅ Herkend station {station}: {station_name} (lat={lat}, lon={lon})")
    else:
        station_name_in = input("Stationsnaam (voor bestandsnaam, bijv. 'De Bilt'): ").strip()
        station_name = station_name_in if station_name_in else f"STN_{station}"
        # Coördinaten nodig voor pvlib solar position
        lat = float(input("Latitude (bijv. 52.10): ").strip())
        lon = float(input("Longitude (bijv. 5.18): ").strip())

    return station, station_name, lat, lon, year


def download_knmi_hourly(station: int, year: int) -> str:
    """Download KNMI uurgegevens via POST. [1](https://photovoltaic-software.com/pv-softwares-calculators/online-free-photovoltaic-software/pvgis)"""
    start = f"{year}010101"
    end = f"{year}123124"

    payload = {
        "start": start,
        "end": end,
        "stns": str(station),
        "vars": VARS,
        "fmt": "csv",
    }

    r = requests.post(KNMI_URL, data=payload, timeout=60)
    r.raise_for_status()
    return r.text


def parse_knmi_text(text: str) -> pd.DataFrame:
    """
    KNMI output kan mét of zonder header komen.
    We skippen commentaarregels (#) en detecteren header.
    """
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        lines.append(line)

    if not lines:
        raise ValueError("Geen dataregels gevonden. Controleer download/parameters.")

    data = "\n".join(lines)

    first = lines[0]
    has_letters = any(ch.isalpha() for ch in first)

    var_list = VARS.split(":") if VARS else []
    expected_cols = ["STN", "YYYYMMDD", "HH"] + var_list

    if has_letters:
        df = pd.read_csv(io.StringIO(data), sep=",", skipinitialspace=True)
        df.columns = [c.strip() for c in df.columns]
    else:
        df = pd.read_csv(io.StringIO(data), sep=",", header=None, skipinitialspace=True)
        if df.shape[1] != len(expected_cols):
            raise ValueError(
                f"Onverwacht aantal kolommen. Verwacht {len(expected_cols)} ({expected_cols}), "
                f"maar kreeg {df.shape[1]}. Eerste regel: {first}"
            )
        df.columns = expected_cols

    return df


def knmi_units_to_physical(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converteer KNMI eenheden:
    - Q: J/cm2 per uurvak → W/m2 via *10000/3600 [2](https://pvgis.com/en/manual-5-3)[3](https://netzerogo.org.uk/resources/PVGIS/)
    - T: 0.1°C → °C [2](https://pvgis.com/en/manual-5-3)[3](https://netzerogo.org.uk/resources/PVGIS/)
    - FH: 0.1 m/s → m/s [2](https://pvgis.com/en/manual-5-3)[3](https://netzerogo.org.uk/resources/PVGIS/)
    """
    out = df.copy()
    for col in ["Q", "T", "FH"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out["ghi_wm2"] = out["Q"] * 10000.0 / 3600.0
    out["temp_c"] = out["T"] / 10.0
    out["wind_ms"] = out["FH"] / 10.0

    out.loc[out["ghi_wm2"] < 0, "ghi_wm2"] = 0.0
    return out


def build_datetime_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    """
    KNMI HH is uurvaknummer 1..24 in UT; uurvak 05 = 04:00–05:00 UT. [3](https://netzerogo.org.uk/resources/PVGIS/)[5](https://essd.copernicus.org/articles/16/5243/2024/)
    Voor zonstand nemen we het midden van het uurvak: einde - 30 min.
    """
    ymd = df["YYYYMMDD"].astype(int).astype(str)
    hh = df["HH"].astype(int)

    base = pd.to_datetime(ymd, format="%Y%m%d", utc=True)
    end_time = base + pd.to_timedelta(hh, unit="h")
    mid_time = end_time - pd.Timedelta(minutes=30)

    return pd.DatetimeIndex(mid_time).tz_convert(TZ)


def add_dni_dhi(df: pd.DataFrame, times: pd.DatetimeIndex, lat: float, lon: float) -> pd.DataFrame:
    """Decomposeer GHI naar DNI/DHI met pvlib Erbs. [4](https://pypi.org/project/pvgis-api/)"""
    location = pvlib.location.Location(lat, lon, tz=TZ)
    solpos = location.get_solarposition(times)
    zenith = solpos["zenith"].values
    ghi = df["ghi_wm2"].values

    erbs_out = pvlib.irradiance.erbs(
        ghi=ghi,
        zenith=zenith,
        datetime_or_doy=times
    )

    df2 = df.copy()
    df2["dni_wm2"] = erbs_out["dni"].astype(float).values
    df2["dhi_wm2"] = erbs_out["dhi"].astype(float).values

    df2.loc[df2["dni_wm2"] < 0, "dni_wm2"] = 0.0
    df2.loc[df2["dhi_wm2"] < 0, "dhi_wm2"] = 0.0

    night = df2["ghi_wm2"] <= 0.0
    df2.loc[night, ["dni_wm2", "dhi_wm2"]] = 0.0
    return df2


def to_helioscope_csv(df: pd.DataFrame, times: pd.DatetimeIndex, outfile: str, hour_1_to_24: bool = False):
    """
    Export CSV:
    Year,Month,Day,Hour,GHI,DNI,DHI,Temperature,WindSpeed

    hour_1_to_24=False → 0..23
    hour_1_to_24=True  → 1..24 (soms vereist)
    """
    hours = times.hour
    if hour_1_to_24:
        hours = hours + 1

    out = pd.DataFrame({
        "Year": times.year,
        "Month": times.month,
        "Day": times.day,
        "Hour": hours,
        "GHI": np.round(df["ghi_wm2"].values, 2),
        "DNI": np.round(df["dni_wm2"].values, 2),
        "DHI": np.round(df["dhi_wm2"].values, 2),
        "Temperature": np.round(df["temp_c"].values, 2),
        "WindSpeed": np.round(df["wind_ms"].values, 2),
    })

    out.to_csv(outfile, index=False)
    print(f"✅ Klaar: {outfile}")
    print(f"   Rijen: {len(out)} (verwacht ~8760 voor niet-schrikkeljaar)")


def main():
    station, station_name, lat, lon, year = ask_user_inputs()
    path = os.path.dirname(os.path.abspath(__file__))
    outfile = f"helioscope_weather_{station}_{slugify(station_name)}_{year}.csv"
    outfile = os.path.join(path, outfile)
    print("\n1) Download KNMI uurgegevens...")
    text = download_knmi_hourly(station, year)

    print("2) Parse KNMI output...")
    df_raw = parse_knmi_text(text)

    print("3) Eenheden omzetten...")
    df_phys = knmi_units_to_physical(df_raw)

    print("4) Tijdindex bouwen (UTC, mid-hour)...")
    times = build_datetime_index(df_phys)

    # Filter exact op jaar (veiligheid)
    mask = (times.year == year)
    df_phys = df_phys.loc[mask].reset_index(drop=True)
    times = times[mask]

    print("5) DNI/DHI berekenen (Erbs)...")
    df_full = add_dni_dhi(df_phys, times, lat=lat, lon=lon)

    print("6) Exporteren naar HelioScope CSV...")
    # Als HelioScope 1..24 wil, zet naar True.
    to_helioscope_csv(df_full, times, outfile, hour_1_to_24=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Fout:", e)
        sys.exit(1)