#!/usr/bin/env bash
# Load the offline fixtures into the corpus so the pipeline runs without Jira.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p corpus/raw
cp tests/fixtures/pesd1.jsonl tests/fixtures/prdt.jsonl corpus/raw/
cp tests/fixtures/intake_sheet.csv corpus/raw/
python3 -m corpus.index build
