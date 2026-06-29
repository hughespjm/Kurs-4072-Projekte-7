# ---- import modules -----------------------------------------------------------------------

import arcpy
from arcpy.sa import *


# --- environment ---------------------------------------------------------------------------

arcpy.env.overwriteOutput = True
arcpy.env.cellSize = 25
arcpy.env.workspace = "memory"

arcpy.management.Delete("memory")

# --- input ------------------------------------------------------------------------------
pfad_gdb = r"C:\Melli\Projektphase\maerkisches_modell_daten.gdb"
windrad = rf"{pfad_gdb}\windrad"
dom = rf"{pfad_gdb}\Digitales_Oberflaechenmodell"
dgm = r"C:\Melli\Projektphase\dgm_merged\dgm1_merged.tif"
anlagenhoehe = 200
prozente = [20, 40, 60, 80, 100]
wald = fr"{pfad_gdb}\wald"
siedlung = fr"{pfad_gdb}\Siedlung_Wohnbau"


# DGM und DOM Werte am Windrad-Standort extrahieren und zur feature class hinzufügen
arcpy.sa.ExtractValuesToPoints(windrad, dgm, "windrad_dgm")
arcpy.management.AddField("windrad_dgm", "dgm_wert", "DOUBLE")
arcpy.management.CalculateField("windrad_dgm", "dgm_wert", "!RASTERVALU!")
arcpy.management.DeleteField("windrad_dgm", "RASTERVALU")

arcpy.sa.ExtractValuesToPoints("windrad_dgm", dom, "windrad_dom")
arcpy.management.AddField("windrad_dom", "dom_wert", "DOUBLE")
arcpy.management.CalculateField("windrad_dom", "dom_wert", "!RASTERVALU!")
arcpy.management.DeleteField("windrad_dom", "RASTERVALU")

# 5 Spalten hinzufügen und befüllen

for prozent in prozente:
    feldname = f"hoehe_{prozent}"
    arcpy.management.AddField("windrad_dom", feldname, "DOUBLE")
    hoehe_relativ = anlagenhoehe * (prozent / 100)
    # Vegetationshöhe wird pro Punkt individuell abgezogen
    arcpy.management.CalculateField(
        "windrad_dom",
        feldname,
        f"{hoehe_relativ} - (!dom_wert! - !dgm_wert!)"
    ) # nicht offset angeben sondern absolute höhe

cursor = arcpy.da.SearchCursor("windrad_dom", ["*"])
for line in cursor:
    print(line)

# Puffer um Windrad & extent setzen
buffer = arcpy.analysis.PairwiseBuffer(windrad, "buffer_2km", "2000 METERS")
arcpy.env.extent = arcpy.Describe(buffer).extent


# Bereiche ausschneiden bei denen DOM mehr als 1,57m über DGM liegt
# Differenz DOM - DGM
differenz = Raster(dom) - Raster(dgm)
abschirmung = Con(differenz > 1.57, 1)
arcpy.conversion.RasterToPolygon(abschirmung, "abschirmung_polygon", "NO_SIMPLIFY")

# Wald & Siedlung & nicht einsichtige Bereiche ausschneiden und Maske setzen
cutout = arcpy.management.Merge([wald, siedlung, "abschirmung_polygon"], "cutout")
area_filtered = arcpy.analysis.Erase(buffer, cutout, "area_filtered")
arcpy.env.mask = "area_filtered"

# distanzraster berechnen
distance_raster = EucDistance(windrad)


entfernungsklassen = Reclassify(
    in_raster        = distance_raster,
    reclass_field    = "Value",
    remap            = RemapRange([
        [0, 250, 1],
        [250, 500, 2],
        [500, 1000, 3],
        [1000, 2000, 4],
        [2000, 4000, "NODATA"]
    ])
)

entfernungsklassen.save(fr"{pfad_gdb}\entfernungsklassen_neu")

zonen_mapping = {
    1: "hoehe_20",
    2: "hoehe_40",
    3: "hoehe_60",
    4: "hoehe_80"
}

viewshed_ergebnisse = []

for zone_nr, hoehe_feld in zonen_mapping.items():
    arcpy.env.extent = arcpy.Describe(buffer).extent
    # Zone als Polygon extrahieren und Extent setzen
    zone_raster  = Con(Raster(fr"{pfad_gdb}\entfernungsklassen_neu") == zone_nr, 1)
    zone_polygon = fr"{pfad_gdb}\zone_{zone_nr}_polygon"
    arcpy.conversion.RasterToPolygon(zone_raster, zone_polygon, "NO_SIMPLIFY")
    arcpy.env.extent = arcpy.Describe(zone_polygon).extent

    # Viewshed berechnen
    viewshed_zone = Viewshed2(
        dom,
        "windrad_dom",
        surface_offset = 1.57,
        observer_offset=hoehe_feld
    )

    # Auf Zone maskieren (damit Ringe rauskommen und kein Rechteck) und speichern
    viewshed_maskiert = ExtractByMask(viewshed_zone, zone_polygon)
    viewshed_maskiert.save(fr"{pfad_gdb}\viewshed_zone_{zone_nr}")
    viewshed_ergebnisse.append(fr"{pfad_gdb}\viewshed_zone_{zone_nr}")

    print(f"Zone {zone_nr} fertig")

print(viewshed_ergebnisse)
# Alle Zonen zusammenführen
arcpy.management.MosaicToNewRaster(
    viewshed_ergebnisse,
    pfad_gdb,
    "viewshed_gesamt",
    pixel_type    = "8_BIT_UNSIGNED",
    number_of_bands=1,
    mosaic_method = "MAXIMUM"
)

print("Viewshed gesamt fertig")









