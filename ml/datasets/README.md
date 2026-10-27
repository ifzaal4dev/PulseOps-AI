# Datasets

## UCI MetroPT-3 — the flagship dataset (REAL operational data)

* **Source**: UCI Machine Learning Repository, id 791 —
  <https://archive.ics.uci.edu/dataset/791/metropt+3+dataset>
* **Direct zip**: <https://archive.ics.uci.edu/static/public/791/metropt+3+dataset.zip>
* **License**: CC BY 4.0
* **What it is**: telemetry from a metro train's Air Production Unit (APU)
  compressor (Porto, Feb 1 – Sep 1 2020), logged at 1 Hz on-board and
  published at 10 s cadence. 1,516,948 rows; 7 analog sensors (TP2, TP3, H1,
  DV_pressure, Reservoirs, Oil_temperature, Motor_current) + 8 digital
  control signals (COMP, DV_eletric, Towers, MPG, LPS, Pressure_switch,
  Oil_level, Caudal_impulses). Note: `DV_eletric` is the dataset's own
  spelling.
* **Failure reports** (from the UCI page; all air leaks): Apr 18 2020,
  May 29–30 2020, Jun 5–7 2020, Jul 15 2020 — exact timestamps in
  `pulseops_ml/dataset.py::FAILURE_EVENTS` with the source citation.
* **Paper**: Davari et al. (2021), IEEE DSAA — "Predictive maintenance based
  on anomaly detection using deep learning for air production unit in the
  railway industry."

### Expected layout (validated by `python -m pulseops_ml.ingest`)

```
ml/datasets/raw/metropt3/
├── metropt3.zip                     # sha256 pinned in pulseops_ml/dataset.py
└── MetroPT3(AirCompressor).csv      # extracted from the zip
```

Expected zip sha256:
`aab991a970e58210de853bb8078ce0e63abb4d9412fdc5c79792dae3d8e1721a`

`raw/` and `processed/` are gitignored — never commit dataset files.

### Download

```bash
mkdir -p ml/datasets/raw/metropt3 && cd ml/datasets/raw/metropt3
curl -L -o metropt3.zip "https://archive.ics.uci.edu/static/public/791/metropt+3+dataset.zip"
python -c "import zipfile; zipfile.ZipFile('metropt3.zip').extractall('.')"
```

## NASA C-MAPSS — NOT used

C-MAPSS is *simulated* turbofan degradation data. Per repo rules it must not
be described as real operational data or used for the flagship model.
