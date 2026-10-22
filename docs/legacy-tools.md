# Legacy transfer, registration-log and montage tools

These standalone tools remain supported. For raw-image preprocessing, start
with the [project README](../README.md) and [research guide](RESEARCH_GUIDE.md).

Company-specific QC tools for the TMA data. Private: it contains internal paths
and slide names. The general image-QC library `fluoinspect` stays a separate,
public project.

| Tool | What it does | Needs | Status |
|---|---|---|---|
| `check_transfer.py` | Counts slides, cores and FOVs; flags missing data and several cores saved in one folder; compares two sites | Python only | Ready (v0.1) |
| `check_registration.py` | Flags cores that are not registered or where AF and HE do not line up, from the registration log files | Python only | First version, limits to tune |
| `make_montage.py` | One picture per slide: AF, HE and AF/HE overlay, every core in its TMA position | image libraries | First version |
| Focus, fold, incomplete core... | See [ROADMAP.md](ROADMAP.md) | | Planned |

```
phomedics-tma-qc/
├── check_transfer.py         ← transfer report
├── check_registration.py     ← registration report
├── make_montage.py           ← one picture per slide
├── settings.toml             ← everything you may want to change
├── requirements-image.txt    ← libraries for make_montage.py
├── src/tma_qc/
│   ├── raw_layout.py         ← reads the raw folders (names and sizes only)
│   ├── transfer_check.py     ← transfer rules and report
│   ├── registration_check.py ← registration rules and report
│   ├── preview.py            ← fast small previews of big TIFFs
│   ├── montage.py            ← builds the per-slide pictures
│   └── report.py             ← OK/CHECK/ALERT, CSV and text output shared by all tools
├── tests/                 ← fake slides that prove the rules still work
└── docs/ROADMAP.md
```

`check_transfer.py` and `check_registration.py` need **Python 3.11 or newer** and
nothing else. `make_montage.py` also needs the libraries in `requirements-image.txt`;
on the HK workstation use the existing environment:
`/mnt/4TB_ssd/PhoMedics/qc/environments/fluoinspect/bin/python make_montage.py ...`

---

## Transfer check

**It only reads.** It never changes, moves or deletes data. If it has a bug,
the worst result is a wrong report.

### 1. Check a data folder

```
python check_transfer.py scan "<data folder>" --out "<report folder>"
```

Example (HK workstation):

```
python check_transfer.py scan "/mnt/pho-nas/datacollection/TMA(Raw)/2026/Project 1_CQ_UV_HE_Raw" --out reports/wuhan_2026-10-13
```

Windows example: `python check_transfer.py scan "E:\TMA\Project 1_CQ_UV_HE_Raw" --out "E:\TMA_reports\HK_2026-10-13"`

`<data folder>` can be one slide or a folder holding many slides; the tool finds
the slides itself (a slide = a folder containing core folders such as `A1`).

If the run stops half-way (NAS down, PC restarted), **run the same command again**:
it continues where it stopped. Every other run reads all slides fresh, so an old
result is never shown as new (a finished run, a different data folder, or
`--rescan` all start from scratch). Slides that could not be read because of a
NAS error are always read again.

### 2. Compare two sites

Run `scan` at both sites, then:

```
python check_transfer.py compare "<report of sending site>" "<report of receiving site>" --out "<folder>"
```

It lists every slide, core, FOV, image or stitch tile that was lost, added or changed
size on the way. It compares names and sizes, not file contents: a file damaged
without changing size is not detected.

### 3. Optional: a reference list

If you know the expected cores or FOV counts, put them in a CSV and add
`--reference reference.csv`:

```
slide,core,expected_fovs
D049Ga01_01_CQ_UV,A1,49
D049Ga01_01_CQ_UV,A2,42
D049Ga01_01_CQ_UV,A3,
```

A blank `expected_fovs` means "this core should exist" without checking the count.

### What the report folder contains

| File | Content |
|---|---|
| `report.txt` | Short summary plus every ALERT and CHECK. **Start here.** |
| `alerts.csv` | Only the problems, worst first (opens in Excel) |
| `slides.csv` | One row per slide: cores, FOVs, size, status |
| `cores.csv` | One row per core: FOVs, grid shape, stitch tiles, scan time, problems |
| `manifests/` | One "packing list" per slide, used by `compare` |
| `run_unfinished.txt` | Only while a run is going; if it is still there, the run stopped early |

### What each message means and what to do

Levels: **OK** nothing to do · **CHECK** probably fine, have a look · **ALERT** something is wrong.

| Message | Level | Meaning | What to do |
|---|---|---|---|
| FOVs do not fit the main scan grid | ALERT | Positions from a second, shifted scan grid are in this folder | Likely **several cores in one folder** (the A3/A4 problem). Ask Athena to check the folder. |
| scan grid is AxB, larger than the limit | ALERT | The FOVs cover a bigger area than one core | Same as above |
| FOVs share a grid position | ALERT | Two FOVs at the same place | Possible double scan; check the folder |
| no scan folder / no FOVs | ALERT | The core folder has no data | Data missing; check with the hospital or the previous site |
| FOVs have no image / more than one image | ALERT | Each FOV folder should hold exactly one image | Incomplete copy, or two scans merged |
| images are 0 bytes | ALERT | Broken file | Copy again from the previous site |
| FOVs, reference says N | ALERT | Count differs from your reference list | Check the core |
| cores in reference but not found | ALERT | A core on the reference list has no folder | Data missing |
| positions missing inside the grid | CHECK | The rectangle has holes | Usually incomplete scan; have a look |
| stitch folder has N tiles, scan has M FOVs | CHECK | Stitch tiles and raw FOVs differ | Stitch may be outdated or incomplete |
| no stitch folder | CHECK | Core was not stitched | Fine for raw-only transfers |
| images were written N min apart | CHECK | Long pause inside one core | Possibly scanned twice; have a look. Only meaningful where the data was scanned: copies that do not keep file times give false CHECKs. Set `max_time_gap_minutes = 0` to switch off |
| unexpected items in scan folder | CHECK | Files or folders that do not belong there | Have a look |
| slide folder name does not follow the naming pattern | CHECK | e.g. `_Fail`, `_no stitch folder` | Confirm with the team which folder is the real one |
| has 2 lamp folders | CHECK | A core was scanned with two lamp settings | Confirm which one is used |

### How "several cores in one folder" is found

Every FOV folder is named by the stage position in mm, e.g. `43.591236_42.357218`.
One core is one full rectangle of positions, about 0.43 mm apart (7×7, 7×6, 6×6
or 6×5 seen so far). A second core in the same folder sits on a shifted grid or
makes the rectangle too big. No image is opened, so this is fast.

---

## Registration check

```
python check_registration.py "/mnt/pho-nas/datacollection/TMA(Raw)/2026/Project 1_CQ_UV_HE_Reg" --out reports/registration_2026-10-13
```

Reads only the small text files the registration code writes in each core's
`Registration` folder (`matrix.txt`, `registration_log.csv`, `postcrop_eval_result.txt`).
Writes `report.txt`, `alerts.csv` and `registration.csv` (one row per core).

| Message | Level | Meaning |
|---|---|---|
| not registered | ALERT | Core has no `Registration` folder |
| registered HE / AF image missing | ALERT | Registration did not finish |
| transform scale X, expected about 0.85 | ALERT | HE was shrunk or stretched wrongly: registration failed |
| HE rotated N degrees | ALERT | HE was turned: registration failed |
| tissue overlap (Dice) is very low / low | ALERT / CHECK | AF and HE tissue areas do not match well |
| local shift after registration N px | CHECK | Small patches still disagree by many pixels |
| only N matched features | CHECK | Weak registration; result may be unreliable |
| registration code flagged the crop boundary | CHECK | The registration code itself raised a flag |
| no evaluation result | CHECK | Older registration run without `postcrop_eval_result.txt` |

The limits (in `settings.toml`, `[registration]`) are a first guess from 433 cores.
Confirm them by looking at the overlay montage.

---

## Montage

```
python make_montage.py "<registered folder>" --out "<folder>" --registration-report "<registration.csv>"
```

Per slide: `<slide>_AF.png`, `<slide>_HE.png`, `<slide>_overlay.png`. Cores sit in their
TMA position (rows A, B, ... and columns 1, 2, ...).

- **AF**: all cores use the same brightness window, so a core that is much brighter or
  darker than its neighbours stands out. Use it for stitching direction and brightness checks.
- **Overlay**: AF green, HE magenta. Aligned tissue looks white/grey; green or magenta
  edges mean misalignment.
- With `--registration-report`, tile borders show the registration status
  (green OK, orange CHECK, red ALERT).

Previews are cached in `preview_cache/` inside the output folder, so a second run is fast.

---

### Changing the rules

Edit `settings.toml` (not the code). For example `max_columns`, `max_rows`,
`max_time_gap_minutes`, or the folder names under `[layout]`.

### Checking the tool still works

After any change, run from this folder:

```
python3 -m unittest tests.test_transfer_check tests.test_registration_check
```

It builds small fake slides (normal, two cores in one folder, empty core, lost
file...) and checks that every rule gives the expected answer. The last line must
say `OK`.

### Keeping track of changes (git)

```
git status                 # what changed since the last save
git add -A
git commit -m "Raise max_rows to 9 for large cores"
git log --oneline          # history
```
