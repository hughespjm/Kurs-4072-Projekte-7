import arcpy
from arcpy.sa import *
import os

arcpy.CheckOutExtension("Spatial")

# --- user input ------------------------------------------------------------------------------

park = arcpy.GetParameterAsText(0) # Feature Layer, polygon für Solar, punkte für Windpark
wind_or_solar = arcpy.GetParameterAsText(1) # string ("wind", "solar")
anlagenhoehe = arcpy.GetParameterAsText(2) # integer
anteil_sichtbar = arcpy.GetParameterAsText(3) # integer
untersuchungsgebiet = arcpy.GetParameterAsText(4) # Feature Layer, Polygon
zu_analysierende_layer = arcpy.GetParameterAsText(5) # Feature Layer, mehrere
dom = arcpy.GetParameterAsText(6) # Oberflaechenmodell, Raster-Layer
dgm = arcpy.GetParameterAsText(7) # Gelaendemodell, Raster-Layer
gdb_ergebnis = arcpy.GetParameterAsText(8)  #gdb in der die Ergebnisse gespeichert werden (workspace)

# --- Environmental settings --------------------------------------------------------------------------

arcpy.env.overwriteOutput = True
arcpy.env.cellSize = 25
arcpy.env.extent = dom
arcpy.env.mask = dom
arcpy.env.workspace = "memory"

arcpy.management.Delete("memory")

# --- funktionen ----------- --------------------------------------------------------------------------

# --- Sichtanalyse Erneuerbare Energien
# Limit: ab wie viel Prozent ist es eine Beeinträchtigung?
def sichtanalyse(oberflaechenmodell, gelaendemodell, park_punktelayer, hoehe, limit):
    # DGM-Wert extrahieren
    arcpy.sa.ExtractValuesToPoints(park_punktelayer, gelaendemodell, "park_mit_dgm")
    arcpy.management.AddField("park_mit_dgm", "dgm_wert", "DOUBLE")
    arcpy.management.CalculateField("park_mit_dgm", "dgm_wert", "!RASTERVALU!")
    arcpy.management.DeleteField("park_mit_dgm", "RASTERVALU")
    # DOM-Wert extrahieren
    arcpy.sa.ExtractValuesToPoints("park_mit_dgm", oberflaechenmodell, "park_mit_dom")
    arcpy.management.AddField("park_mit_dom", "dom_wert", "DOUBLE")
    arcpy.management.CalculateField("park_mit_dom", "dom_wert", "!RASTERVALU!")

    hoehe_angepasst = hoehe - (hoehe/100 * limit)
    arcpy.management.AddField("park_mit_dom", "hoehe_dgm_based", "DOUBLE")

    with arcpy.da.UpdateCursor("park_mit_dom", ["dgm_wert", "dom_wert", "hoehe_dgm_based"]) as cursor:
        for row in cursor:
            dgm_value = row[0]
            dom_value = row[1]
            row[2] = hoehe_angepasst - (dom_value - dgm_value) # individuell pro Windrad
            cursor.updateRow(row)
    view = Viewshed2(oberflaechenmodell, "park_mit_dom", surface_offset="1.6 METERS", observer_offset="hoehe_dgm_based")
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

def zonale_analyse (polygon, area, binary_raster):
    polygon_name = os.path.basename(polygon)
    clipped_polygon = fr"{gdb_ergebnis}\{polygon_name}"
    arcpy.analysis.Clip(polygon, area, clipped_polygon)

    # wie viel Prozent des polygons sind visuell vom Park betroffen?
    table_analysis = ZonalStatisticsAsTable(clipped_polygon, "OBJECTID", binary_raster, "zonale_analysis",
                                                    statistics_type="MEAN")
    arcpy.management.JoinField(
        clipped_polygon,
        "OBJECTID",
        table_analysis,
        "OBJECTID",
        [f"MEAN"])


# --- Main Code -----------------------------------------------------------------------------------------
# --- Windpark ---
if wind_or_solar == "wind":
    # viewshed erstellen
    viewshed_wND = sichtanalyse(dom, dgm, park, float(anlagenhoehe), int(anteil_sichtbar))
    viewshed = Con(IsNull(viewshed_wND), 0, viewshed_wND)

    pfad_viewshed_wind = rf"{gdb_ergebnis}\viewshed_wind"
    if not arcpy.Exists(pfad_viewshed_wind):
        viewshed.save(pfad_viewshed_wind)
        print("Viewshed für Windpark erstellt.")
    else:
        print("Viewshed für Windpark existiert bereits.")

    # binäres Raster berechnen
    viewshed_binary = Con(Raster(pfad_viewshed_wind) == 0, 0, 1)

    if not arcpy.Exists(rf"{gdb_ergebnis}\viewshed_wind_binary"):
        viewshed_binary.save(fr"{gdb_ergebnis}\viewshed_wind_binary")
        print("Binäre viewshed für Windpark erstellt.")
    else:
        print("Binäre viewshed für Windpark existiert bereits.")

    # Polygon für Windparks erstellen
    arcpy.management.MinimumBoundingGeometry(park, "wea_polygon", "CONVEX_HULL")

    # Puffer
    fc_ringe = puffer_berechnen("wea_polygon", untersuchungsgebiet)


# --- Solarpark ---
else:
    pv_punkte = polygon_to_point(park)

    viewshed = sichtanalyse(dom, dgm, pv_punkte, float(anlagenhoehe), int(anteil_sichtbar))

    pfad_viewshed_solar = rf"{gdb_ergebnis}\viewshed_solar"
    if not arcpy.Exists(pfad_viewshed_solar):
        viewshed.save(pfad_viewshed_solar)
        print("Viewshed für Solarpark erstellt.")
    else:
        print("Viewshed für Solarpark existiert bereits.")

    # binäres Raster berechnen
    # von wo sieht man mind. 10% der PV Anlage?
    punkt_anzahl = int(arcpy.management.GetCount(pv_punkte)[0])
    schwellenwert = punkt_anzahl * 0.1
    viewshed_binary = Con((IsNull(Raster(pfad_viewshed_solar))) | (Raster(pfad_viewshed_solar) < schwellenwert), 0,
                              1)

    if not arcpy.Exists(rf"{gdb_ergebnis}\viewshed_solar_binary"):
        viewshed_binary.save(fr"{gdb_ergebnis}\viewshed_solar_binary")
        print(f"Binäre viewshed für PV erstellt")
    else:
        print("Binäre viewshed für PV existiert bereits.")

    fc_ringe = puffer_berechnen(park, untersuchungsgebiet)


# Analyse der Ringzonen
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
arcpy.management.CopyFeatures(fc_ringe, fr"{gdb_ergebnis}\ringdistanzanalyse_{wind_or_solar}")

layer_liste = zu_analysierende_layer.split(";")
for layer in layer_liste:
    zonale_analyse(layer, untersuchungsgebiet, viewshed_binary)


