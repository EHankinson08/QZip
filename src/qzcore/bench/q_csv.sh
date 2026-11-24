#!/bin/bash
# wait for enwik8 jobs, then logs/CSVs one at a time (qzs = qtyp + qzc CM, whole-file CM tried <=256 MiB) + references
while pgrep -f "qzc12 c enwik8|qzc12 d out/e8auto|qzc13 c data_SSH|qzc13 d out/qzc13_L3" >/dev/null; do sleep 30; done
D=~/qzip_bench/data
for f in Android.log youth-dataset-long-form.csv TRI_By_Name_1987_2014.csv Panel_2000_2022.csv products2m.csv Data8277.csv; do
  ln -sf $D/$f data_$f; ./rt.sh qzs data_$f qzs_auto; ./refs.sh data_$f bsc mcmx11 xz zstd
done
for f in Apache.log HealthApp.log SSH.log fndds.csv hctsa.csv; do ./refs.sh data_$f bsc mcmx11 xz zstd; done
