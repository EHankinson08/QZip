#!/bin/bash
# all NIfTI volumes through qzm (QMED transforms + qzcore backends), sha256 checked by rt.sh
for f in ~/qzip_medical/orig/*.nii; do b=med_$(basename $f); ln -sf $f $b; grep -q "qzm_auto	$b	" rt.tsv || ./rt.sh qzm $b qzm_auto; done
