import arcpy
from arcpy.sa import *
arcpy.CheckOutExtension("Spatial")
import os

# --- Parameter ---
gdb_daten  = r"C:\Patrick\Projekte\Ausgangsdaten\Ausgangsdaten\Ausgangsdaten.gdb"
gdb_zwischenergebnis = r"C:\Patrick\Projekte\Ausgangsdaten\Ausgangsdaten\sichtfeld_landschaft.gdb"

# --- Layer Namen ---
WEA_Layer     = "WEA_Punkte"
DOM_Layer     = "Digitales_Oberflaechenmodell"
Clip_Boundary = "Untersuchungsgebiet"

# --- Viewshed Parameter ---
HOEHE = 200  # Gesamthöhe der Turbine in Metern
LIMIT = 10   # Mindestprozentsatz der Turbine der sichtbar sein muss

# --- Ausgabe Namen ---
Output_Frequency = "viewshed_wind"
Output_Binary    = "viewshed_wind_binary"

# -------------------------------------------------------

# Output-GDB bei jedem Start neu erstellen — stellt sicher dass keine alten Ergebnisse stören
if arcpy.Exists(gdb_zwischenergebnis):
    arcpy.management.Delete(gdb_zwischenergebnis)
arcpy.management.CreateFileGDB(os.path.dirname(gdb_zwischenergebnis), os.path.basename(gdb_zwischenergebnis))

# --- Umgebungseinstellungen ---
arcpy.env.overwriteOutput = True
arcpy.env.cellSize        = 25                                  # 25m Auflösung
arcpy.env.extent          = os.path.join(gdb_daten, DOM_Layer)  # Ausdehnung auf DOM setzen
arcpy.env.mask            = os.path.join(gdb_daten, DOM_Layer)  # Maske auf DOM setzen
arcpy.env.workspace       = "memory"                            # Zwischenergebnisse im Arbeitsspeicher

arcpy.management.Delete("memory")  # Arbeitsspeicher leeren bevor wir starten

print("\n--- Viewshed Landschaft gestartet ---\n")

DOM_path  = os.path.join(gdb_daten, DOM_Layer)
WEA_path  = os.path.join(gdb_daten, WEA_Layer)
Clip_path = os.path.join(gdb_daten, Clip_Boundary)

# --- Funktion: Sichtanalyse ---
def sichtanalyse(oberflaechenmodell, park_punktelayer, hoehe, limit):
    hoehe_angepasst = hoehe - (hoehe / 100 * limit)
    print(f"  Turbinenhöhe: {hoehe}m  |  Limit: {limit}%  |  Angepasste Beobachterhöhe: {hoehe_angepasst}m")
    view = Viewshed2(oberflaechenmodell, park_punktelayer, surface_offset="1.6 METERS", observer_offset=f"{hoehe_angepasst} METERS")
    return view

# --- Schritt 1: Viewshed2 ausführen ---
print("Schritt 1: Viewshed2 wird berechnet...")
print("  Dies kann einige Minuten dauern...\n")

pfad_viewshed = os.path.join(gdb_zwischenergebnis, Output_Frequency)

if not arcpy.Exists(pfad_viewshed):
    viewshed_wind = sichtanalyse(DOM_path, WEA_path, HOEHE, LIMIT)
    viewshed_wind.save(pfad_viewshed)
    print("  Viewshed für Windpark erstellt:", Output_Frequency)
else:
    print("  Viewshed bereits vorhanden — wird wiederverwendet")

# --- Schritt 2: Binary Raster ableiten ---
print("\nSchritt 2: Binary Raster wird abgeleitet...")

viewshed_binary = Con(IsNull(Raster(pfad_viewshed)), 0, 1)
viewshed_binary.save(os.path.join(gdb_zwischenergebnis, Output_Binary))
print("  Binary Raster gespeichert:", Output_Binary)

arcpy.CheckInExtension("Spatial")

print("\n--- Viewshed Landschaft abgeschlossen ---")
print(f"Ergebnisse in: {gdb_zwischenergebnis}")
print(f"  {Output_Frequency}  — wie viele Turbinen pro Zelle sichtbar (min. {LIMIT}% der Turbine)")
print(f"  {Output_Binary}     — ob mindestens eine Turbine sichtbar (1/0)")