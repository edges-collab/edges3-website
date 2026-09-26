# Plan: comparing a night with a "golden" reference set, binned in LST

Status: **proposal** (2026-09-26), for the team and the `edges-database` data
session. Nothing here is implemented yet.

## Goal

When a night is loaded on the site, show at a glance whether it is an
outlier. For each LST bin (15 or 30 min), plot the night's calibrated
spectrum against the distribution of a curated set of good ("golden")
nights: the selected night highlighted, the reference distribution as grey
bands. A time average is not enough: the sky changes with LST, so nights can
only be compared bin by bin.

## Why this lives outside the website

It needs (1) a *nominal* calibration applied to every night, with fixed,
versioned scientific choices, and (2) precomputed per-night LST-binned
products and reference statistics. Both are batch work for the pipeline
(`edges-pipeline`, after QL/L1), not for request handlers. The website only
reads the products, as it does for QL/L1.

## Components

### 1. A nominal calibration (scientific choices; the team decides)

A versioned pipeline configuration (hashed like the QL/L1 configs) fixing:

- **Receiver calibration per night:** which calibration day applies (the
  latest good one before the night? a fixed calibration per "epoch" between
  hardware changes?), with `cterms`/`wterms`, the fit band and the hot-load
  loss model. Proposal: an explicit epoch table (date ranges → calibration
  day), because there were no calibrations in 2024–2025.
- **Antenna S11:** which session applies to a night (nearest full or antenna
  session before it?), the fit band and the model (terms, delay).
- **Band and resolution:** e.g. 50–190 MHz; bins of 0.5 MHz (as QL) or 1 MHz.
- **Flagging:** drop dropout and outlier cycles (L1 `cycle_dropout`,
  `cycle_outlier`) and RFI-flagged channels; a minimum number of cycles per
  bin.
- **Daytime / Sun / Moon:** only cycles with the Sun below e.g. −10°; decide
  whether to exclude the Moon above the horizon.
- **What is compared:** the absolute calibrated temperature, or residuals to a
  per-bin smooth model (e.g. log-log polynomial). Differences are subtle next
  to the ~10³ K foreground, so the site will mostly show ratios or residuals
  relative to the reference median (see §4).

Record the choices in the config; a change makes a new `config_hash` and a
reprocessing, like QL/L1.

### 2. A per-night LST-binned product ("L2 night")

A new stage, after L1, run per night (the 18:00–06:00 AWST window of
`Products.night`):

- calibrate each cycle with the nominal calibration (Dicke + linear
  frontend, as `run_single_day.py` does now);
- flag as above; bin in a fixed LST grid (0–24 h in 15 or 30 min bins) and in
  frequency;
- store per bin: weighted mean, median, number of cycles, the fraction
  flagged, and the time span. Plus night-level metadata: the calibration day
  and S11 session used, housekeeping summaries, and the L1 QA counts.

Size: 96 bins × ~280 channels × a few float32 statistics ≈ 0.5 MB per night.
Products DB: an `l2_night` table (night date, config hash, n_cycles, LST
coverage, flags) plus the HDF5 file, as for QL/L1. Compute: calibrating a
night is a few CPU-minutes, so a year is a few CPU-hours; run it `nice`d in
the cron chain after `l1`, and backfill once.

### 3. The golden reference set

- **Membership:** a versioned, named list of nights in the products DB
  (`reference_set`, `reference_set_night`), curated by the team. The pipeline
  can *propose* candidates (no dropouts, low RFI occupancy, full LST coverage,
  housekeeping in range, stable calibration) but a person approves them.
- **Statistics:** for each reference set × config, per LST bin and channel:
  the median, the 16/84 % and 2.5/97.5 % percentiles, a robust σ (MAD), and the
  number of contributing nights. Stored as one small HDF5 product
  (`reference_<set>_<config>.h5`). Recompute when the set or config changes.
- **Seasonality:** a given LST falls at different local times through the
  year, so a set should cover the season(s) being compared, or the site can
  pick the members nearest in date. Start simple (one set) and see.

### 4. Scores

For a night and a reference set: per bin and channel,
`z = (night − median) / robust σ`. Summarise per LST bin (median |z|, the
fraction of channels with |z| > 3) and per night (e.g. the worst bin, the
number of bins beyond a threshold). Store the scores with the L2 product, so
the Nightly Overview can show a badge ("outlier in 3 of 40 LST bins") and a
score-vs-LST strip.

### 5. Website

Read-only, like the QL/L1 pages:

- `GET /api/nights/{date}/lst-compare?set=&bin_minutes=`: for each LST bin
  the night's spectrum, the reference percentile bands, and the scores
  (downsampled in frequency if needed; ~96 bins × 280 channels is small).
- **Detailed Data View:** a grid of small multiples (one panel per LST bin,
  shared axes), the night in colour over grey bands; a toggle between
  absolute, ratio to the reference median, and z-score.
- **Nightly Overview:** a score-vs-LST strip under the waterfall and an
  outlier badge.

## Phases

1. **Decide the nominal calibration** (§1) with the team: epochs, S11 choice,
   band, flagging, Sun/Moon cuts.
2. **L2 night product** in `edges-pipeline` for the last 30 days, then a
   backfill; add `Products.l2_night(date)`.
3. **Reference sets and statistics**; the pipeline proposes candidates, the
   team curates the first set.
4. **Site:** the API and the comparison view; then the overview badge.
5. **Scores and alerts:** e.g. flag outlier nights in the cron summary.

## Open questions for the team

- How to choose the receiver calibration and antenna S11 for each night
  (epochs vs. nearest)?
- The comparison band and resolution; whether to compare absolute
  temperatures or residuals to a smooth model.
- Sun and Moon cuts; the minimum cycles per bin.
- Who curates the golden set, and how often it changes.
- 15 or 30 min LST bins (15 min: ~39 cycles per bin per night; 30 min: ~78).
