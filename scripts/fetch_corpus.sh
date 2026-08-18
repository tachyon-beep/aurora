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

echo "== done"
du -sh "$OUT"/*
