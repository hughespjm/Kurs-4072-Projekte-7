import re                      # for parsing the .meta4 XML and tile filenames with regex
import os                      # filesystem paths, directories, listing files
import ssl                     # to build a secure SSL context for HTTPS downloads
import hashlib                 # for SHA-256 hash verification of downloaded files
import urllib.request          # standard library HTTP(S) client used for all downloads
import certifi                 # provides an up-to-date CA bundle for the SSL context
import arcpy                   # ArcGIS Pro's Python API for geoprocessing

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# Per-product output settings.
PRODUCTS = {
    "dgm1": {
        "output_dir": r"C:\Patrick\Projekte\dgm1_downloads",   # where individual DGM1 tiles get saved
        "merged_name": "dgm1_merged.tif",                      # filename of the final mosaicked raster
    },
    "dom1": {
        "output_dir": r"C:\Patrick\Projekte\dom1_downloads",   # where individual DOM1 tiles get saved
        "merged_name": "dom1_merged.tif",                      # filename of the final mosaicked raster
    },
}

MERGED_OUTPUT_FOLDER = r"C:\Patrick\Projekte\merged"            # destination folder for the mosaicked rasters
METALINK_CACHE_FOLDER = r"C:\Patrick\Projekte\metalinks"        # local cache for downloaded .meta4 files
INPUT_GDB = r"C:\Patrick\Projekte\Ausgangsdaten\Ausgangsdaten\Ausgangsdaten.gdb"   # geodatabase holding all validated input layers
STUDY_AREA = os.path.join(INPUT_GDB, "Untersuchungsgebiet")     # the study area polygon feature class

# Landkreise are now a manually-imported, pre-validated layer in the gdb
# (CRS defined as EPSG:25832 and reprojected as needed during import, then
# checked by Datenvalidierung.py same as every other input layer) instead
# of being downloaded and CRS-defined live on every run. This removes the
# dependency on the legacy http:// WFS endpoint from the main pipeline.
LANDKREISE_FC = os.path.join(INPUT_GDB, "Landkreis_RLP")

# Shapefile/feature class field holding the Kreisschlüssel. "ags" is the
# 8-digit Amtlicher Gemeindeschlüssel; its first 5 digits ARE the
# Kreisschlüssel, so this is more reliable than "kreissch", which (in this
# dataset) lacks the leading "07" Bundesland prefix.
KREISSCHLUESSEL_FIELD = "ags"

# Template for building the geobasis-rlp.de metalink download URL per
# product + Kreisschlüssel, e.g. dgm1_tif_07137.meta4
METALINK_URL_TEMPLATE = "https://geobasis-rlp.de/data/{product}/current/meta4/{product}_tif_{code}.meta4"

# RLP DGM1/DOM1 tiles follow the standard AdV naming scheme:
#   <product>_32_<easting_km>_<northing_km>_1_rp.tif
# where easting_km/northing_km are the lower-left corner of a 1km x 1km
# tile, in km, in UTM zone 32N (EPSG:25832). E.g. dgm1_32_416_5524_1_rp.tif
# covers easting [416000, 417000], northing [5524000, 5525000].
TILE_NAME_PATTERN = re.compile(r"_32_(\d+)_(\d+)_1_")
TILE_SIZE_M = 1000   # tile edge length in meters
TILE_CRS_EPSG = 25832   # CRS the tile coordinates in the filename are expressed in

for folder in (MERGED_OUTPUT_FOLDER, METALINK_CACHE_FOLDER):   # make sure both working folders exist before anything runs
    if not os.path.exists(folder):
        os.makedirs(folder)                                    # create the folder (and any missing parents) if needed

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())   # shared SSL context using certifi's trusted CA bundle


# ---------------------------------------------------------------------------
# STUDY-AREA -> LANDKREISE -> METALINK URLS
# ---------------------------------------------------------------------------

def get_intersecting_kreisschluessel(study_area, landkreise_fc, code_field=KREISSCHLUESSEL_FIELD):   # finds which Landkreise the study area overlaps
    if not arcpy.Exists(landkreise_fc):   # Landkreise is now a manually-imported layer — fail loudly and early if it's missing
        raise RuntimeError(
            f"Landkreise layer not found at {landkreise_fc}. "
            "Import it manually into the gdb (see Datenvalidierung.py LAYERS) before running this script."
        )

    layer_name = "landkreise_lyr"                                       # temp in-memory layer name used for the selection
    if arcpy.Exists(layer_name):                                        # clean up a leftover layer from a previous failed run
        arcpy.management.Delete(layer_name)

    arcpy.management.MakeFeatureLayer(landkreise_fc, layer_name)        # wrap the Landkreise feature class in a selectable layer
    arcpy.management.SelectLayerByLocation(
        in_layer=layer_name,
        overlap_type="INTERSECT",                                       # select Landkreise polygons that intersect the study area
        select_features=study_area,
        selection_type="NEW_SELECTION",
    )

    codes = set()                                                        # use a set to automatically dedupe codes
    with arcpy.da.SearchCursor(layer_name, [code_field]) as cursor:      # iterate only over the currently selected (intersecting) features
        for (code,) in cursor:
            codes.add(str(code).zfill(8)[:5])                            # ags is 8 digits; the first 5 digits are the Kreisschlüssel

    arcpy.management.Delete(layer_name)                                  # clean up the temp layer

    print(f"Study area intersects {len(codes)} Landkreis(e): {sorted(codes)}")
    return sorted(codes)                                                  # sorted list for deterministic, readable output


def build_metalink_urls(product, codes):                                  # builds the full metalink URLs for one product + a list of Kreisschlüssel codes
    return [METALINK_URL_TEMPLATE.format(product=product, code=code) for code in codes]


# ---------------------------------------------------------------------------
# METALINK DOWNLOAD + PARSE
# ---------------------------------------------------------------------------

def download_metalink(url, cache_dir):                                     # downloads one .meta4 metalink file to a local cache folder
    filename = os.path.basename(url)                                       # derive the local filename from the URL itself
    dest_path = os.path.join(cache_dir, filename)

    print(f"  Fetching metalink: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})   # avoids 403s some RLP endpoints return to the default Python UA
    with urllib.request.urlopen(req, context=SSL_CONTEXT) as response, open(dest_path, "wb") as out_file:   # this endpoint IS https, so use SSL_CONTEXT
        out_file.write(response.read())

    return dest_path                                                         # caller uses this local path to parse the metalink


def parse_metalink(path):                                                    # extracts (filename, download url, sha256) tuples from a .meta4 file
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    files = []
    for block in re.findall(r'<file name="([^"]+)">(.*?)</file>', content, re.DOTALL):   # one regex match per <file> entry in the metalink XML
        name, body = block
        hash_match = re.search(r'<hash type="sha-256">([a-fA-F0-9]+)</hash>', body)       # SHA-256 checksum, if present
        url_match = re.search(r'<url>([^<]+)</url>', body)                                # actual download URL for this file
        sha256 = hash_match.group(1) if hash_match else None
        url = url_match.group(1).strip() if url_match else None
        if url:                                                                            # only keep entries that actually have a download URL
            files.append((name, url, sha256))
    return files


def verify_sha256(filepath, expected_hash):                                   # checks a downloaded file's hash against the metalink's expected hash
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):                         # read in 8 KB chunks to avoid loading huge files into memory at once
            sha256.update(chunk)
    return sha256.hexdigest().lower() == expected_hash.lower()                # case-insensitive comparison


def download_file(url, dest_path):                                            # downloads a single raster (.tif) file from a direct URL
    print(f"  Downloading: {url}")
    with urllib.request.urlopen(url, context=SSL_CONTEXT) as response, open(dest_path, "wb") as out_file:
        out_file.write(response.read())


# ---------------------------------------------------------------------------
# TILE-EXTENT PRE-FILTERING (filename-based, no download required)
# ---------------------------------------------------------------------------

def tile_extent_from_filename(name):                                          # builds an arcpy Polygon for a tile from its filename, or None if unparseable
    match = TILE_NAME_PATTERN.search(name)
    if not match:
        return None                                                            # unrecognized naming pattern — caller decides how to handle this

    east_km, north_km = (int(g) for g in match.groups())
    xmin, ymin = east_km * TILE_SIZE_M, north_km * TILE_SIZE_M
    xmax, ymax = xmin + TILE_SIZE_M, ymin + TILE_SIZE_M

    sr = arcpy.SpatialReference(TILE_CRS_EPSG)
    return arcpy.Polygon(
        arcpy.Array([
            arcpy.Point(xmin, ymin),
            arcpy.Point(xmin, ymax),
            arcpy.Point(xmax, ymax),
            arcpy.Point(xmax, ymin),
            arcpy.Point(xmin, ymin),                                            # close the ring back at the starting point
        ]),
        sr
    )


def tile_overlaps_study_area(name, study_area_lyr):                            # True/False/None (None = couldn't determine, caller should fail open)
    extent_polygon = tile_extent_from_filename(name)
    if extent_polygon is None:
        return None                                                             # filename didn't match the expected pattern

    arcpy.management.SelectLayerByLocation(
        in_layer=study_area_lyr,
        overlap_type="INTERSECT",
        select_features=extent_polygon,
        selection_type="NEW_SELECTION",
    )
    return int(arcpy.management.GetCount(study_area_lyr).getOutput(0)) > 0


# ---------------------------------------------------------------------------
# DOWNLOAD PIPELINE (now filters tiles BEFORE downloading them)
# ---------------------------------------------------------------------------

def download_all(metalink_urls, output_dir):                                   # full download pipeline: fetch metalinks, then only the overlapping files they list
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    study_lyr_name = "study_area_prefilter_lyr"                                # one reusable selectable layer for all tile-overlap checks in this run
    if arcpy.Exists(study_lyr_name):
        arcpy.management.Delete(study_lyr_name)
    arcpy.management.MakeFeatureLayer(STUDY_AREA, study_lyr_name)

    ok, failed, skipped = [], [], []                                           # track which filenames succeeded, failed, or were skipped as non-overlapping

    try:
        for metalink_url in metalink_urls:                                    # one metalink per intersecting Landkreis
            try:
                metalink_file = download_metalink(metalink_url, METALINK_CACHE_FOLDER)   # download the .meta4 itself first
            except Exception as e:
                print(f"  ERROR fetching metalink {metalink_url}: {e}")        # e.g. 404 if the Kreisschlüssel code is wrong/unavailable
                continue                                                        # skip this Kreis, keep processing the rest

            print(f"\n===== Processing: {os.path.basename(metalink_file)} =====")
            files = parse_metalink(metalink_file)                               # list of (name, url, sha256) entries inside this metalink
            print(f"Found {len(files)} files in metalink.\n")

            for name, url, expected_hash in files:
                # Only consider .tif rasters for the overlap pre-filter — metalinks
                # also list sidecar files (.tfw, .prj, .xml etc.) with the same
                # tile coordinates, which we still want alongside a kept .tif.
                if name.lower().endswith(".tif"):
                    overlaps = tile_overlaps_study_area(name, study_lyr_name)
                    if overlaps is False:
                        print(f"[{name}] ✗ No overlap (from filename) — skipping download")
                        skipped.append(name)
                        continue
                    elif overlaps is None:
                        print(f"[{name}]  WARNING: filename didn't match expected tile pattern — downloading to be safe")

                dest = os.path.join(output_dir, name)
                print(f"[{name}]")

                if os.path.exists(dest):                                        # skip re-downloading if the file is already on disk
                    print("  Already exists locally, checking hash...")
                else:
                    try:
                        download_file(url, dest)                                # actually download the raster file
                    except Exception as e:
                        print(f"  ERROR downloading: {e}")
                        failed.append(name)
                        continue                                                 # don't try to hash-check a file that failed to download

                if expected_hash:                                                # only verify if the metalink actually provided a hash
                    if verify_sha256(dest, expected_hash):
                        print("  ✓ Hash OK")
                        ok.append(name)
                    else:
                        print("  ✗ HASH MISMATCH — file may be corrupt")
                        failed.append(name)
                else:
                    print("  (no hash provided to verify)")
                    ok.append(name)                                              # treat as OK since there's nothing to verify against
                print()
    finally:
        if arcpy.Exists(study_lyr_name):                                         # always clean up the temp layer, even on error
            arcpy.management.Delete(study_lyr_name)

    print("----- Download Summary -----")
    print(f"Succeeded: {len(ok)}")
    print(f"Skipped (no overlap): {len(skipped)}")
    print(f"Failed:    {len(failed)}")
    if failed:
        print("Failed files:", failed)

    return ok                                                                     # only the verified, overlapping filenames go on to merge


# ---------------------------------------------------------------------------
# MERGE
# ---------------------------------------------------------------------------

def merge_rasters(verified_filenames, output_dir, merged_name):                      # mosaics all downloaded tiles into one merged raster
    if not verified_filenames:
        print("No verified rasters to merge — skipping mosaic step.")
        return

    print("\n----- Merging rasters -----")
    full_paths = [os.path.join(output_dir, name) for name in verified_filenames]      # build full paths from filenames + output_dir

    try:
        arcpy.management.MosaicToNewRaster(
            input_rasters=full_paths,
            output_location=MERGED_OUTPUT_FOLDER,
            raster_dataset_name_with_extension=merged_name,
            coordinate_system_for_the_raster="",                                       # "" = use the first input raster's CRS
            pixel_type="32_BIT_FLOAT",                                                  # elevation data needs floating-point precision
            number_of_bands=1,                                                          # DGM1/DOM1 are single-band elevation rasters
            mosaic_method="FIRST"                                                       # in overlap areas, keep the first raster's pixel values
        )
        print(f"Merged raster written to: {os.path.join(MERGED_OUTPUT_FOLDER, merged_name)}")
    except arcpy.ExecuteError:
        print("ERROR during mosaic:")
        print(arcpy.GetMessages(2))                                                      # print arcpy's detailed error messages (severity 2 = errors)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    # 1. Figure out which Landkreise the (possibly changed) Untersuchungsgebiet
    #    currently falls into, using the validated, pre-imported Landkreise layer.
    kreis_codes = get_intersecting_kreisschluessel(STUDY_AREA, LANDKREISE_FC)             # list of Kreisschlüssel the study area overlaps

    if not kreis_codes:                                                                   # nothing intersected — likely a config/data problem
        print("No intersecting Landkreise found — check STUDY_AREA and KREISSCHLUESSEL_FIELD.")
        return

    # 2. Build the metalink URLs per product from those codes, then download
    #    only the tiles that actually overlap the study area (filtered by
    #    filename-derived extent, before any download happens), and mosaic.
    for product_name, config in PRODUCTS.items():                                         # repeat the whole pipeline once per product (dgm1, dom1)
        print(f"\n\n########## PROCESSING PRODUCT: {product_name.upper()} ##########")
        metalink_urls = build_metalink_urls(product_name, kreis_codes)                     # one metalink URL per intersecting Kreis
        verified_files = download_all(metalink_urls, config["output_dir"])                 # download + hash-verify only overlapping raster tiles
        merge_rasters(verified_files, config["output_dir"], config["merged_name"])         # mosaic the downloaded tiles into one raster


if __name__ == "__main__":                                                                  # only run main() when executed directly, not on import
    main()
