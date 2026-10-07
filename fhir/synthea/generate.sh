#!/bin/sh
# Generates the clinic's synthetic adult Patients as FHIR R4 bundles in /output/fhir.
# Seed, clinician seed and reference date are pinned, so every run produces the same people.
# Skips generation when /output/fhir already holds bundles. Delete fhir/output to regenerate.
set -eu

OUT=/output
POPULATION="${SYNTHEA_POPULATION:-60}"
SEED="${SYNTHEA_SEED:-20260101}"
REFERENCE_DATE="${SYNTHEA_REFERENCE_DATE:-20260101}"

if ls "$OUT"/fhir/*.json >/dev/null 2>&1; then
  echo "Synthea output already present in $OUT/fhir, skipping generation."
  exit 0
fi

exec java -Xmx2g -jar /opt/synthea/synthea.jar \
  -s "$SEED" -cs "$SEED" -r "$REFERENCE_DATE" \
  -p "$POPULATION" -a 18-85 -m "demographics-only-no-modules" \
  --exporter.baseDirectory="$OUT" \
  --exporter.fhir.export=true \
  --exporter.hospital.fhir.export=false \
  --exporter.practitioner.fhir.export=false \
  --exporter.years_of_history=1 \
  --generate.thread_pool_size=2 \
  --generate.only_alive_patients=true \
  --generate.append_numbers_to_person_names=false
