# Roadmap

This table preserves the original October 9 meeting plan. The implemented v0.2
package, current stage contracts and validation limits are documented in
[pipeline.md](pipeline.md); the historical statuses below are not live job status.

From the TMA meeting of 2026-10-09. Classical (non-LLM) methods first; each check
is scored against the expert workbook (`TMA Data QC Record.xlsx`) before it is trusted.

| # | Item | Owner | Planned method | Status |
|---|---|---|---|---|
| 1 | Batch transfer report | Kwan | `check_transfer.py` | v0.1 done |
| 2 | Stitched image per slide in one big image | Wilton/Kwan | `make_montage.py` (registered AF for now) | First version |
| 3 | AF/HE overlay per slide in one big image | Wilton/Kwan | `make_montage.py` overlay | First version |
| 4 | Wrong stitching direction | Kwan | Montage review, then seam-continuity score | Planned |
| 5 | HE / AF out of focus | Kwan | Sharpness score per tile, tissue only | Planned |
| 6 | HE incomplete core | Kwan | Tissue mask area and shape | Planned |
| 7 | HE tissue fold | Kwan | Colour/darkness rule, then a small trained classifier | Planned |
| 8 | Registration misalignment | Wilton/Kwan | `check_registration.py` from registration logs; image-based check later | First version |
| 9 | Double pattern, grid, uneven illumination | Wilton | Uses raw FOV positions from `tma_qc/raw_layout.py` as tile layout | Wilton |

Open questions:

- Which slide had the A3/A4 "several cores in one folder" problem? Needed to confirm the grid rule on a real case.
- Do `macro_with_roi.png` or the `.isf` file hold the planned core list?
- Can one core have more than one lamp folder?
- HE data: per core as `<core>.tiff` in the `_Reg` folders; whole HE slides as `.svs` in `_Project 1 HE to be Reg`. Confirm which one HE focus/fold QC should use.
