# Spec: Racks (select a line, get a rack)

Replaces the earlier `docs/rack-spec.md` on branch `claude/dazzling-cray-3p6oh2`. Written against `main` at 93dab27 with the code read, but nothing here has been run in Fusion yet. API calls named below were checked against the installed stubs; anything marked **verify** must be confirmed in Fusion before relying on it.

SPEC.md stays the source of truth. Part of this work is adding a "Racks" section to it (see "Definition of done").

## Goal

GearGremlin turns a sketch circle into a spur gear. Add the same for a sketch **line**: the line becomes the **pitch line** of a **rack** (a gear of infinite radius) with straight-sided teeth.

Two steps. **Finish, test, and report Step 1 before starting Step 2.**

- **Step 1:** a standalone rack from a line, editable like a gear.
- **Step 2:** rack-and-pinion meshing.

## Decisions taken (defaults; change before building if the user disagrees)

| # | Decision | Default chosen | Why |
|---|---|---|---|
| D1 | Tooth count with Resize on | `N = max(1, round(L / p))` | Matches the circles' hybrid sizing, `round(d / m)`. `floor` would always shorten the line. |
| D2 | Outline | Closed: toothed edge + two end lines + a back line, backing thickness `3·m` below the roots. `0` = toothed edge only. | A closed region extrudes immediately, which is the usual next step. |
| D3 | Which side the teeth are on | A **Flip side** checkbox, off = teeth on the **left** of the line's start → end direction. Not remembered between runs. | Users don't know which way a line was drawn, so "left/right" is meaningless to them. The preview shows the result; flip if wrong. For a line drawn left to right, off means teeth point up. Remembering it would just be a coin flip on the next line. |
| D4 | Tooth phase | With offset 0, the line's start is at the **center of a tooth space**. Offset slides the pattern toward the line's end. | It puts the ends of the toothed edge at root depth, so the end lines are simple. Step 2's mesh rule is written in terms of it. |
| D5 | Partial teeth | Never drawn. A tooth whose full pitch cell doesn't fit on the line is replaced by a flat root. | "Whole teeth only," as the circles have. |

## Notation and frame

`m` module, `α` pressure angle, `k` tooth height factor, `B` backlash (total per mesh, mm), `p = π·m` pitch, `T` backing thickness.

Rack frame, all in mm and sketch coordinates:

- `S` = line start point, `E` = end point, `L = |E − S|`, `u = (E − S) / L`.
- `σ = +1` for teeth on the left (Flip side off), `−1` for the right. `n = σ·(−u_y, u_x)` points from the pitch line toward the tooth tips.
- A rack point `(s, h)` maps to `S + s·u + h·n`. `s` runs along the line, `h` is height above the pitch line.
- `σ = −1` makes the map a reflection, which reverses the direction of every arc (see "Arc direction").

## Step 1: Standalone rack

### User workflow

1. In a sketch, select a line (solid or construction) and run GearGremlin, or run it and pick the line.
2. The dialog shows the rack inputs, and the preview draws the rack live.
3. On OK the line is resized (if Resize is on), made construction, and the rack outline is drawn. A rack record goes on the line.
4. Right-clicking the line or any rack curve offers **Edit Gear**, which reopens the dialog with the stored settings and redraws the rack under the same id.

### Tooth geometry

All of this is a pure function in `gearmath.py`, built in the local `(s, h)` frame and then placed.

**Constants shared with gears.** Addendum `a = k·m` (`GearParams.addendum`). Dedendum `hf = (k + 0.25)·m`. Root fillet radius, as in ISO 53 profile A.

**Key fact: the rack's tooth space is exactly the cutter tooth that `cutter()` already models.** `cutter()` returns a tooth with half-width at the pitch line `w = π·m/4 + B/4`, depth `hf`, and corners rounded at radius `ρ` (`CUTTER_TIP_RADIUS·m`, shrunk if it doesn't fit). A rack meshing with gears made by the tool has the same shape for its spaces. Check: its tooth width at the pitch line is then `p − 2w = p/2 − B/2`. That matches a gear tooth, which is thinned by `B/2` (`half_angle` subtracts `B/(4r)`). So a rack and a pinion together have the full backlash `B`, as two gears do.

- Don't write new constants. Refactor `cutter(params)` into `_cutter(module, pressure_angle, dedendum, backlash)` plus the existing wrapper. The rack calls `_cutter` directly. `cutter()`'s behavior and the existing tests must not change.
- **Common mistake:** the rack's *tooth tip* is at `h = +a`, not `+hf`. The cutter is 0.25·m taller than a rack tooth because it also cuts the gear's root clearance. Only the depth (`hf`) is shared.

**One tooth space, centered at `s = 0`** (`x` measured from the space centerline; `c = _cutter(...)`):

- Root flat: `h = −hf`, from `x = −c.corner_x` to `+c.corner_x`. Omit it when `corner_x == 0`. That is a full-round root, and the two arcs meet at the centerline.
- Right corner arc: center `(c.corner_x, −c.corner_depth)`, radius `c.corner_radius`. It starts at the root point `(corner_x, −hf)` (direction angle `−π/2` from the center) and ends at the flank tangent point `(corner_x + ρ·cos α, −corner_depth − ρ·sin α)` (direction angle `−α`). Sweep `π/2 − α`. The tangent point's depth equals `c.straight_depth`. Assert that in a test.
- Right flank: the straight line `x = w + h·tan α`, from the tangent point up to the tip `h = a`.
- The left side is the mirror image through `x = 0`.

**One tooth** fills the gap between two spaces. Its top land at `h = a` runs from `x = w + a·tan α` to `x = p − w − a·tan α`, measured from the left space's centerline. Top-land width = `p/2 − B/2 − 2·a·tan α`. Tip corners are sharp, as on the gears.

**Pattern (D4, D5).** Tooth `i` (any integer) is centered at `s_i = p/2 + offset + i·p`. Its **cell** is `[s_i − p/2, s_i + p/2]`, from one space center to the next. Draw tooth `i` exactly when its cell is inside `[0, L]`, with a tolerance of 1e-9 mm. Only `offset mod p` matters. Everywhere no tooth is drawn, the edge runs along the root line `h = −hf`. So:

- the toothed edge always starts at `(0, −hf)` and ends at `(L, −hf)`;
- consecutive root flats (between teeth, and the end runs) are merged into single `Line`s, so the root is never split into collinear pieces;
- with `offset = 0` and `L = N·p`, exactly `N` teeth are drawn. With a non-zero offset, the same line holds `N − 1`. The info text shows the count drawn.

**Closing (D2).** If `T > 0`: end lines from `(0, −hf)` to `(0, −hf − T)` and from `(L, −hf)` to `(L, −hf − T)`, plus a back line between their bottom ends. Together with the edge they form one closed loop. If `T = 0`: just the edge, plus an info note "Outline is open, so it won't extrude."

### Arc direction

`Arc` is counter-clockwise only today (`start_angle`, `sweep > 0`). A closed rack loop needs the root corners traversed the other way. Add `clockwise: bool = False` to `Arc`, at the end so existing positional calls keep working:

- When `clockwise` is true, `start` and `end` swap. `mid` is unchanged. The arc still covers `start_angle .. start_angle + sweep`.
- Update `_svg_path` (sweep flag) and anything else that reads `start`/`end`.
- `draw_profile` uses `seg.start`, `seg.mid`, `seg.end` with `addByThreePoints`, so it needs no change. Confirm that by reading it again before relying on it.
- Placing with `σ = −1` mirrors arcs: an arc reflected in the frame flips `clockwise`, and its angles change to match. Test both sides for loop continuity.

### gearmath.py additions (pure Python, typed, unit-testable)

- `RackParams` (frozen dataclass): `module`, `pressure_angle`, `backlash = 0.0`, `height_factor = 1.0`. Properties: `pitch`, `addendum`, `dedendum`. Racks don't use `GearParams`, which requires a meaningful `teeth`. Step 2 needs `same_system`-style matching between the two types, so put module, pressure angle and factor under the same attribute names.
- `RackProfile` (dataclass): `params`, `origin` (S), `direction` (angle of `u`, radians), `side` (σ), `length` (L used), `offset`, `body` (T), `tooth_centers` (the drawn `s_i`), `segments` (placed, in sketch mm), `warnings`.
- `rack_teeth_for_length(L, params) -> int`: D1.
- `rack_tooth_centers(L, params, offset) -> list[float]`: D4/D5.
- `build_rack(params, origin, direction, side, length, offset, body) -> RackProfile`. Build in local `(s, h)`, then place.
- `rack_factor_max(params) -> float`: the largest `k` with top land ≥ `MIN_TOP_LAND·m` (0.2·m) and `_cutter(...).valid`. It's closed-form, so no scan is needed.
- `check_rack(params, length, resize, offset, body) -> Check`. Blocks: module ≤ 0; backlash < 0 or ≥ `p/2`; `k` above `rack_factor_max` or below `FACTOR_FLOOR`; `T < 0`; zero teeth fit ("The line is shorter than one tooth (p = 6.28 mm)."); more than `MAX_TEETH` teeth. Warns: Resize off and the line isn't a whole number of pitches.
- `profile_area(segments) -> float`: signed area of a closed loop of `Line`/`Arc`/`Spline` segments, by Green's theorem with exact arc terms. Both the pytest and Fusion area checks use it.
- `RackRecord` plus `to_rack_attribute` / `from_rack_attribute` (separate from the gear record, so neither reader can confuse the two). `from_rack_attribute` returns `None` on any malformed input, as `from_attribute` does.
- `to_svg` accepts `RackProfile`s, alone or mixed with gear `Profile`s, for Step 2. Draw the pitch line dashed, and draw an arrow along `u` at `S` plus a short tick along `n`, so direction and side are visible.

### Rack record (attribute)

Group `GearGremlin`, name **`rack`**, on the pitch line:

```json
{"version": 1, "id": "<uuid hex>", "module_mm": 2.0, "pressure_angle_deg": 20.0,
 "tooth_height_factor": 1.0, "backlash_mm": 0.05, "side": 1, "offset_mm": 0.0,
 "resize": true, "body_mm": 6.0, "teeth": 8,
 "origin_mm": [0.0, 0.0], "direction_rad": 0.0, "length_mm": 50.27, "mesh_with": ""}
```

- `origin_mm`, `direction_rad` and `length_mm` record where the rack was **drawn**. An edit may already have moved the line, so the parts walk measures against these (as gears measure from the tagged tip arc's center). Step 2 alignment uses the **live** line geometry, as gears use live centers.
- `teeth` is the drawn count, for the info text and Step 2 messages.
- `mesh_with` is empty in Step 1. It's reserved so Step 2 doesn't need a version bump.

### Fusion layer: `commands/gearProfile/drawing.py`

Put rack code in a new `rack_drawing.py` next to `drawing.py` if that keeps `drawing.py` readable. Reuse `draw_profile`, `tag_part`, `capture_features`, `_Repoint`, `to_cm`/`to_mm` and `p3` rather than copying them.

**Reading the line.** `line.startSketchPoint.geometry` / `endSketchPoint.geometry` are in sketch coordinates (cm). If either point's `z` is more than 1e-6 cm, block with "The line must lie in the sketch plane."

**`resize_line(sketch, line, length_mm, hold) -> ResizeResult`**, mirroring `resize_circle`:

- Blocking pre-check (`line_resize_check`): the line itself or its **end** point is fixed. A fixed start point is fine, because the start is meant to stay put.
- Temporarily fix: the line's start point, every gear circle's center, and every other rack line's endpoints. Unfix them all in `finally`.
- Find the length dimension: a driving `SketchLinearDimension` whose `entityOne`/`entityTwo` are this line's two endpoints, or the line itself, with orientation `AlignedDimensionOrientation`. **Verify** in Fusion how a dimension placed on a line reports its entities. If found, set `dim.parameter.value`. Otherwise add one with `sketch.sketchDimensions.addDistanceDimension(start, end, adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, textPoint, True)` (signature confirmed in the installed stubs).
- If the line's length is held by something else (a horizontal or vertical dimension on a diagonal line, or a coincident endpoint), the solver will refuse or ignore the change. Report it as circles do: "The sketch constraints hold this line at 48.00 mm, but the rack needs 50.27 mm. Change the module, or remove a constraint."
- Afterwards, re-read `S`, `E`. Succeed only if `|L − target| ≤ 1e-4 mm`, `S` hasn't moved (1e-6 mm), and the direction hasn't changed (1e-9 rad). Otherwise report an error. Never draw teeth on a line the solver rotated.
- Skip when the line is already at the target length and held there (by a dimension, or fully constrained). Any dimension change re-solves the whole sketch.
- If the length dimension is an expression, warn as `dimension_expression_warning` does.
- **Verify** that a failed execute leaves no added dimension behind. Circles have the same path, so whatever they do, racks must do the same, and say so in the report.

**`make_rack(line, params, side, offset, body, resize, finalize, edit) -> GearResult`**, the same shape as `make_gear`:

1. Guards: `edit` requires an existing, readable rack record. Without `edit`, a line that already has one errors with "This line is already a rack. Pick a different line." A line that belongs to a gear or rack outline errors with "That line is part of a gear made by this tool."
2. Resize if on, else warn (the `check_sizing` analog).
3. Re-read the line, then `build_rack(...)`.
4. On edit with finalize: `capture_features` with the rack's part tokens, then `_Repoint.park()`. Delete the old parts only after resize and build have succeeded, so a failed edit leaves the old rack intact.
5. Draw with `isComputeDeferred = True`. On finalize, tag each tooth's **top-land line** with `PART_NAME = rack id`, inside the deferred block. If no teeth are drawn, tag the back line, or with `T = 0` the root line. No tags in preview.
6. On finalize: `line.isConstruction = True`, write the record, then `_Repoint.finish(...)`.

`GearResult.profile` is typed `gm.Profile`. Widen it to accept a `RackProfile` too, or add a `rack` field. Don't fake a `Profile`.

**Finding a rack's parts.** `gear_parts` can't be reused as is: it looks up a *circle* by id and walks only arcs and splines inside a radius band. Add `rack_parts(sketch, rack_id)`: start from the tagged curves and use `_walk` with an `accept` that takes untagged `SketchLine`s and `SketchArc`s whose endpoints all lie in the drawn band (from the record's `origin_mm`, `direction_rad`, `length_mm`): `−1e-4 ≤ s ≤ L + 1e-4` and `−(hf + T) − 1e-4 ≤ h ≤ a + 1e-4`, plus anything tagged with this id. The pitch line itself is excluded. A user curve attached to a rack point, but outside the band, is left alone. `delete_rack_parts` deletes tags first, then the curves in one `deleteEntities` call, as `delete_gear_parts` does.

**Resolving an entity** (`gear_for_entity`, used by the right-click menu and by command start): extend it, or add a sibling with one entry point, so it returns `('gear', circle)`, `('rack', line)` or `None`:

- A line with a `rack` record is that rack.
- A curve tagged with an id: look for a gear circle with that id, then for a rack line with that id (`rack_lines(sketch)`, the analog of `gear_circles`).
- An untagged `SketchLine` or `SketchArc`: walk as today (≤ 12 steps) to a tagged curve. Accept it only if the curve is inside that owner's band: the radial band for a gear, the rack band for a rack. The current walk skips `SketchLine`s, which is correct for gears (their outlines contain no lines) and must stay that way for gear lookups.

Existing callers of `gear_for_entity` expect a circle. Update each one; don't change the return type silently.

### Dialog: `commands/gearProfile/entry.py`

- Pitch input: label "Pitch circle or line", filters `SketchCircles` and `SketchLines` (both valid filter names per the API reference's SelectionFilters page). Tooltip: "A circle becomes a gear's pitch circle; a line becomes a rack's pitch line, where it touches the gear it meshes with."
- `_selected_circle` / `command_pre_select` / `command_created` cast only to `SketchCircle`. Generalize them with a helper returning the selected circle or line.
- `command_pre_select` for the pitch input rejects gear circles, rack lines (except the one being edited), and any line or arc that belongs to an outline. Use the cheap checks (record, tag) first, because preSelect fires on every hover. Walk only for untagged lines.
- `_edit_circle` becomes the edited entity plus its kind.
- New inputs, shown only when a line is selected:

| Input | Type | Default | Tooltip (one plain sentence) |
|---|---|---|---|
| Flip side | Checkbox | Off | "Puts the teeth on the other side of the line." |
| Offset along line | Distance value | 0 mm | "Slides the teeth along the line." |
| Resize line to whole teeth | Checkbox | On (remembered with circles' Resize) | "Lengthens or shortens the line from its end so it holds a whole number of teeth." |
| Backing thickness | Distance value | 3 × module | "Solid material below the tooth roots; 0 draws only the toothed edge." |

- Shared inputs stay: module, pressure angle, tooth height (with its note), backlash.
- Hide when a line is selected: gear type, teeth, Resize circle, Mesh with, rotation offset, reference circles. Toggle in `command_input_changed` when `PITCH` changes, and once in `command_created`.
- Tooth height note for a rack: "Valid: 0.60–{rack_factor_max:.2f}." `ci.height_note` takes `GearParams`, so add a rack variant rather than passing a fake gear.
- Info text: `Line 48.00 mm → 50.27 mm (+2.27 mm), 8 teeth, pitch 6.28 mm`, then warnings and errors, as for circles. With Resize off: `8 teeth; 1.73 mm of the line has no teeth`.
- Update `CMD_Description` to mention racks. The right-click entry stays "Edit Gear".

### Settings (`settings.py`)

Add one key, `rack_body_mm`, default `-1.0`, meaning "3 × module". The load loop already ignores missing keys and keeps defaults, so old `settings.json` files load. Add a pytest-free check of that in the Fusion script, or a tiny unit test if `settings.py` can be imported without `adsk` (it can: it only imports `json` and `os`). Flip side and offset are per rack (restored from the record on edit), not remembered.

### Pytest (`tests/test_gearmath.py`)

Run the whole suite (about 1.5 minutes), not just the new tests.

- `rack_teeth_for_length`: `L = N·p` exactly, just under, just over, and the round-half boundary. Minimum 1.
- Tooth centers: the offset-0 pattern; an offset of `p` gives the same pattern; a non-zero offset drops exactly one tooth on an `N·p` line; no cell extends past `[0, L]`.
- Tooth width at the pitch line is `p/2 − B/2` for several `B` (measured on the built segments, not recomputed from the formula).
- Tip at `h = a = k·m`, root at `h = −(k + 0.25)·m`, for k = 0.8, 1.0, 1.25.
- Flank angle to the normal equals `α` for 14.5°, 20°, 25°.
- Fillet: tangent to the root line and to the flank (equal tangent directions at both joins). The tangent point's depth equals `_cutter(...).straight_depth`. Full-round case (`corner_x == 0`) included.
- `cutter()` unchanged after the refactor (the existing tests cover it; also compare a few values before and after).
- Continuity: every segment's end equals the next one's start (1e-9). The loop is closed when `T > 0` and open from `(0, −hf)` to `(L, −hf)` when `T = 0`.
- Side: `σ = −1` gives the mirror image of `σ = +1` through the pitch line. Tooth tips for `σ = +1` lie on the left of `u`, checked by cross product.
- Placement: a profile built at `(origin, direction)` equals the one built at the origin, rotated and translated, for directions 0°, 30°, 90°, 137°, 180°.
- Area: `profile_area` of the closed rack equals an independent count (`L·(hf + T)` + teeth area − fillet corrections), and is invariant under placement.
- `rack_factor_max`: top land equals 0.2·m at the max. `check_rack` blocks above it and below 0.6.
- `check_rack` messages: too short, too many teeth, backlash too large, negative T.
- Record round-trip. Malformed JSON, a missing key, or a gear record passed to `from_rack_attribute` all give `None`.
- Write SVGs of a short rack on both sides, with a diagonal direction, to `tests/out/`, and tell the user where they are.

### Fusion (`dev_scripts/test_rack.py`, run through the MCP server)

Follow `dev_scripts/test_drawing.py` exactly: `ROOT` at the top, `load_drawing()` under a private package name, a **new scratch design document** (never touch, save, or close existing ones), `check(label, ok, detail)`, results to `tests/out/`.

- Lines at 0°, 90°, 30° and 137°, on both sides. The teeth point to the chosen side, checked from the drawn top-land midpoints (sign of the cross product with `u`), not from the input.
- Module, pressure angle, k, and B combinations: tooth tips at `h = a` and roots at `h = −hf`, measured from sketch geometry.
- Resize on, with an awkward length: final length `N·p`, start point unmoved, direction unchanged. Resize off: no partial tooth, and leftover reported.
- A line with an aligned length dimension; with a horizontal constraint; with a fixed end point (blocked); a diagonal line with a horizontal dimension (clear error, sketch unchanged); an end point coincident with another line.
- A line shorter than one pitch (clean error). A long line near `MAX_TEETH`: time preview and execute as `time_preview.py` does, and report the times.
- The guard: a second rack on the same line errors. A rack line or a tooth line can't be picked as a new pitch line (call the preSelect logic's helper directly).
- Regions: `T > 0` gives exactly one new profile whose area matches `profile_area` (convert cm² → mm²). Extrude it and confirm one body with volume = area × height. `T = 0` gives no new closed profile.
- Edit: change module, Flip side, offset, and T on an existing rack. The id is unchanged, no orphan curves or `part` attributes are left, and the old curves are gone.
- Edit with an extrude built on the rack, and with a second extrude using the rack plus a user rectangle around it: the features are re-pointed and healthy, and volume matches the new area. Report honestly if it doesn't work, and don't paper over it.
- Gears and racks in one sketch: `gear_circles`, `find_gear_circle`, `gear_for_entity`, `gear_parts` and `rack_parts` each return only their own curves. Editing a gear next to a rack doesn't touch the rack, and vice versa.

**Regression:** run `test_drawing.py`, `test_edit.py`, `test_repoint.py`, `test_planetary.py` and `test_extent_settings.py` before and after. Report any difference.

### Manual checklist for the user (things a script can't see)

Ask them to restart GearGremlin in Scripts and Add-Ins first. Then:

1. Select a line, run GearGremlin: the rack inputs show and the gear-only ones hide. Switch the selection to a circle and back.
2. Toggle Flip side and change the offset: the preview updates live.
3. OK, then extrude the rack.
4. Right-click a tooth: Edit Gear appears and opens the rack's settings. Change the module, then Update: the extrude follows.
5. Hovering over rack curves or gear circles in the pitch input doesn't highlight them as selectable.

If anything fails, read `gear_gremlin.log` before guessing.

### Definition of done for Step 1

- All pytest tests pass, old and new. SVGs generated.
- `test_rack.py` passes in Fusion, with no regressions in the existing scripts.
- The user has gone through the manual checklist.
- **SPEC.md** gains a "Racks" section (geometry, frame and phase convention, record, dialog, validation rows), and "racks" is removed from "Out of scope".
- **README**: a "Making a rack" usage section, the feature list, and Limitations ("A gear can't yet be meshed with a rack automatically").
- A report covering: deviations from this spec and why, everything marked **verify** and what Fusion actually did, timings, and what's still unverified.

## Step 2: Rack-and-pinion meshing (outline; refine with the user first)

- **Mesh with** on a circle gear accepts a rack line, and on a rack accepts a gear circle. Module, pressure angle and k must match (`same_system`, extended to `RackParams`). Internal gears can't mesh with a rack.
- **Tangency:** the pinion center is on the teeth side at distance `r` (its pitch radius) from the line. Contact point `C` = the foot of the perpendicular. Its `s_c` must fall within the drawn teeth, with a margin, or warn. The user constrains it with a line–circle tangent constraint. Warn if the distance is off by more than `TANGENCY_TOL_MM`.
- **Phase:** the rack phase at `C` is `q = frac((s_c − (p/2 + offset)) / p)`, where 0 means a tooth centerline is at `C`. The gear phase `p_g` is as in SPEC "Mesh alignment", with `d` = the direction from the gear center to `C` (= `−n`). Derivation: the gear's counter-clockwise tangent at `C` is `σ·u`, and `r·τ = p`, so:
  - teeth on the left (`σ = +1`): `p_g − q ≡ ½ (mod 1)`
  - teeth on the right (`σ = −1`): `p_g + q ≡ ½ (mod 1)`

  Solve for the pinion's θ₀, or for the rack's offset when the rack is the new part. **Must be confirmed** by the same "tooth faces gap at the contact point" pytest the gear pairs use, on both sides and several directions, before relying on it.
- **Contact ratio and collisions:** a linear version of `pair_report` / `pair_collides` (pinion against rack). Reuse the polygon-sweep approach of the existing collision tests, translating the rack by `r·Δθ` per step.
- **Undercut:** the pinion is already cut by this same cutter, so no new rule is needed. The existing undercut warning applies.
- **Edits:** editing a rack or its pinion warns when the other no longer lines up, as `neighbor_warnings` does for gears (it currently only looks at circles).
