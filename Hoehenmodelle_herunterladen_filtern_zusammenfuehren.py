import re
import os
import ssl
import hashlib
import urllib.request
import certifi
import arcpy

PRODUCTS = {
    "dgm1": {
        "metalink_files": [
            r"C:\Patrick\Projekte\dgm1_tif_0713709.meta4",
            r"C:\Patrick\Projekte\dgm1_tif_0713702.meta4",
        ],
        "output_dir": r"C:\Patrick\Projekte\dgm1_downloads",
        "merged_name": "dgm1_merged.tif",
    },
    "dom1": {
        "metalink_files": [
            r"C:\Patrick\Projekte\dom1_tif_0713709.meta4",
            r"C:\Patrick\Projekte\dom1_tif_0713702.meta4",
        ],
        "output_dir": r"C:\Patrick\Projekte\dom1_downloads",
        "merged_name": "dom1_merged.tif",
    },
}

MERGED_OUTPUT_FOLDER = r"C:\Patrick\Projekte\merged"
STUDY_AREA = r"C:\Patrick\Projekte\Ausgangsdaten\Ausgangsdaten\Ausgangsdaten.gdb\Untersuchungsgebiet"

if not os.path.exists(MERGED_OUTPUT_FOLDER):
    os.makedirs(MERGED_OUTPUT_FOLDER)

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def parse_metalink(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    files = []
    for block in re.findall(r'<file name="([^"]+)">(.*?)</file>', content, re.DOTALL):
        name, body = block
        hash_match = re.search(r'<hash type="sha-256">([a-fA-F0-9]+)</hash>', body)
        url_match = re.search(r'<url>([^<]+)</url>', body)
        sha256 = hash_match.group(1) if hash_match else None
        url = url_match.group(1).strip() if url_match else None
        if url:
            files.append((name, url, sha256))
    return files


def verify_sha256(filepath, expected_hash):
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest().lower() == expected_hash.lower()


def download_file(url, dest_path):
    print(f"  Downloading: {url}")
    with urllib.request.urlopen(url, context=SSL_CONTEXT) as response, open(dest_path, "wb") as out_file:
        out_file.write(response.read())


def download_all(metalink_files, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    ok, failed = [], []

    for metalink_file in metalink_files:
        print(f"\n===== Processing: {os.path.basename(metalink_file)} =====")
        files = parse_metalink(metalink_file)
        print(f"Found {len(files)} files in metalink.\n")

        for name, url, expected_hash in files:
            dest = os.path.join(output_dir, name)
            print(f"[{name}]")

            if os.path.exists(dest):
                print("  Already exists locally, checking hash...")
            else:
                try:
                    download_file(url, dest)
                except Exception as e:
                    print(f"  ERROR downloading: {e}")
                    failed.append(name)
                    continue

            if expected_hash:
                if verify_sha256(dest, expected_hash):
                    print("  ✓ Hash OK")
                    ok.append(name)
                else:
                    print("  ✗ HASH MISMATCH — file may be corrupt")
                    failed.append(name)
            else:
                print("  (no hash provided to verify)")
                ok.append(name)
            print()

    print("----- Download Summary -----")
    print(f"Succeeded: {len(ok)}")
    print(f"Failed:    {len(failed)}")
    if failed:
        print("Failed files:", failed)

    return ok


def filter_by_study_area(verified_filenames, output_dir):
    print("\n----- Filtering by Untersuchungsgebiet -----")
    overlapping = []

    for name in verified_filenames:
        raster_path = os.path.join(output_dir, name)
        layer_name = f"study_lyr_{name}"
        try:
            desc = arcpy.Describe(raster_path)
            extent = desc.extent
            extent_polygon = arcpy.Polygon(
                arcpy.Array([
                    arcpy.Point(extent.XMin, extent.YMin),
                    arcpy.Point(extent.XMin, extent.YMax),
                    arcpy.Point(extent.XMax, extent.YMax),
                    arcpy.Point(extent.XMax, extent.YMin),
                    arcpy.Point(extent.XMin, extent.YMin),
                ]),
                desc.spatialReference
            )

            if arcpy.Exists(layer_name):
                arcpy.management.Delete(layer_name)

            intersects = arcpy.management.SelectLayerByLocation(
                in_layer=arcpy.management.MakeFeatureLayer(STUDY_AREA, layer_name),
                overlap_type="INTERSECT",
                select_features=extent_polygon,
                selection_type="NEW_SELECTION"
            )

            result = int(arcpy.management.GetCount(intersects).getOutput(0))

            if result > 0:
                print(f"  ✓ Overlaps — keeping:  {name}")
                overlapping.append(name)
            else:
                print(f"  ✗ No overlap — skipping: {name}")

        except Exception as e:
            print(f"  ERROR checking {name}: {e} — keeping it to be safe")
            overlapping.append(name)

        finally:
            if arcpy.Exists(layer_name):
                arcpy.management.Delete(layer_name)

    print(f"\nKept {len(overlapping)} of {len(verified_filenames)} rasters after spatial filter.")
    return overlapping


def merge_rasters(verified_filenames, output_dir, merged_name):
    if not verified_filenames:
        print("No verified rasters to merge — skipping mosaic step.")
        return

    print("\n----- Merging rasters -----")
    full_paths = [os.path.join(output_dir, name) for name in verified_filenames]

    try:
        arcpy.management.MosaicToNewRaster(
            input_rasters=full_paths,
            output_location=MERGED_OUTPUT_FOLDER,
            raster_dataset_name_with_extension=merged_name,
            coordinate_system_for_the_raster="",
            pixel_type="32_BIT_FLOAT",
            number_of_bands=1,
            mosaic_method="FIRST"
        )
        print(f"Merged raster written to: {os.path.join(MERGED_OUTPUT_FOLDER, merged_name)}")
    except arcpy.ExecuteError:
        print("ERROR during mosaic:")
        print(arcpy.GetMessages(2))


def main():
    for product_name, config in PRODUCTS.items():
        print(f"\n\n########## PROCESSING PRODUCT: {product_name.upper()} ##########")
        verified_files = download_all(config["metalink_files"], config["output_dir"])
        overlapping_files = filter_by_study_area(verified_files, config["output_dir"])
        merge_rasters(overlapping_files, config["output_dir"], config["merged_name"])


if __name__ == "__main__":
    main()