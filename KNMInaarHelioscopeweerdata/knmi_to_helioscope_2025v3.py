
import io
import sys
import re
import requests
import pandas as pd
import numpy as np
import pvlib
import os

# ========== INSTELLINGEN ==========
KNMI_URL = "https://daggegevens.knmi.nl/klimatologie/uurgegevens"  # [3](https://www.cmsaf.eu/EN/Outreach/Workshops/past_workshops/CMSAF_User_Workshop_2025/Pdfs_Presentations/16_SARAHx_data_in_PVGIS_CMSAF_Workshop2025.pdf?__blob=publicationFile&v=3)[4](https://joint-research-centre.ec.europa.eu/document/download/aa763480-b092-48af-a7f8-42913b43d305_en?filename=Report_2024_PVGIS_5.3_Datasets_Update.pdf&prefLang=cs)
VARS = "Q:T:FH"  # [3](https://www.cmsaf.eu/EN/Outreach/Workshops/past_workshops/CMSAF_User_Workshop_2025/Pdfs_Presentations/16_SARAHx_data_in_PVGIS_CMSAF_Workshop2025.pdf?__blob=publicationFile&v=3)[5](https://www.cmsaf.eu/EN/Outreach/Applications/graphics/PV_GIS.html)
TZ = "UTC"  # KNMI HH in UT/UTC [5](https://www.cmsaf.eu/EN/Outreach/Applications/graphics/PV_GIS.html)[7](https://daggegevens.knmi.nl/)

DEFAULT_STATION = 260
DEFAULT_STATION_NAME = "De Bilt"
DEFAULT_LAT = 52.10
DEFAULT_LON = 5.18
DEFAULT_YEAR = 2025

# Pad naar jouw HelioScope sample TMY3 CSV (downloaded from HelioScope help)
TEMPLATE_PATH = "tmy3_sample.csv"


def slugify(s: str) -> str:
    s = s.strip().replace(" ", "_")
    s = re.sub(r"[^A-Za-z0-9_\-]", "", s)
    return s or "unknown"


def ask_inputs():
    print("=== KNMI → HelioScope (TMY3 template) === (voor default-->enter)")
    stn = input(f"Stationnummer (default {DEFAULT_STATION}={DEFAULT_STATION_NAME}): ").strip()
    year = input(f"Jaar (default {DEFAULT_YEAR}): ").strip()

    station = int(stn) if stn else DEFAULT_STATION
    year = int(year) if year else DEFAULT_YEAR

    # Voor nu: lat/lon alleen automatisch voor De Bilt default.
    # Wil je ander station? Dan even vragen.
    if station == DEFAULT_STATION:
        name = DEFAULT_STATION_NAME
        lat, lon = DEFAULT_LAT, DEFAULT_LON
    else:
        name = input("Stationsnaam (voor bestandsnaam): ").strip() or f"STN_{station}"
        lat = float(input("Latitude (bijv. 52.10): ").strip())
        lon = float(input("Longitude (bijv. 5.18): ").strip())

    return station, name, lat, lon, year


def download_knmi_hourly(station: int, year: int) -> str:
    start = f"{year}010101"
    end = f"{year}123124"
    payload = {"start": start, "end": end, "stns": str(station), "vars": VARS, "fmt": "csv"}
    r = requests.post(KNMI_URL, data=payload, timeout=60)  # [4](https://joint-research-centre.ec.europa.eu/document/download/aa763480-b092-48af-a7f8-42913b43d305_en?filename=Report_2024_PVGIS_5.3_Datasets_Update.pdf&prefLang=cs)
    r.raise_for_status()
    return r.text


def parse_knmi(text: str) -> pd.DataFrame:
    # drop comment lines
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    if not lines:
        raise ValueError("Geen dataregels gevonden in KNMI response.")

    data = "\n".join(lines)
    first = lines[0]
    has_letters = any(ch.isalpha() for ch in first)

    cols = ["STN", "YYYYMMDD", "HH"] + VARS.split(":")
    if has_letters:
        df = pd.read_csv(io.StringIO(data), sep=",", skipinitialspace=True)
        df.columns = [c.strip() for c in df.columns]
    else:
        df = pd.read_csv(io.StringIO(data), sep=",", header=None, skipinitialspace=True)
        df.columns = cols

    return df


def knmi_to_physical(df: pd.DataFrame) -> pd.DataFrame:
    # Q: J/cm2 per uurvak -> W/m2 = Q*10000/3600 [3](https://www.cmsaf.eu/EN/Outreach/Workshops/past_workshops/CMSAF_User_Workshop_2025/Pdfs_Presentations/16_SARAHx_data_in_PVGIS_CMSAF_Workshop2025.pdf?__blob=publicationFile&v=3)[5](https://www.cmsaf.eu/EN/Outreach/Applications/graphics/PV_GIS.html)
    # T: 0.1°C -> °C ; FH: 0.1m/s -> m/s [3](https://www.cmsaf.eu/EN/Outreach/Workshops/past_workshops/CMSAF_User_Workshop_2025/Pdfs_Presentations/16_SARAHx_data_in_PVGIS_CMSAF_Workshop2025.pdf?__blob=publicationFile&v=3)[5](https://www.cmsaf.eu/EN/Outreach/Applications/graphics/PV_GIS.html)
    out = df.copy()
    for c in ["Q", "T", "FH"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    out["GHI"] = out["Q"] * 10000.0 / 3600.0
    out["DryBulb"] = out["T"] / 10.0
    out["Wspd"] = out["FH"] / 10.0

    out.loc[out["GHI"] < 0, "GHI"] = 0.0
    return out


def build_times(df: pd.DataFrame) -> pd.DatetimeIndex:
    # HH is uurvak 1..24 UT; neem mid-uur = einde - 30min [5](https://www.cmsaf.eu/EN/Outreach/Applications/graphics/PV_GIS.html)[7](https://daggegevens.knmi.nl/)
    ymd = df["YYYYMMDD"].astype(int).astype(str)
    hh = df["HH"].astype(int)
    base = pd.to_datetime(ymd, format="%Y%m%d", utc=True)
    end_time = base + pd.to_timedelta(hh, unit="h")
    mid_time = end_time - pd.Timedelta(minutes=30)
    return pd.DatetimeIndex(mid_time).tz_convert(TZ)


def add_dni_dhi(ghi: np.ndarray, times: pd.DatetimeIndex, lat: float, lon: float) -> pd.DataFrame:
    # Erbs decomposition [6](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis_en)
    loc = pvlib.location.Location(lat, lon, tz=TZ)
    solpos = loc.get_solarposition(times)
    zenith = solpos["zenith"].values
    erbs = pvlib.irradiance.erbs(ghi=ghi, zenith=zenith, datetime_or_doy=times)  # [6](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis_en)
    out = pd.DataFrame({"DNI": erbs["dni"].astype(float).values, "DHI": erbs["dhi"].astype(float).values})
    out[out < 0] = 0.0
    out[ghi <= 0] = 0.0
    return out


def load_template(path: str) -> pd.DataFrame:
    """
    Leest HelioScope TMY3 sample:
    - Regel 1: metadata (7 velden)
    - Regel 2: kolomheaders
    - Regel 3..: data
    """
    path_script = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(path_script, path)
    with open(full_path, "r", encoding="utf-8") as f:
        first_line = f.readline().rstrip("\n")
        header_line = f.readline().rstrip("\n")
        rest = f.read()

    # Dataframe maken uit rest met header_line als kolomnamen
    cols = [c.strip() for c in header_line.split(",")]
    df = pd.read_csv(io.StringIO(rest), names=cols, header=None)

    return first_line, header_line, df


def save_template(path_out: str, meta_line: str, header_line: str, df: pd.DataFrame):
    with open(path_out, "w", encoding="utf-8", newline="\n") as f:
        f.write(meta_line + "\n")
        f.write(header_line + "\n")
        df.to_csv(f, index=False, header=False)


def main():
    station, name, lat, lon, year = ask_inputs()

    fileout_path = f"tmy3_{station}_{slugify(name)}_{year}.csv"
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fileout_path)
    print("1) Download KNMI uurgegevens...")
    knmi_txt = download_knmi_hourly(station, year)

    print("2) Parse KNMI...")
    knmi_df = parse_knmi(knmi_txt)

    print("3) Converteer eenheden...")
    phys = knmi_to_physical(knmi_df)

    print("4) Bouw tijdindex...")
    times_mid = build_times(phys)   # KNMI is mid-uurvak, HelioScope verwacht einde uurvak [5](https://www.cmsaf.eu/EN/Outreach/Applications/graphics/PV_GIS.html)[7](https://daggegevens.knmi.nl/)
    times_end = times_mid + pd.Timedelta(minutes=30)  # einde uurvak
    # Filter op exact jaar (veiligheid)

    mask = (times_mid.year == year)

    phys = phys.loc[mask].reset_index(drop=True)
    times_end = times_end[mask]
    times_mid = times_mid[mask]

    print("5) DNI/DHI afleiden...")
    dd = add_dni_dhi(phys["GHI"].values, times_mid, lat, lon)
    phys["DNI"] = dd["DNI"].values
    phys["DHI"] = dd["DHI"].values

    print("6) Laad HelioScope TMY3 template...")
    meta_line, header_line, tpl = load_template(TEMPLATE_PATH)

    # HelioScope eist exact 8760 uurregels voor non-leap-year uploads [1](https://essd.copernicus.org/articles/16/5243/2024/)
    if len(phys) != 8760:
        
        raise ValueError(f"KNMI dataset heeft {len(phys)} rijen, verwacht 8760 voor {year}. De eerste rijen zijn \n{phys.head(5)}\nDe laatste rijen zijn\n{phys.tail(5)}")


    if len(tpl) != 8760:
        raise ValueError(f"Template heeft {len(tpl)} rijen, verwacht 8760 (sample moet compleet zijn).")

    print("7) Vul template kolommen (minimaal vereist door HelioScope)...")
    # Datum/tijd format zoals sample: MM/DD/YYYY en HH:MM
    tpl["Date (MM/DD/YYYY)"] = [t.strftime("%m/%d/%Y") for t in times_end]
    tpl["Time (HH:MM)"] = [t.strftime("%H:%M") for t in times_end]

    # Irradiance
    tpl["GHI (W/m^2)"] = np.round(phys["GHI"].values, 0).astype(int)
    tpl["DNI (W/m^2)"] = np.round(phys["DNI"].values, 0).astype(int)
    tpl["DHI (W/m^2)"] = np.round(phys["DHI"].values, 0).astype(int)

    # Temp / wind
    tpl["Dry-bulb (C)"] = np.round(phys["DryBulb"].values, 1)
    tpl["Wspd (m/s)"] = np.round(phys["Wspd"].values, 1)

    print("8) Update metadata regel (FileID, naam, TZ, lat/lon)...")
    # meta format: File ID, "File Name", State, Time Zone, Lat, Lon, Elev
    parts = []
    # preserve quoting for name
    parts.append(str(station))
    parts.append(f"\"{name}\"")
    parts.append("NL")
    parts.append("0.0")  # UTC offset; KNMI is UT/UTC [5](https://www.cmsaf.eu/EN/Outreach/Applications/graphics/PV_GIS.html)
    parts.append(f"{lat:.3f}")
    parts.append(f"{lon:.3f}")
    parts.append("0")    # elevation onbekend -> 0 (kan je later verbeteren)

    new_meta = ",".join(parts)

    print("9) Schrijf nieuw TMY3 CSV...")
    save_template(out_path, new_meta, header_line, tpl)

    print(f"✅ Gereed: {os.path.abspath(out_path)}")
    print("Upload dit bestand in HelioScope als: TMY3 (.csv)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Fout:", e)
        sys.exit(1)
