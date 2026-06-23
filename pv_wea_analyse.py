import arcpy
from arcpy.sa import *

arcpy.CheckOutExtension("Spatial")

# --- user input ------------------------------------------------------------------------------
gdb_daten = r"C:\Melli\Projektphase\Ausgangsdaten\Ausgangsdaten\Ausgangsdaten.gdb"
gdb_zwischenergebnis = r"C:\Melli\Projektphase\Ausgangsdaten\Ausgangsdaten\Zwischenergebnisse.gdb"
dom = fr"{gdb_daten}\Digitales_Oberflaechenmodell"
wea = fr"{gdb_daten}\WEA_Punkte"
pv = fr"{gdb_daten}\Solar_Park_Polygon"
untersuchungsgebiet = fr"{gdb_daten}\Untersuchungsgebiet"
wind_or_solar = "solar"

# --- Environmental settings --------------------------------------------------------------------------

arcpy.env.overwriteOutput = True
arcpy.env.cellSize = 2
arcpy.env.extent = dom
arcpy.env.mask = dom
arcpy.env.workspace = "memory"

arcpy.management.Delete("memory")

# --- funktionen ----------- --------------------------------------------------------------------------

# --- Sichtanalyse Erneuerbare Energien
# Limit: ab wie viel Prozent ist es eine Beeinträchtigung?
def sichtanalyse(oberflaechenmodell, park_punktelayer, hoehe, limit):
    hoehe_angepasst = hoehe - (hoehe/100 * limit)
    view = Viewshed2(oberflaechenmodell, park_punktelayer, surface_offset="1.6 METERS", observer_offset=f"{hoehe_angepasst} METERS")
    return view


# --- Polygon in Punktelayer
def polygon_to_point(polygon, abstand=100, cellsize=100):
    # 1. Stützpunkte des Polygons
    arcpy.management.FeatureVerticesToPoints(polygon, "stuetzpunkte", "ALL")

    # 2. Punkte entlang der Außenkanten
    arcpy.management.GeneratePointsAlongLines(polygon, "kantenpunkte", "DISTANCE", Distance=f"{abstand} Meters")

    # 3. Rasterpunkte im Inneren
    arcpy.conversion.PolygonToRaster(polygon, "OBJECTID", "pv_raster", cellsize=cellsize)
    arcpy.conversion.RasterToPoint("pv_raster", "rasterpunkte")

    # 4. Alle zusammenführen
    arcpy.management.Merge(["stuetzpunkte", "kantenpunkte", "rasterpunkte"], "pv_punkte")

    # 5. Duplikate entfernen
    arcpy.management.DeleteIdentical("pv_punkte", ["Shape"])

    return "pv_punkte"

# --- Feature class mit Ringpolygonen erstellen
# 1. Puffer erstellen in bestimmten Abständen (buffer)
# 2. Puffer auf Untersuchungsgebiet zuschneiden (clip)
# 3. Das Innere der Puffer entfernen (erase)
# 4. Ringe zusammenführen in eine fc (merge)
# 5. ID erstellen mit Pufferdistanz als Integer (zur weiteren Verwendung in zonaler Statistik)
def puffer_berechnen(park_polygon, untersuchungsgebiet):
    list_buffer = [1000, 2000, 3000, 4000, 5000, 6000]

    prev_buffer = None
    for value in list_buffer:
        arcpy.analysis.PairwiseBuffer(park_polygon, f"buffer_{value}", f"{value} METERS", method="GEODESIC")
        clipped = f"buffer_{value}_clipped"
        arcpy.analysis.PairwiseClip(f"buffer_{value}", untersuchungsgebiet, clipped)
        if prev_buffer is not None:
            arcpy.analysis.PairwiseErase(clipped, prev_buffer, f"ring_{value}")
        else:
            arcpy.management.CopyFeatures(clipped, f"ring_{value}")

        prev_buffer = clipped

    rings = [f"ring_{value}" for value in list_buffer]
    arcpy.management.Merge(rings, "rings_merged")

    arcpy.management.AddField("rings_merged", "ring_id", "INTEGER")
    arcpy.management.CalculateField("rings_merged", "ring_id", "!BUFF_DIST!")

    return "rings_merged"

# --- Main Code -----------------------------------------------------------------------------------------
# --- Windpark ---
if wind_or_solar == "wind":
    # viewshed erstellen
    pfad_viewshed_wind = rf"{gdb_zwischenergebnis}\viewshed_wind"
    if not arcpy.Exists(pfad_viewshed_wind):
        viewshed_wind = sichtanalyse(dom, wea, 240, 10)
        viewshed_wind.save(pfad_viewshed_wind)
        print("Viewshed für Windpark erstellt.")
    else:
        print("Viewshed für Windpark existiert bereits.")

    # Polygon für Windparks erstellen
    arcpy.management.MinimumBoundingGeometry(wea, "wea_polygon", "CONVEX_HULL")

    # Puffer
    fc_ringe = puffer_berechnen("wea_polygon", untersuchungsgebiet)

    # binäres Raster berechnen
    viewshed_binary = Con(IsNull(Raster(pfad_viewshed_wind)), 0, 1)
    # von wo sieht man mehr als 2 Windräder?
    # viewshed_binary = Con((IsNull(viewshed)) | (viewshed < 3), 0, 1)
    if not arcpy.Exists(rf"{gdb_zwischenergebnis}\viewshed_wind_binary"):
        viewshed_binary.save(fr"{gdb_zwischenergebnis}\viewshed_wind_binary")
        print("Binäre viewshed für Windpark erstellt.")
    else:
        print("Binäre viewshed für Windpark existiert bereits.")

# --- Solarpark ---
else:
    pv_punkte = polygon_to_point(pv)
    arcpy.management.CopyFeatures(pv_punkte, fr"{gdb_zwischenergebnis}\pv_punkte")

    pfad_viewshed_solar = rf"{gdb_zwischenergebnis}\viewshed_solar"
    if not arcpy.Exists(pfad_viewshed_solar):
        viewshed_solar = sichtanalyse(dom, pv_punkte, 5, 10)
        viewshed_solar.save(pfad_viewshed_solar)
        print("Viewshed für Solarpark erstellt.")
    else:
        print("Viewshed für Solarpark existiert bereits.")

    fc_ringe = puffer_berechnen(pv, untersuchungsgebiet)

    # binäres Raster berechnen
    # von wo sieht man mind. 10% der PV Anlage?
    punkt_anzahl = int(arcpy.management.GetCount(pv_punkte)[0])
    schwellenwert = punkt_anzahl * 0.1
    viewshed_binary = Con((IsNull(Raster(pfad_viewshed_solar))) | (Raster(pfad_viewshed_solar) < schwellenwert), 0, 1)

    if not arcpy.Exists(rf"{gdb_zwischenergebnis}\viewshed_solar_binary"):
        viewshed_binary.save(fr"{gdb_zwischenergebnis}\viewshed_solar_binary")
        print(f"Binäre viewshed für PV erstellt")
    else:
        print("Binäre viewshed für PV existiert bereits.")

table_buffer_analysis = ZonalStatisticsAsTable(fc_ringe, "ring_id", viewshed_binary, "buffer_analysis",
                                                statistics_type="MEAN")

# MEAN an rings_merged joinen
arcpy.management.JoinField(
    fc_ringe,
    "ring_id",
    table_buffer_analysis,
    "ring_id",
    [f"MEAN"])

# feature class speichern
arcpy.management.CopyFeatures(fc_ringe, fr"{gdb_zwischenergebnis}\ringdistanzanalyse_{wind_or_solar}")
