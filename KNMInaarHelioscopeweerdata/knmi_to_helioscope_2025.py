import io
import sys
import requests
import pandas as pd
import numpy as np
import pvlib

# =========================
# Instellingen
# =========================
STATION = 260  # De Bilt
YEAR = 2025

# De Bilt (ongeveer)
LAT = 52.10
LON = 5.18

# KNMI uurgegevens zijn in UT/UTC (HH = uurvak 1..24). [3](https://netzerogo.org.uk/resources/PVGIS/)[4](https://essd.copernicus.org/articles/16/5243/2024/)
TZ = "UTC"

# KNMI uurgegevens selectie endpoint (POST) [1](https://photovoltaic-software.com/pv-softwares-calculators/online-free-photovoltaic-software/pvgis)[2](https://pvgis.com/en/manual-5-3)
KNMI_URL = "https://daggegevens.knmi.nl/klimatologie/uurgegevens"

# Q = globale straling (J/cm2/uurvak), T = temp (0.1°C), FH = uurgem wind (0.1 m/s) [2](https://pvgis.com/en/manual-5-3)[3](https://netzerogo.org.uk/resources/PVGIS/)
VARS = "Q:T:FH"

OUTFILE = f"helioscope_weather_{STATION}_{YEAR}.csv"


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
    KNMI output bevat vaak commentaarregels (#) en daarna CSV.
    Soms mét header, soms zónder header. Wij detecteren dat.
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

    # Bepaal of eerste regel een header is (bevat letters) of datarij (allemaal cijfers/commas/mintekens)
    first = lines[0]
    has_letters = any(ch.isalpha() for ch in first)

    var_list = VARS.split(":") if VARS else []
    expected_cols = ["STN", "YYYYMMDD", "HH"] + var_list

    if has_letters:
        # Header aanwezig
        df = pd.read_csv(io.StringIO(data), sep=",", skipinitialspace=True)
        df.columns = [c.strip() for c in df.columns]
    else:
        # Geen header: eerste rij is data -> header=None en zelf kolomnamen zetten
        df = pd.read_csv(io.StringIO(data), sep=",", header=None, skipinitialspace=True)
        if df.shape[1] != len(expected_cols):
            raise ValueError(
                f"Onverwacht aantal kolommen. Verwacht {len(expected_cols)} ({expected_cols}), "
                f"maar kreeg {df.shape[1]}. Eerste regel: {first}"
            )
        df.columns = expected_cols

    required = set(expected_cols)
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Ontbrekende kolommen: {missing}. Gevonden: {df.columns.tolist()}")

    return df


def knmi_units_to_physical(df: pd.DataFrame) -> pd.DataFrame:
    """
    Zet KNMI eenheden om:
    - Q: J/cm2 per uurvak -> W/m2 met *10000/3600 [2](https://pvgis.com/en/manual-5-3)[3](https://netzerogo.org.uk/resources/PVGIS/)
    - T: 0.1 °C -> °C [2](https://pvgis.com/en/manual-5-3)[3](https://netzerogo.org.uk/resources/PVGIS/)
    - FH: 0.1 m/s -> m/s [2](https://pvgis.com/en/manual-5-3)[3](https://netzerogo.org.uk/resources/PVGIS/)
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
    KNMI: HH is uurvaknummer 1..24 in UT.
    Uurvak 05 loopt 04:00–05:00 UT; we nemen mid-uur = einde - 30 min. [3](https://netzerogo.org.uk/resources/PVGIS/)[4](https://essd.copernicus.org/articles/16/5243/2024/)
    """
    ymd = df["YYYYMMDD"].astype(int).astype(str)
    hh = df["HH"].astype(int)

    base = pd.to_datetime(ymd, format="%Y%m%d", utc=True)
    end_time = base + pd.to_timedelta(hh, unit="h")
    mid_time = end_time - pd.Timedelta(minutes=30)

    return pd.DatetimeIndex(mid_time).tz_convert(TZ)


def add_dni_dhi(df: pd.DataFrame, times: pd.DatetimeIndex) -> pd.DataFrame:
    """Decomposeer GHI naar DNI/DHI met Erbs (pvlib). [5](https://pypi.org/project/pvgis-api/)"""
    location = pvlib.location.Location(LAT, LON, tz=TZ)
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
    Export:
    Year,Month,Day,Hour,GHI,DNI,DHI,Temperature,WindSpeed
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
    print(f"   Rijen: {len(out)} (verwacht ~8760 voor {YEAR})")


def main():
    print("1) Download KNMI uurgegevens...")
    text = download_knmi_hourly(STATION, YEAR)

    print("2) Parse KNMI output...")
    df_raw = parse_knmi_text(text)

    print("3) Eenheden omzetten...")
    df_phys = knmi_units_to_physical(df_raw)

    print("4) Tijdindex bouwen (UTC, mid-hour)...")
    times = build_datetime_index(df_phys)

    # Filter exact op jaar (veiligheid)
    mask = (times.year == YEAR)
    df_phys = df_phys.loc[mask].reset_index(drop=True)
    times = times[mask]

    print("5) DNI/DHI berekenen (Erbs)...")
    df_full = add_dni_dhi(df_phys, times)

    print("6) Exporteren naar HelioScope CSV...")
    # Als HelioScope 1..24 verwacht: zet hour_1_to_24=True
    to_helioscope_csv(df_full, times, OUTFILE, hour_1_to_24=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Fout:", e)
        sys.exit(1)
