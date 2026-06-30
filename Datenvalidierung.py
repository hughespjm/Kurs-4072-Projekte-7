import arcpy
import os

# --- Parameters ---
Input_gdb = r"C:\Patrick\Projekte\Ausgangsdaten\Ausgangsdaten\Ausgangsdaten.gdb"
Output_Report = r"C:\Patrick\Projekte\Ausgangsdaten\Ausgangsdaten\Datenvalidierung_Report.txt"

# --- Layer Namen ---
LAYERS = [
    "WEA_Punkte",
    "Solar_Park_Polygon",
    "Untersuchungsgebiet",
    "Umspannwerke",
    "Landstrassen_etc",
    "Camping",
    "Sport_Freizeit",
    "Naturdenkmale",
    "Siedlung_Wohnbau",
    "FFH",
    "Landschaftsschutzgebiet",
    "Digitales_Oberflaechenmodell",
    "Landkreis_RLP",  # manuell importierte RLP-Landkreisgrenzen (EPSG:25832 definiert, ggf. reprojiziert) -
                        # wird jetzt hier statt live im Download-Skript geprüft
]

# -------------------------------------------------------

arcpy.env.workspace = Input_gdb  # erspart bei jedem arcpy-Aufruf den vollen GDB-Pfad
Report = []

def report_print(mld):
    print(mld)
    Report.append(mld)

report_print("--- Datenvalidierung Report ---")

missing_layers = []  # wird für die Zusammenfassung gebraucht, statt den Report-Text später erneut durchsuchen zu müssen
null_geom_issues = {}  # gleicher Grund — Ergebnis pro Layer für die Zusammenfassung vorhalten

for layer in LAYERS:
    full_path = os.path.join(Input_gdb, layer)

    if not arcpy.Exists(full_path):  # verhindert einen Absturz, falls ein Layer fehlt oder umbenannt wurde
        report_print (f"[fehlend] {layer}\n")
        missing_layers.append(layer)
        continue

    desc = arcpy.Describe(full_path)
    sr = desc.spatialReference

    report_print(f"[OK] {layer}")
    report_print(f"  Typ  : {desc.dataType}")                               
    report_print(f"  CRS  : {sr.name}  (WKID  :  {sr.factoryCode})")        

    if desc.dataType == "RasterDataset":
        ras = arcpy.Raster(full_path)
        report_print(f"  Zellengroß  : {ras.meanCellWidth} x {ras.meanCellHeight}")  # falsche Zellgröße würde spätere Viewshed-Ergebnisse verfälschen
        report_print(f"  Extent  : {ras.extent}")
        report_print(f"  NoData  : {ras.noDataValue}")
    else:
        count = int(arcpy.management.GetCount(full_path).getOutput(0))
        report_print(f"  Feature count  : {count}")
        report_print(f"  Extent  : {desc.extent}")

        null_geom = 0
        with arcpy.da.SearchCursor(full_path, ["SHAPE@"]) as cursor:
            for row in cursor:
                if row[0] is None:
                    null_geom += 1
        if null_geom > 0:  # leere Geometrien würden nachfolgende Analysetools (z.B. Viewshed) zum Absturz bringen
            report_print(f"  [Warnung] Null-Geometries  : {null_geom}")
            null_geom_issues[layer] = null_geom

    report_print("")

# --- CRS-Konsistenzprüfung ---
report_print("--- CRS-Konsistenzprüfung ---\n")  # unterschiedliche KBS zwischen Layern würden räumliche Analysen verfälschen
wkids={}
for layer in LAYERS:
    full_path = os.path.join(Input_gdb, layer)
    if arcpy.Exists(full_path):
        sr = arcpy.Describe(full_path).spatialReference
        wkids[layer] = sr.factoryCode                                   

unique_crs = set(wkids.values())  # ein Set enthält nur einen Wert, wenn wirklich alle Layer denselben WKID haben
if len(unique_crs) == 1:
    report_print(f"Alle Layer verwenden denselben CRS-WKID: {list(unique_crs)[0]}   OK")
else:
    report_print("CRS-DISKREPANZ FESTGESTELLT:")
    for layer, wkid in wkids.items():
        report_print(f"  {layer:<35} WKID: {wkid}")                        

# --- WEA FIELD-INSPEKTION ---
report_print("\n=== WEA-Attributtabelle ===\n")  # prüfen ob z.B. Nabenhöhe als Attribut vorhanden ist, bevor das Viewshed-Skript darauf zugreift
wea_path = os.path.join(Input_gdb, "WEAs")
if arcpy.Exists(wea_path):
    fields = arcpy.ListFields(wea_path)
    for field in fields:
        report_print(f"  {field.name:<30} {field.type:<15}")
    report_print("")
    with arcpy.da.SearchCursor(wea_path, [field.name for field in fields]) as cursor:
        for i, row in enumerate(cursor):
            report_print(f"  Row {i+1}: {row}")
            if i >= 4:  # 5 Zeilen reichen zur Kontrolle, mehr wäre nur unnötig lang im Report
                break

# --- Zusammenfassung ---
report_print("\n--- Zusammenfassung ---\n")  # schneller Gesamtstatus am Ende, ohne den ganzen Report lesen zu müssen

checks_passed = True

if missing_layers:
    checks_passed = False
    report_print(f"[FEHLgeschlagen] Fehlende Layer: {', '.join(missing_layers)}")
else:
    report_print("[OK] Alle Layer vorhanden")

if null_geom_issues:
    checks_passed = False
    for layer, count in null_geom_issues.items():
        report_print(f"[FEHLgeschlagen] Null-Geometrien in {layer}: {count}")
else:
    report_print("[OK] Keine Null-Geometrien gefunden")

if len(unique_crs) == 1:
    report_print(f"[OK] CRS consistent (WKID {list(unique_crs)[0]})")
else:
    checks_passed = False
    report_print("[FEHLgeschlagen] CRS-Diskrepanz zwischen den Layern")

report_print("")
if checks_passed:
    report_print("ALLE PRÜFUNGEN ERFOLGREICH")
else:
    report_print("EINE ODER MEHRERE PRÜFUNGEN FEHLGESCHLAGEN – siehe oben")

# --- REPORT SCHREIBEN ---
with open(Output_Report, "w", encoding = "utf-8") as datei:  # Report dauerhaft speichern statt nur in der Konsole anzuzeigen
    datei.write("\n".join(Report))

print(f"\nReport in {Output_Report} geschrieben")
