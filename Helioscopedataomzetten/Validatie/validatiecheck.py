import pandas as pd
import matplotlib.pyplot as plt

path = r"C:\Users\a.sikkema\SP\VOF - General\Software\Software\Python\Helioscopedataomzetten\Validatie\Vergelijking_strandweg10.xlsx"

df = pd.read_excel(path)

print(df.columns)

df["Gemeten index"] = pd.to_datetime(
    df["Gemeten index"]
)

df = df.set_index("Gemeten index")
df['Simulatie'] = df['Simulatie'].astype(float)*0.9

# for shift in ["-1h","-45min","-30min","-15min","0min","15min","30min","45min", "1h"]:
#     # print(f"shift: {shift}")
#     df_simulatie = df['Simulatie'].copy()
#     df_simulatie.index = df_simulatie.index + pd.Timedelta(shift)
   
#     df_gemeten = df["Gemeten"].copy()
#     df_shift = pd.merge(df_gemeten,df_simulatie,how="inner",left_index=True,right_index=True).copy()
#     # print(f"shift: {shift} ,abs std_dev: {abs(df_shift["Simulatie"]-df_shift["Gemeten"]).std():.1f},STD: {(df_shift["Simulatie"]-df_shift["Gemeten"]).std():.1f},abs verschil: {abs(df_shift["Simulatie"]-df_shift["Gemeten"]).sum():.0f} ")
#     # Eerste 1000 RIJEN
#     #df = df.iloc[10000:11000]
#     df_1h = df_shift[["Simulatie","Gemeten"]].resample("1h").sum()
    
#     print(f"shift: {shift} ,abs std_dev: {abs(df_1h["Simulatie"]-df_1h["Gemeten"]).std():.1f},STD: {(df_1h["Simulatie"]-df_1h["Gemeten"]).std():.1f},abs verschil: {abs(df_1h["Simulatie"]-df_1h["Gemeten"]).sum():.0f} ")

#     #print(f"uur waardes: {(df_1h["Simulatie"]-df_1h["Gemeten"]).describe()}")
#     sommen = df_1h[["Simulatie","Gemeten"]].sum()
#     #print(f"Percentage verschil: Simulatie is {(sommen['Simulatie']-sommen['Gemeten'])/(sommen['Gemeten'])*100:.1f}% hoger tov Gemeten")

#     df_daily = df_1h[["Simulatie","Gemeten"]].resample("1D").sum()

#     df_daily["Verschil"] = df_daily["Simulatie"] - df_daily["Gemeten"]
#     df_daily["%verschil"] = df_daily["Verschil"]/df_daily["Gemeten"]*100
  
    #print(f"Het verschil per dag: {df_daily['%verschil'].describe()}")

df_1h = df[["Simulatie","Gemeten"]].resample("1h").sum()
plt.figure(figsize=(14, 6))

plt.plot(
    df_1h.index,
    df_1h["Simulatie"],
    label="Simulatie",
    marker="o"
)

plt.plot(
    df_1h.index,
    df_1h["Gemeten"],
    label="Gemeten",
    marker="o",

)

plt.xlabel("Datum")
plt.ylabel("Energie (kWh)")
plt.legend()
plt.tight_layout()
plt.show()