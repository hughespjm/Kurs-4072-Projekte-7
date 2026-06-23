import arcpy
from arcpy.sa import *

arcpy.CheckOutExtension("Spatial")

gdb_daten = r"C:\Melli\Projektphase\Ausgangsdaten\Ausgangsdaten\Ausgangsdaten.gdb"
gdb_zwischenergebnis = r"C:\Melli\Projektphase\Ausgangsdaten\Ausgangsdaten\Zwischenergebnisse.gdb"
dom = fr"{gdb_daten}\Digitales_Oberflaechenmodell"
wea = fr"{gdb_daten}\WEA_Punkte"
untersuchungsgebiet = fr"{gdb_daten}\Untersuchungsgebiet"

# --- Environmental settings --------------------------------------------------------------------------

arcpy.env.overwriteOutput = True
arcpy.env.cellSize = 2
arcpy.env.extent = dom
arcpy.env.mask = dom
arcpy.env.workspace = "memory"

arcpy.management.Delete("memory")

# --- main code -----------------------------------------------------------------------------------------
# --- sichtanalyse durchführen --------------------------------------------------------------------------
def sichtanalyse(dom, wea):
    if not arcpy.Exists(rf"{gdb_zwischenergebnis}\viewshed"):
        # Assumption: the turbine is 240m high, and  if more than 10% of it is visible, it is considered a visual obstruction.
        view = Viewshed2(dom, wea, surface_offset="1.6 METERS", observer_offset="216 METERS")
        view.save(fr"{gdb_zwischenergebnis}\viewshed")
        print(f"Viewshed erstellt")
        return view
    else:
        print(f"viewshed existiert bereits")
        return Raster(rf"{gdb_zwischenergebnis}\viewshed")

viewshed = sichtanalyse(dom, wea)

# --- Sichtbarkeit in Abhängigkeit von der Distanz berechnen ----------------------------------------------

# Polygon für Windparks erstellen
arcpy.management.MinimumBoundingGeometry(wea, "wea_polygon", "CONVEX_HULL")

# Puffer berechnen 1000m Abstände, auf Untersuchungsgebiet clippen, inneres ausschneiden

list_buffer = [1000, 2000, 3000, 4000, 5000, 6000]

prev_buffer = None
for value in list_buffer:

    # Puffer berechnen
    arcpy.analysis.PairwiseBuffer("wea_polygon", f"buffer_{value}", f"{value} METERS", method="GEODESIC")

    # Zuschneiden auf Untersuchungsgebiet
    clipped = f"buffer_{value}_clipped"
    arcpy.analysis.PairwiseClip(f"buffer_{value}", untersuchungsgebiet, clipped)

    # Ringe ausstanzen
    if prev_buffer is not None:
        arcpy.analysis.PairwiseErase(clipped, prev_buffer, f"ring_{value}")
    else:
        arcpy.management.CopyFeatures(clipped, f"ring_{value}")

    prev_buffer = clipped

# Ringe zusammenführen
rings = [f"ring_{value}" for value in list_buffer]
arcpy.management.Merge(rings, "rings_merged")

arcpy.management.AddField("rings_merged", "ring_id", "INTEGER")
arcpy.management.CalculateField("rings_merged", "ring_id", "!BUFF_DIST!")

# binäres Raster berechnen
# von wo sieht man mind. 1 Windrad?
viewshed_binary = Con(IsNull(viewshed), 0, 1)
# von wo sieht man mehr als 2 Windräder?
#viewshed_binary = Con((IsNull(viewshed)) | (viewshed < 3), 0, 1)
if not arcpy.Exists(rf"{gdb_zwischenergebnis}\viewshed_binary"):
    viewshed_binary.save(fr"{gdb_zwischenergebnis}\viewshed_binary")
    print(f"Binäre viewshed erstellt")


table_buffer_analysis = ZonalStatisticsAsTable("rings_merged", "ring_id", viewshed_binary, "buffer_analysis", statistics_type="MEAN")

# MEAN an rings_merged joinen
arcpy.management.JoinField(
        "rings_merged",
        "ring_id",
        table_buffer_analysis,
        "ring_id",
        [f"MEAN"])

# feature class speichern
arcpy.management.CopyFeatures("rings_merged", fr"{gdb_zwischenergebnis}\ringdistanzanalyse")



