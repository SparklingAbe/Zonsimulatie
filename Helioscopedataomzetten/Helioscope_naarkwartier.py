import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

import numpy as np
import pandas as pd

from scipy.interpolate import interp1d


# ============================================================
# INSTELLINGEN
# ============================================================

VALUE_COLUMN = "grid_power"

NUMBER_OF_PLOT_POINTS = 1000

# True wanneer grid_power in de invoer-CSV in watt staat.
# De waarden worden dan naar kW omgerekend.
INPUT_IS_WATT = True

SOURCE_TIMEZONE = "UTC"
OUTPUT_TIMEZONE = "Europe/Amsterdam"

# Welke van de dubbele lokale wintertijdrecords behouden blijft.
# "first" bewaart het eerste lokale 02:00-uur.
DUPLICATE_KEEP = "first"

# Tolerantie voor waarschuwingen over verwijderde energie.
ENERGY_TOLERANCE_KWH = 1e-9
FINAL_TIME_SHIFT = pd.Timedelta(hours=1)  #uur shift is nodig om validatie spot on op helioscope te krijgen
DEGRADATION_FACTOR = 0.9  #gekalibreerd met validatie. Nodig om simulatie realistischer te maken. 1.0 = geen degradatie, 0.9 = 10% productieverlies, etc.
# ============================================================
# BESTANDSPAD
# ============================================================

def top_script_dir() -> Path:
    """
    Geeft de map terug waarin het bovenste script staat.

    Bij interactief gebruik wordt de huidige werkmap gebruikt.
    """

    main = sys.modules.get("__main__")

    if (
        main is not None
        and hasattr(main, "__file__")
    ):
        return Path(
            main.__file__
        ).resolve().parent

    return Path.cwd()


def choose_csv_file() -> Path:
    """
    Opent een dialoogvenster om een CSV-bestand te selecteren.
    """

    root = tk.Tk()
    root.withdraw()

    try:
        file_path = filedialog.askopenfilename(
            title="Kies invoerbestand",
            filetypes=[
                ("CSV-bestanden", "*.csv"),
            ],
            initialdir=top_script_dir(),
        )

    finally:
        root.destroy()

    if not file_path:
        raise ValueError(
            "Geen bestand gekozen."
        )

    return Path(file_path)


# ============================================================
# UUR NAAR KWARTIER
# ============================================================

def uur_naar_kwartier_quadratic(
    day: pd.DataFrame,
    value_column: str = VALUE_COLUMN,
) -> pd.DataFrame:
    """
    Zet uurvermogens om naar kwartiervermogens.

    De interpolatie wordt quadratic uitgevoerd als minimaal drie
    uurwaarden beschikbaar zijn.

    Na interpolatie worden de vier kwartierwaarden per uur herschaald,
    zodat het gemiddelde van de vier kwartieren exact gelijk is aan
    de oorspronkelijke uurwaarde.

    De tijdindex blijft hier volledig in UTC.
    """

    day = day.sort_index().copy()

    if day.empty:
        return pd.DataFrame(
            columns=[
                value_column,
                "energy_kwh",
            ]
        )

    values_kw = day[
        value_column
    ].to_numpy(
        dtype=float
    )

    values_kw = np.nan_to_num(
        values_kw,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )

    if np.any(values_kw < 0):
        raise ValueError(
            f"Negatieve waarden gevonden op "
            f"{day.index[0].date()}."
        )

    number_of_hours = len(
        values_kw
    )

    number_of_quarters = (
        number_of_hours * 4
    )

    # --------------------------------------------------------
    # Eén uur
    # --------------------------------------------------------

    if number_of_hours == 1:
        quarter_power_kw = np.full(
            4,
            values_kw[0],
            dtype=float,
        )

    # --------------------------------------------------------
    # Twee uren: lineair
    # --------------------------------------------------------

    elif number_of_hours == 2:
        hour_centers = (
            np.arange(
                number_of_hours,
                dtype=float,
            )
            + 0.5
        )

        quarter_centers = (
            np.arange(
                number_of_quarters,
                dtype=float,
            )
            * 0.25
            + 0.125
        )

        interpolator = interp1d(
            hour_centers,
            values_kw,
            kind="linear",
            bounds_error=False,
            fill_value="extrapolate",
            assume_sorted=True,
        )

        quarter_power_kw = interpolator(
            quarter_centers
        )

    # --------------------------------------------------------
    # Drie of meer uren: quadratic
    # --------------------------------------------------------

    else:
        hour_centers = (
            np.arange(
                number_of_hours,
                dtype=float,
            )
            + 0.5
        )

        quarter_centers = (
            np.arange(
                number_of_quarters,
                dtype=float,
            )
            * 0.25
            + 0.125
        )

        interpolator = interp1d(
            hour_centers,
            values_kw,
            kind="quadratic",
            bounds_error=False,
            fill_value="extrapolate",
            assume_sorted=True,
        )

        quarter_power_kw = interpolator(
            quarter_centers
        )

    # Eventuele fouten en negatieve overshoot verwijderen
    quarter_power_kw = np.nan_to_num(
        quarter_power_kw,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )

    quarter_power_kw = np.maximum(
        quarter_power_kw,
        0.0,
    )

    # Vier kwartieren per uur
    quarter_matrix = (
        quarter_power_kw.reshape(
            number_of_hours,
            4,
        )
    )

    quarter_means = (
        quarter_matrix.mean(
            axis=1
        )
    )

    positive_target = (
        values_kw > 0.0
    )

    usable_mean = (
        quarter_means > 0.0
    )

    correction_factors = np.ones(
        number_of_hours,
        dtype=float,
    )

    valid_hours = (
        positive_target
        & usable_mean
    )

    correction_factors[
        valid_hours
    ] = (
        values_kw[valid_hours]
        / quarter_means[valid_hours]
    )

    quarter_matrix[
        valid_hours
    ] *= correction_factors[
        valid_hours,
        np.newaxis,
    ]

    # Oorspronkelijk nul betekent alle kwartieren nul
    zero_hours = (
        values_kw == 0.0
    )

    quarter_matrix[
        zero_hours
    ] = 0.0

    # Fallback als interpolatie gemiddeld nul opleverde
    fallback_hours = (
        positive_target
        & ~usable_mean
    )

    quarter_matrix[
        fallback_hours
    ] = values_kw[
        fallback_hours,
        np.newaxis,
    ]

    quarter_power_kw = (
        quarter_matrix.reshape(
            number_of_quarters
        )
    )

    # Energie per kwartier
    quarter_energy_kwh = (
        quarter_power_kw * 0.25
    )

    quarter_index = pd.date_range(
        start=day.index[0],
        periods=number_of_quarters,
        freq="15min",
    )

    return pd.DataFrame(
        {
            value_column: quarter_power_kw,
            "energy_kwh": quarter_energy_kwh,
        },
        index=quarter_index,
    )


# ============================================================
# CONTROLE PER UUR
# ============================================================

def controleer_uurwaarden(
    df_uur: pd.DataFrame,
    df_kwartier: pd.DataFrame,
    value_column: str = VALUE_COLUMN,
) -> pd.DataFrame:
    """
    Controleert of het gemiddelde van vier kwartieren gelijk is
    aan de oorspronkelijke uurwaarde.
    """

    original_hourly = (
        df_uur[value_column]
        .fillna(0.0)
        .rename("original_hourly_kw")
    )

    reconstructed_hourly = (
        df_kwartier[value_column]
        .resample("1h")
        .mean()
        .rename("reconstructed_hourly_kw")
    )

    check = pd.concat(
        [
            original_hourly,
            reconstructed_hourly,
        ],
        axis=1,
    )

    check["difference_kw"] = (
        check[
            "reconstructed_hourly_kw"
        ]
        - check[
            "original_hourly_kw"
        ]
    )

    check[
        "equal_within_tolerance"
    ] = np.isclose(
        check[
            "original_hourly_kw"
        ],
        check[
            "reconstructed_hourly_kw"
        ],
        rtol=1e-8,
        atol=1e-8,
    )

    return check


# ============================================================
# CONTROLE PER UTC-DAG
# ============================================================

def controleer_dagsommen(
    df_uur: pd.DataFrame,
    df_kwartier: pd.DataFrame,
    value_column: str = VALUE_COLUMN,
) -> pd.DataFrame:
    """
    Controleert de energie per UTC-kalenderdag.

    Deze controle wordt vóór de conversie naar Amsterdam uitgevoerd.
    """

    hourly_daily_energy = (
        df_uur[value_column]
        .fillna(0.0)
        .groupby(
            df_uur.index.normalize()
        )
        .sum()
        .rename("hourly_energy_kwh")
    )

    quarterly_daily_energy = (
        df_kwartier["energy_kwh"]
        .groupby(
            df_kwartier.index.normalize()
        )
        .sum()
        .rename("quarterly_energy_kwh")
    )

    check = pd.concat(
        [
            hourly_daily_energy,
            quarterly_daily_energy,
        ],
        axis=1,
    )

    check["difference_kwh"] = (
        check[
            "quarterly_energy_kwh"
        ]
        - check[
            "hourly_energy_kwh"
        ]
    )

    check[
        "equal_within_tolerance"
    ] = np.isclose(
        check[
            "hourly_energy_kwh"
        ],
        check[
            "quarterly_energy_kwh"
        ],
        rtol=1e-8,
        atol=1e-8,
    )

    return check

# ============================================================
# DEGRADATIE
# ============================================================

def apply_degradation(
    df_kwartier: pd.DataFrame,
    degradation_factor: float,
    value_column: str = VALUE_COLUMN,
) -> pd.DataFrame:
    """
    Past een vaste degradatiefactor toe op de gesimuleerde
    productie.

    Voorbeelden:
        1.00 = geen degradatie
        0.98 = 2% productieverlies
        0.95 = 5% productieverlies
        0.90 = 10% productieverlies

    Zowel vermogen als energie worden aangepast.
    """

    if not 0.0 <= degradation_factor <= 1.0:
        raise ValueError(
            "DEGRADATION_FACTOR moet tussen 0 en 1 liggen."
        )

    result = df_kwartier.copy()

    energy_before = float(
        result["energy_kwh"].sum()
    )

    result[value_column] = (
        result[value_column]
        * degradation_factor
    )

    result["energy_kwh"] = (
        result["energy_kwh"]
        * degradation_factor
    )

    energy_after = float(
        result["energy_kwh"].sum()
    )

    loss_percentage = (
        (1.0 - degradation_factor) * 100.0
    )

    print()
    print("Degradatie:")
    print(
        f"Factor              : {degradation_factor:.4f}"
    )
    print(
        f"Productieverlies    : {loss_percentage:.2f}%"
    )
    print(
        f"Energie vóór        : {energy_before:.2f} kWh"
    )
    print(
        f"Energie na          : {energy_after:.2f} kWh"
    )
    print(
        f"Verschil            : "
        f"{energy_before - energy_after:.2f} kWh"
    )

    return result

# ============================================================
# UTC NAAR AMSTERDAM EN DST NORMALISEREN
# ============================================================
def converteer_naar_amsterdam_vaste_kwartieren(
    df_kwartier_utc: pd.DataFrame,
    value_column: str = VALUE_COLUMN,
) -> tuple[pd.DataFrame, dict]:
    """
    Zet UTC-kwartierdata om naar lokale Amsterdamtijd.

    Daarna wordt de lokale tijdreeks genormaliseerd:

    - start exact op 1 januari 00:00
    - einde exact op 31 december 23:45
    - interval exact 15 minuten
    - wintertijdduplicaten worden verwijderd
    - zomertijdgat wordt aangevuld met nullen
    - ontbrekende waarden aan begin/einde worden ook nul

    De uiteindelijke index is timezone-naive,
    zodat Excel deze probleemloos kan opslaan.
    """

    if df_kwartier_utc.empty:
        raise ValueError(
            "Geen kwartierwaarden beschikbaar."
        )

    if df_kwartier_utc.index.tz is None:
        raise ValueError(
            "De kwartierindex heeft geen timezone. "
            "UTC werd verwacht."
        )

    if df_kwartier_utc.index.has_duplicates:
        raise ValueError(
            "De UTC-index bevat dubbele timestamps."
        )

    # ========================================================
    # UTC -> EUROPE/AMSTERDAM
    # ========================================================

    local = df_kwartier_utc.copy()

    local.index = local.index.tz_convert(
        OUTPUT_TIMEZONE
    )

    # Jaar bepalen op basis van lokale tijd
    years = local.index.year

    unique_years = np.unique(years)

    if len(unique_years) != 1:
        print(
            "LET OP: na UTC -> Amsterdam komen "
            "meerdere lokale jaren voor:",
            unique_years,
        )

        # Gebruik het jaar waarin de meeste waarden voorkomen
        year_counts = pd.Series(
            years
        ).value_counts()

        target_year = int(
            year_counts.index[0]
        )

    else:
        target_year = int(
            unique_years[0]
        )

    print()
    print(
        f"Lokaal kalenderjaar voor uitvoer: "
        f"{target_year}"
    )

    # ========================================================
    # ALLEEN GEWENSTE LOKALE JAAR
    # ========================================================

    year_mask = (
        local.index.year == target_year
    )

    local = local.loc[
        year_mask
    ].copy()

    # ========================================================
    # TIMEZONE VERWIJDEREN
    # ========================================================

    # Vanaf dit punt werken we met lokale kloktijd.
    #
    # Hierdoor worden de twee 02:xx-reeksen tijdens de
    # wintertijdomschakeling echte duplicate timestamps.

    local.index = (
        local.index.tz_localize(None)
    )

    # De volledige lokale reeks bewust 1 uur later zetten
    local.index = (
        local.index + FINAL_TIME_SHIFT
    )

    local.index.name = "timestamp"

    # ========================================================
    # WINTERTIJD-DUBBELEN DETECTEREN
    # ========================================================

    duplicate_mask_all = (
        local.index.duplicated(
            keep=False
        )
    )

    duplicate_rows_total = int(
        duplicate_mask_all.sum()
    )

    duplicate_local_quarters = int(
        local.index[
            duplicate_mask_all
        ].nunique()
    )

    # Welke records daadwerkelijk verwijderd worden
    remove_duplicate_mask = (
        local.index.duplicated(
            keep=DUPLICATE_KEEP
        )
    )

    removed_duplicates = local.loc[
        remove_duplicate_mask
    ].copy()

    duplicates_removed = int(
        remove_duplicate_mask.sum()
    )

    # Energie die eventueel wordt verwijderd
    if removed_duplicates.empty:

        removed_duplicate_energy_kwh = 0.0
        removed_duplicate_max_power_kw = 0.0

    else:

        removed_duplicate_energy_kwh = float(
            removed_duplicates[
                "energy_kwh"
            ].sum()
        )

        removed_duplicate_max_power_kw = float(
            removed_duplicates[
                value_column
            ].max()
        )

    # ========================================================
    # DUBBELEN VERWIJDEREN
    # ========================================================

    local = local.loc[
        ~remove_duplicate_mask
    ].copy()

    local = local.sort_index()

    # ========================================================
    # EXACTE JAARINDEX MAKEN
    # ========================================================

    year_start = pd.Timestamp(
        year=target_year,
        month=1,
        day=1,
        hour=0,
        minute=0,
    )

    year_end = pd.Timestamp(
        year=target_year,
        month=12,
        day=31,
        hour=23,
        minute=45,
    )

    complete_local_index = pd.date_range(
        start=year_start,
        end=year_end,
        freq="15min",
    )

    print()
    print(
        "Gewenste start:",
        year_start,
    )

    print(
        "Gewenst einde:",
        year_end,
    )

    print(
        "Aantal verwachte kwartieren:",
        len(complete_local_index),
    )

    # ========================================================
    # ONTBREKENDE TIMESTAMPS BEPALEN
    # ========================================================

    missing_local_timestamps = (
        complete_local_index.difference(
            local.index
        )
    )

    missing_count = len(
        missing_local_timestamps
    )

    print(
        "Aantal ontbrekende lokale kwartieren:",
        missing_count,
    )

    # ========================================================
    # REINDEX NAAR PRECIES HET KALENDERJAAR
    # ========================================================

    local = local.reindex(
        complete_local_index
    )

    local.index.name = "timestamp"

    # ========================================================
    # ALLE ONTBREKENDE KWARTIEREN OP 0
    # ========================================================

    local[value_column] = (
        local[value_column]
        .fillna(0.0)
    )

    local["energy_kwh"] = (
        local["energy_kwh"]
        .fillna(0.0)
    )

    # ========================================================
    # CONTROLES
    # ========================================================

    if local.index.has_duplicates:

        raise ValueError(
            "De lokale jaarindex bevat nog "
            "dubbele timestamps."
        )

    expected_first = pd.Timestamp(
        target_year,
        1,
        1,
        0,
        0,
    )

    expected_last = pd.Timestamp(
        target_year,
        12,
        31,
        23,
        45,
    )

    if local.index[0] != expected_first:

        raise ValueError(
            f"Verkeerde eerste timestamp: "
            f"{local.index[0]}. "
            f"Verwacht {expected_first}."
        )

    if local.index[-1] != expected_last:

        raise ValueError(
            f"Verkeerde laatste timestamp: "
            f"{local.index[-1]}. "
            f"Verwacht {expected_last}."
        )

    spacing = (
        local.index.to_series()
        .diff()
        .dropna()
    )

    incorrect_spacing = (
        spacing
        != pd.Timedelta(minutes=15)
    )

    if incorrect_spacing.any():

        raise ValueError(
            "De lokale uitvoer bevat geen "
            "doorlopende 15-minutenindex."
        )

    # ========================================================
    # DIAGNOSTIEK
    # ========================================================

    diagnostics = {

        "year": target_year,

        "start": local.index[0],

        "end": local.index[-1],

        "number_of_quarters": len(local),

        "duplicate_rows_total": (
            duplicate_rows_total
        ),

        "duplicate_local_quarters": (
            duplicate_local_quarters
        ),

        "duplicates_removed": (
            duplicates_removed
        ),

        "removed_duplicate_energy_kwh": (
            removed_duplicate_energy_kwh
        ),

        "removed_duplicate_max_power_kw": (
            removed_duplicate_max_power_kw
        ),

        "missing_quarters_added": (
            missing_count
        ),

        "missing_timestamps": (
            missing_local_timestamps.tolist()
        ),
    }

    return local, diagnostics

# ============================================================
# HOOFDPROGRAMMA
# ============================================================

def main():
    csv_invoer_path = choose_csv_file()

    print("Bestand:")
    print(csv_invoer_path)

    df = pd.read_csv(
        csv_invoer_path
    )

    required_columns = {
        "timestamp",
        VALUE_COLUMN,
    }

    missing_columns = (
        required_columns
        - set(df.columns)
    )

    if missing_columns:
        raise ValueError(
            "Kolommen ontbreken in CSV: "
            f"{sorted(missing_columns)}"
        )

    df_uur = df[
        [
            "timestamp",
            VALUE_COLUMN,
        ]
    ].copy()

    # --------------------------------------------------------
    # Timestamps expliciet als UTC inlezen
    # --------------------------------------------------------

    df_uur["timestamp"] = pd.to_datetime(
        df_uur["timestamp"],
        errors="raise",
        utc=True,
    )

    df_uur[VALUE_COLUMN] = pd.to_numeric(
        df_uur[VALUE_COLUMN],
        errors="coerce",
    )

    df_uur = df_uur.sort_values(
        "timestamp"
    )

    df_uur.set_index(
        "timestamp",
        inplace=True,
    )

    if df_uur.index.has_duplicates:
        duplicate_timestamps = df_uur.index[
            df_uur.index.duplicated(
                keep=False
            )
        ].unique()

        raise ValueError(
            "Dubbele UTC-timestamps gevonden, bijvoorbeeld: "
            f"{duplicate_timestamps[:5].tolist()}"
        )

    missing_count = int(
        df_uur[
            VALUE_COLUMN
        ].isna().sum()
    )

    print()
    print(
        f"Aantal UTC-uurwaarden: {len(df_uur)}"
    )

    print(
        f"Aantal ontbrekende vermogenswaarden: "
        f"{missing_count}"
    )

    df_uur[VALUE_COLUMN] = (
        df_uur[
            VALUE_COLUMN
        ].fillna(0.0)
    )

    if INPUT_IS_WATT:
        df_uur[VALUE_COLUMN] = (
            df_uur[VALUE_COLUMN]
            / 1000.0
        )

    if (
        df_uur[VALUE_COLUMN] < 0
    ).any():
        negative_values = df_uur.loc[
            df_uur[VALUE_COLUMN] < 0
        ].head(10)

        raise ValueError(
            "Negatieve vermogenswaarden gevonden:\n"
            f"{negative_values}"
        )

    # --------------------------------------------------------
    # Interpoleren in UTC
    # --------------------------------------------------------

    quarterly_days = []

    for _, day in df_uur.groupby(
        df_uur.index.normalize()
    ):
        quarterly_day = (
            uur_naar_kwartier_quadratic(
                day=day,
                value_column=VALUE_COLUMN,
            )
        )

        quarterly_days.append(
            quarterly_day
        )

    if not quarterly_days:
        raise ValueError(
            "Er zijn geen kwartierwaarden berekend."
        )

    df_kwartier_utc = pd.concat(
        quarterly_days
    ).sort_index()

    df_kwartier_utc.index.name = (
        "timestamp"
    )

    if df_kwartier_utc.index.has_duplicates:
        raise ValueError(
            "De berekende UTC-kwartierindex bevat duplicaten."
        )

    print(
        f"Aantal UTC-kwartierwaarden: "
        f"{len(df_kwartier_utc)}"
    )

    # --------------------------------------------------------
    # Controles in UTC
    # --------------------------------------------------------

    hourly_check = controleer_uurwaarden(
        df_uur=df_uur,
        df_kwartier=df_kwartier_utc,
    )

    daily_check = controleer_dagsommen(
        df_uur=df_uur,
        df_kwartier=df_kwartier_utc,
    )

    failed_hours = hourly_check.loc[
        ~hourly_check[
            "equal_within_tolerance"
        ]
    ]

    failed_days = daily_check.loc[
        ~daily_check[
            "equal_within_tolerance"
        ]
    ]

    print()
    print("Controle per UTC-uur:")

    if failed_hours.empty:
        print(
            "Alle kwartiergemiddelden zijn gelijk "
            "aan de oorspronkelijke uurwaarden."
        )
    else:
        print(
            "WAARSCHUWING: afwijkingen per uur gevonden."
        )
        print(failed_hours)

    print()
    print("Controle per UTC-dag:")

    if failed_days.empty:
        print(
            "Alle UTC-dagsommen zijn gelijk."
        )
    else:
        print(
            "WAARSCHUWING: afwijkingen per dag gevonden."
        )
        print(failed_days)


    df_kwartier_utc = apply_degradation(
    df_kwartier=df_kwartier_utc,
    degradation_factor=DEGRADATION_FACTOR,
    value_column=VALUE_COLUMN,
    )


    energy_utc_before_dst = float(
        df_kwartier_utc[
            "energy_kwh"
        ].sum()
    )

    # --------------------------------------------------------
    # Pas nu converteren naar Amsterdam
    # --------------------------------------------------------

    (
        df_kwartier_ams,
        dst_diagnostics,
    ) = converteer_naar_amsterdam_vaste_kwartieren(
        df_kwartier_utc,
        value_column=VALUE_COLUMN,
    )

    print()
    print("================================================")
    print("CONTROLE DEFINITIEVE AMS-TIJDREEKS")
    print("================================================")

    print(
        "Eerste timestamp:",
        df_kwartier_ams.index[0]
    )

    print(
        "Laatste timestamp:",
        df_kwartier_ams.index[-1]
    )

    print(
        "Aantal kwartieren:",
        len(df_kwartier_ams)
    )

    print(
        "Dubbele timestamps:",
        df_kwartier_ams.index.duplicated().sum()
    )

    print(
        "Ontbrekende vermogenswaarden:",
        df_kwartier_ams[VALUE_COLUMN].isna().sum()
    )

    print(
        "Ontbrekende energiewaarden:",
        df_kwartier_ams["energy_kwh"].isna().sum()
    )
    energy_ams_after_dst = float(
        df_kwartier_ams[
            "energy_kwh"
        ].sum()
    )

    print()
    print("DST-correctie Europe/Amsterdam:")
    print(
        "Aantal lokale dubbele records gevonden:",
        dst_diagnostics[
            "duplicate_rows_total"
        ],
    )
    print(
        "Aantal dubbele lokale kwartiertijden:",
        dst_diagnostics[
            "duplicate_local_quarters"
        ],
    )
    print(
        "Aantal wintertijdrecords verwijderd:",
        dst_diagnostics[
            "duplicates_removed"
        ],
    )
    print(
        "Aantal zomertijdkwartieren met nul toegevoegd:",
        dst_diagnostics[
            "missing_quarters_added"
        ],
    )

    if dst_diagnostics[
        "missing_timestamps"
    ]:
        print(
            "Toegevoegde lokale timestamps:"
        )

        for timestamp in dst_diagnostics[
            "missing_timestamps"
        ]:
            print(
                f"  {timestamp}"
            )

    removed_energy = dst_diagnostics[
        "removed_duplicate_energy_kwh"
    ]

    print(
        "Energie verwijderd door wintertijdduplicaten:",
        f"{removed_energy:.10f} kWh",
    )

    print(
        "Maximaal verwijderd kwartiervermogen:",
        (
            f"{dst_diagnostics['removed_duplicate_max_power_kw']:.6f} "
            "kW"
        ),
    )

    print(
        "Totale energie vóór DST-normalisatie:",
        f"{energy_utc_before_dst:.6f} kWh",
    )

    print(
        "Totale energie na DST-normalisatie:",
        f"{energy_ams_after_dst:.6f} kWh",
    )

    if abs(removed_energy) > ENERGY_TOLERANCE_KWH:
        print()
        print(
            "WAARSCHUWING: de verwijderde wintertijdrecords "
            "bevatten energie. Door het verwijderen van de dubbele "
            "lokale tijden is deze energie niet meer aanwezig in "
            "de lokale uitvoer."
        )

    # --------------------------------------------------------
    # Excel-export
    # --------------------------------------------------------

    output_path = csv_invoer_path.with_name(
        f"kwartierwaardes_ams_"
        f"{csv_invoer_path.stem}.xlsx"
    )

    df_export = (
        df_kwartier_ams[["energy_kwh"]].reset_index()
    )

    # De index is hier al lokale naïeve Amsterdamtijd.
    df_export.to_excel(
        output_path,
        index=False,
    )

    print()
    print("Uitvoerbestand:")
    print(output_path)

    print(
        f"Aantal lokale kwartierwaarden: "
        f"{len(df_export)}"
    )

    print(
        "Dubbele lokale timestamps na correctie:",
        int(
            df_export[
                "timestamp"
            ].duplicated().sum()
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