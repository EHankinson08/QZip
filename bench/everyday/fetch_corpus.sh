#!/bin/bash
# Download the public everyday corpus used in bench/everyday/RESULTS.md into ./corpus (about 420 MB on disk).
#   bash bench/everyday/fetch_corpus.sh [dir]     then: python3 bench/everyday/bench.py corpus/silesia/* corpus/*.* ...
# Sources: Silesia corpus (Univ. of Silesia), Canterbury corpus (Univ. of Canterbury), enwik8 (Matt Mahoney / Hutter
# Prize), GH Archive hourly events (gharchive.org, first 64 MiB, cut at a line end), Chinook and Northwind SQLite demo
# databases (GitHub: lerocha/chinook-database, jpwhite3/northwind-SQLite3), the "Attention Is All You Need" PDF (arXiv).
# The three large public CSVs (youth, TRI_By_Name_1987_2014, Panel_2000_2022) are listed in bench/LAUNCH_FACTS.md.
set -e
D=${1:-corpus}; mkdir -p "$D"; cd "$D"
get() { [ -s "$2" ] || curl -fsSL --retry 3 -o "$2" "$1"; }
get https://sun.aei.polsl.pl/~sdeor/corpus/silesia.zip silesia.zip && unzip -oq silesia.zip -d silesia
get https://corpus.canterbury.ac.nz/resources/cantrbry.zip cantrbry.zip && unzip -oq cantrbry.zip -d cantrbry && tar cf cantrbry.tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="2000-01-01 00:00Z" --mode=a+r -C cantrbry .   # GNU tar (gtar on macOS)
get http://mattmahoney.net/dc/enwik8.zip enwik8.zip && unzip -oq enwik8.zip
[ -s gh_events.json ] || { curl -fsSL https://data.gharchive.org/2024-01-15-15.json.gz | gzip -dc | head -c 67108864 > gh_raw.json || true;
  python3 -c "d=open('gh_raw.json','rb').read(); open('gh_events.json','wb').write(d[:d.rfind(b'\n')+1])"; rm -f gh_raw.json; }
get https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sqlite chinook.sqlite
get https://github.com/jpwhite3/northwind-SQLite3/raw/main/dist/northwind.db northwind.db
get https://arxiv.org/pdf/1706.03762v7 paper.pdf
sha256sum silesia/* cantrbry.tar enwik8 gh_events.json chinook.sqlite northwind.db paper.pdf 2>/dev/null > SHA256SUMS || \
  shasum -a 256 silesia/* cantrbry.tar enwik8 gh_events.json chinook.sqlite northwind.db paper.pdf > SHA256SUMS
echo "corpus ready in $(pwd) (checksums in SHA256SUMS)"
