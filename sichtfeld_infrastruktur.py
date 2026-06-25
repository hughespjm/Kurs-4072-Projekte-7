import arcpy
from arcpy.sa import *
arcpy.CheckOutExtension("Spatial")
import os

# --- Parameter ---
gdb_daten          = r"C:\Patrick\Projekte\Ausgangsdaten\Ausgangsdaten\Ausgangsdaten.gdb"
gdb_zwischenergebnis = r"C:\Patrick\Projekte\Ausgangsdaten\Ausgangsdaten\sichtfeld_infrastruktur.gdb"

# --- Layer Namen ---
WEA_Layer  = "WEA_Punkte"
DOM_Layer  = "Digitales_Oberflaechenmodell"

# --- Infrastruktur Layer ---
INFRA_LAYERS = [
    "Camping"
]

# --- Viewshed Parameter ---
HOEHE          = 200    # Gesamthöhe der Turbine in Metern
LIMIT          = 10     # Mindestprozentsatz der Turbine der sichtbar sein muss
GRID_ABSTAND   = 25     # Abstand der Beobachterpunkte im Raster in Metern — entspricht DOM-Auflösung
SURFACE_OFFSET = "1.6 METERS"  # Augenhöhe des Beobachters auf dem Boden

# -------------------------------------------------------

# Output-GDB bei jedem Start neu erstellen
if arcpy.Exists(gdb_zwischenergebnis):
    arcpy.management.Delete(gdb_zwischenergebnis)
arcpy.management.CreateFileGDB(os.path.dirname(gdb_zwischenergebnis), os.path.basename(gdb_zwischenergebnis))

# --- Umgebungseinstellungen ---
arcpy.env.overwriteOutput = True
arcpy.env.cellSize        = 25
arcpy.env.extent          = os.path.join(gdb_daten, DOM_Layer)
arcpy.env.mask            = os.path.join(gdb_daten, DOM_Layer)
arcpy.env.workspace       = "memory"

arcpy.management.Delete("memory")

print("\n--- Viewshed Infrastruktur gestartet ---\n")

DOM_path = os.path.join(gdb_daten, DOM_Layer)
WEA_path = os.path.join(gdb_daten, WEA_Layer)

# --- Funktion: Polygone in Rasterpunkte umwandeln ---
# Konvertiert ein Polygon in ein gleichmäßiges Punkteraster mit definiertem Abstand
# Grund: Viewshed2 benötigt Punktlayer als Beobachter — Polygone müssen erst in Punkte umgewandelt werden

def polygon_zu_punkten(polygon_path, abstand):
    name = os.path.basename(polygon_path)
    punkte_name = f"punkte_{name}"

    arcpy.conversion.PolygonToRaster(polygon_path, "OBJECTID", f"raster_{name}", cellsize=abstand)
    arcpy.conversion.RasterToPoint(f"raster_{name}", punkte_name)

    print(f"  Punkte erstellt für {name}: {int(arcpy.management.GetCount(punkte_name).getOutput(0))} Punkte")
    return punkte_name

# --- Funktion: Prozentsatz sichtbarer Turbinenhöhe berechnen ---
# AGL-Raster gibt für jede Zelle die minimale Höhe über dem Boden an ab der etwas sichtbar ist
# Beispiel: AGL = 50m bei einer 200m Turbine bedeutet die untersten 50m sind verdeckt
# Sichtbarer Anteil = (HOEHE - AGL) / HOEHE * 100
# Zellen wo AGL > HOEHE: Turbine komplett verdeckt → 0%
# Zellen wo AGL = 0: Turbine komplett sichtbar → 100%
def prozent_sichtbar(agl_raster, hoehe):
    # Con() setzt Zellen wo AGL größer als Turbinenhöhe auf 0 — Turbine komplett verdeckt
    # Alle anderen Zellen bekommen den berechneten Prozentwert
    prozent = Con(
        IsNull(agl_raster) | (agl_raster >= hoehe),
        0,
        ((hoehe - agl_raster) / hoehe) * 100
    )
    return prozent

# --- Hauptschleife: Jeden Infrastruktur-Layer verarbeiten ---
print(f"Verarbeite {len(INFRA_LAYERS)} Infrastruktur-Layer...\n")

for layer_name in INFRA_LAYERS:
    print(f"--- {layer_name} ---")
    layer_path = os.path.join(gdb_daten, layer_name)

    # Prüfen ob Layer existiert
    if not arcpy.Exists(layer_path):
        print(f"  [Warnung] Layer nicht gefunden: {layer_name} — wird übersprungen\n")
        continue

    # --- Schritt 1: Polygon in Beobachterpunkte umwandeln ---
    print(f"  Schritt 1: Beobachterpunkte erstellen...")
    beobachter_punkte = polygon_zu_punkten(layer_path, GRID_ABSTAND)

    # Prüfen ob Punkte erstellt wurden — leere Polygone überspringen
    anzahl_punkte = int(arcpy.management.GetCount(beobachter_punkte).getOutput(0))
    if anzahl_punkte == 0:
        print(f"  [Warnung] Keine Punkte erstellt für {layer_name} — wird übersprungen\n")
        continue

    # --- Schritt 2: Viewshed2 mit AGL-Ausgabe ---
    # observer_offset = angepasste Turbinenhöhe nach Limit-Berechnung
    # out_agl_raster = AGL-Raster wird hier benötigt — gibt minimale sichtbare Höhe pro Zelle zurück
    print(f"  Schritt 2: Viewshed2 mit AGL wird berechnet...")
    hoehe_angepasst = HOEHE - (HOEHE / 100 * LIMIT)
    agl_raster_name = f"agl_{layer_name}"

    viewshed_ergebnis = Viewshed2(
        in_raster=DOM_path,
        in_observer_features=beobachter_punkte,
        analysis_type="FREQUENCY",
        observer_offset=SURFACE_OFFSET,
        surface_offset="0 METERS",
        out_agl_raster=agl_raster_name
    )

    # Viewshed2 gibt mehrere Ausgaben zurück — AGL-Raster liegt im memory workspace
    # agl_raster_name wurde als Name übergeben — wir lesen es direkt aus dem memory workspace
    agl_result = Raster(agl_raster_name)

    # AGL-Raster speichern
    agl_result.save(os.path.join(gdb_zwischenergebnis, agl_raster_name))
    print(f"  AGL-Raster gespeichert: {agl_raster_name}")

    # --- Schritt 3: Prozentsatz sichtbarer Turbinenhöhe berechnen ---
    print(f"  Schritt 3: Prozentraster wird berechnet...")
    prozent_raster = prozent_sichtbar(agl_result, HOEHE)

    # Prozentraster speichern
    prozent_name = f"prozent_{layer_name}"
    prozent_raster.save(os.path.join(gdb_zwischenergebnis, prozent_name))
    print(f"  Prozentraster gespeichert: {prozent_name}")

    # Schritt 4: Statistik AM TURBINENSTANDORT, nicht am Camping-Polygon!
    print(f"  Schritt 4: Statistiktabelle wird erstellt...")
    tabelle_name = f"statistik_{layer_name}"

    ZonalStatisticsAsTable(
        in_zone_data=WEA_path,
        zone_field="OBJECTID",
        in_value_raster=prozent_raster,
        out_table=os.path.join(gdb_zwischenergebnis, tabelle_name),
        statistics_type="MEAN"
    )
    print(f"  Statistiktabelle gespeichert: {tabelle_name}\n")

arcpy.CheckInExtension("Spatial")

print("\n--- Viewshed Infrastruktur abgeschlossen ---")
print(f"Ergebnisse in: {gdb_zwischenergebnis}")
print(f"Pro Layer gespeichert:")
print(f"  agl_[layer]        — minimale sichtbare Höhe pro Zelle in Metern")
print(f"  prozent_[layer]    — Prozentsatz sichtbarer Turbinenhöhe pro Zelle")
print(f"  statistik_[layer]  — Tabelle mit Mittelwert, Std, Min, Max pro Polygon")
