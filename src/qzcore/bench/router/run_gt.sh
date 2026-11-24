#!/bin/bash
cd ~/qzcore/router; export OMP_NUM_THREADS=2 QZCSV_THREADS=2; exec python3 gt.py "$@"
