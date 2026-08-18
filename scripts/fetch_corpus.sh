#!/bin/sh
# Fetch the read-only corpus served to the agent at /corpus. Data files
# only: provider README and documentation files are not kept (invariant 2).
# Re-runnable; each section skips work it has already done.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$REPO_DIR/volumes/corpus"
mkdir -p "$OUT"

echo "== sequences: OEIS stripped file"
mkdir -p "$OUT/sequences"
if [ ! -s "$OUT/sequences/stripped" ]; then
    curl -sSL --retry 3 --retry-all-errors -o "$OUT/sequences/stripped.gz" https://oeis.org/stripped.gz
    gunzip -f "$OUT/sequences/stripped.gz"
fi

echo "== sky: DE440s ephemeris and HYG star catalog"
mkdir -p "$OUT/sky"
[ -s "$OUT/sky/de440s.bsp" ] || curl -sSL --retry 3 --retry-all-errors -o "$OUT/sky/de440s.bsp" \
    https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de440s.bsp
[ -s "$OUT/sky/hygdata.csv" ] || curl -sSL --retry 3 --retry-all-errors -o "$OUT/sky/hygdata.csv" \
    https://github.com/astronexus/HYG-Database/raw/main/hyg/CURRENT/hygdata_v41.csv

echo "== writing: Unicode UCD and Noto fonts"
mkdir -p "$OUT/writing/ucd" "$OUT/writing/fonts"
if [ ! -s "$OUT/writing/ucd/UnicodeData.txt" ]; then
    curl -sSL --retry 3 --retry-all-errors -o /tmp/UCD.zip https://www.unicode.org/Public/UCD/latest/ucd/UCD.zip
    python3 -c "import zipfile; zipfile.ZipFile('/tmp/UCD.zip').extractall('$OUT/writing/ucd')"
    rm -f /tmp/UCD.zip "$OUT/writing/ucd/ReadMe.txt"
fi
if ! ls "$OUT/writing/fonts"/*.ttf >/dev/null 2>&1; then
    docker run --rm -v "$OUT/writing/fonts":/fonts debian:trixie-slim sh -c '
        apt-get update -qq >/dev/null
        cd /tmp && apt-get download -qq fonts-noto-core fonts-noto-mono >/dev/null
        for deb in *.deb; do dpkg-deb -x "$deb" ex; done
        find ex -name "*.ttf" -exec cp {} /fonts/ \;
        chown -R '"$(id -u):$(id -g)"' /fonts'
fi

echo "== chess: Syzygy 3-4-5 tablebases (~1 GB, resumable)"
mkdir -p "$OUT/chess/syzygy"
wget -q -e robots=off -c -r -np -nd -A '*.rtbw,*.rtbz' -P "$OUT/chess/syzygy" \
    https://tablebase.lichess.ovh/tables/standard/3-4-5-wdl/ \
    https://tablebase.lichess.ovh/tables/standard/3-4-5-dtz/
find "$OUT/chess/syzygy" -name 'robots.txt*' -delete 2>/dev/null || true

echo "== history: SILSO sunspot numbers"
mkdir -p "$OUT/history/sunspots"
for f in SN_d_tot_V2.0.csv SN_m_tot_V2.0.csv; do
    [ -s "$OUT/history/sunspots/$f" ] || curl -sSL --retry 3 --retry-all-errors \
        -o "$OUT/history/sunspots/$f" "https://www.sidc.be/SILSO/DATA/$f"
done

echo "== history: GHCN-Daily station metadata and a spread of years"
mkdir -p "$OUT/history/climate"
[ -s "$OUT/history/climate/ghcnd-stations.txt" ] || curl -sSL --retry 3 --retry-all-errors \
    -o "$OUT/history/climate/ghcnd-stations.txt" \
    https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt
# A spread across the record rather than consecutive recent years: early files
# are small because few stations existed, so 144 years of depth costs less than
# three consecutive modern years would. 394 MB measured.
for year in 1880 1900 1920 1940 1960 2024; do
    [ -s "$OUT/history/climate/$year.csv.gz" ] || curl -sSL --retry 3 --retry-all-errors \
        -o "$OUT/history/climate/$year.csv.gz" \
        "https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_year/$year.csv.gz"
done

echo "== history: global earthquake catalog"
mkdir -p "$OUT/history/quakes"
if [ ! -s "$OUT/history/quakes/quakes.csv" ]; then
    : > "$OUT/history/quakes/quakes.csv.part"
    year=1970
    while [ "$year" -le 2025 ]; do
        curl -sSL --retry 3 --retry-all-errors \
            "https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv&starttime=$year-01-01&endtime=$((year + 1))-01-01&minmagnitude=4.5&orderby=time-asc" \
            | { if [ "$year" -eq 1970 ]; then cat; else tail -n +2; fi; } \
            >> "$OUT/history/quakes/quakes.csv.part"
        year=$((year + 1))
    done
    mv "$OUT/history/quakes/quakes.csv.part" "$OUT/history/quakes/quakes.csv"
fi

echo "== place: GeoNames gazetteer and coastline"
mkdir -p "$OUT/place"
if [ ! -s "$OUT/place/cities500.txt" ]; then
    curl -sSL --retry 3 --retry-all-errors -o /tmp/cities500.zip \
        https://download.geonames.org/export/dump/cities500.zip
    python3 -c "import zipfile; zipfile.ZipFile('/tmp/cities500.zip').extractall('$OUT/place')"
    rm -f /tmp/cities500.zip "$OUT/place/readme.txt"
fi
[ -s "$OUT/place/ne_10m_coastline.geojson" ] || curl -sSL --retry 3 --retry-all-errors \
    -o "$OUT/place/ne_10m_coastline.geojson" \
    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson

echo "== sky: OpenNGC deep-sky catalog"
[ -s "$OUT/sky/NGC.csv" ] || curl -sSL --retry 3 --retry-all-errors -o "$OUT/sky/NGC.csv" \
    https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv

echo "== life: reference genome"
mkdir -p "$OUT/life"
[ -s "$OUT/life/yeast.fa.gz" ] || curl -sSL --retry 3 --retry-all-errors -o "$OUT/life/yeast.fa.gz" \
    https://ftp.ensembl.org/pub/current_fasta/saccharomyces_cerevisiae/dna/Saccharomyces_cerevisiae.R64-1-1.dna.toplevel.fa.gz

echo "== notation: kern score corpora"
mkdir -p "$OUT/notation"
fetch_kern() {
    name=$1; repo=$2; ref=$3
    [ -d "$OUT/notation/$name" ] && return 0
    tmp=$(mktemp -d)
    curl -sSL --retry 3 --retry-all-errors "https://codeload.github.com/$repo/tar.gz/$ref" \
        | tar xz -C "$tmp"
    mkdir -p "$OUT/notation/$name"
    # Notation only. The upstream tarballs also carry rendered PDFs, MIDI, and
    # README/txt notes: the prose and renderings go for invariant 2, the MIDI
    # goes because this wave ships music as notation and not as sound.
    # --parents keeps the repo-relative directory structure, so scores with the
    # same basename in different composer directories do not collide.
    (cd "$tmp"/*/ && find . -name '*.krn' -exec cp --parents {} "$OUT/notation/$name/" \;)
    rm -rf "$tmp"
}
fetch_kern bach-370-chorales craigsapp/bach-370-chorales          master
fetch_kern josquin           josquin-research-project/jrp-scores  main
fetch_kern beethoven-sonatas craigsapp/beethoven-piano-sonatas    main
fetch_kern mozart-sonatas    craigsapp/mozart-piano-sonatas       main
fetch_kern chopin-mazurkas   craigsapp/chopin-mazurkas            main
find "$OUT/notation" -type f ! -name '*.krn' -delete
find "$OUT/notation" -type d -empty -delete

echo "== place: ETOPO relief, subsampled to 5 arc-minute"
# The 60 arc-second original is 457 MB and essentially uncompressed, which
# costs more than the whole climate record for a static grid. Subsampling by
# 5 keeps every range, basin, and shelf at ~1/44th the bytes, and the result
# stays a real NetCDF-4 file with lat/lon coordinates rather than a bare
# array. Conversion runs on the host in a throwaway container, as the fonts
# step above already does.
if [ ! -s "$OUT/place/etopo_5min.nc" ]; then
    tmp=$(mktemp -d)
    curl -sSL --retry 3 --retry-all-errors -o "$tmp/etopo60s.nc" \
        https://www.ngdc.noaa.gov/thredds/fileServer/global/ETOPO2022/60s/60s_surface_elev_netcdf/ETOPO_2022_v1_60s_N90W180_surface.nc
    docker run --rm -v "$tmp":/w -v "$OUT/place":/out python:3.13-slim sh -c '
        pip install -q --no-cache-dir netCDF4
        python - <<PYCONV
import netCDF4
src = netCDF4.Dataset("/w/etopo60s.nc")
step = 5
z = src.variables["z"][::step, ::step]
lat = src.variables["lat"][::step]
lon = src.variables["lon"][::step]
dst = netCDF4.Dataset("/out/etopo_5min.nc", "w", format="NETCDF4")
dst.createDimension("lat", z.shape[0])
dst.createDimension("lon", z.shape[1])
vla = dst.createVariable("lat", "f4", ("lat",))
vlo = dst.createVariable("lon", "f4", ("lon",))
vz = dst.createVariable("z", "i2", ("lat", "lon"), zlib=True, complevel=6)
vla[:] = lat
vlo[:] = lon
vz[:, :] = z.astype("int16")
vla.units = "degrees_north"
vlo.units = "degrees_east"
vz.units = "meters"
dst.close()
PYCONV
        chown '"$(id -u):$(id -g)"' /out/etopo_5min.nc'
    rm -rf "$tmp"
fi

echo "== done"
du -sh "$OUT"/*
