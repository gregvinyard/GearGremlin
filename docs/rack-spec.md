# Spec: Rack gears (select a line, get a rack)

**Audience:** a Claude Code session running locally with Fusion 360 and its MCP server available.
**Why this exists:** the spec was written in a web session with no Fusion access, so none of it has been run. Treat every file/line reference as a pointer to verify, not a guarantee. The point of handing it to you is that you can test against the real Fusion.

## Goal

Today GearGremlin turns a sketch **circle** into an involute spur gear. Add the ability to select a sketch **line** and turn it into a **rack**: a straight-toothed gear of infinite radius. The line is the rack's **pitch line**.

Two steps. **Do Step 1 first, get it working and tested, and stop for review before Step 2.**

- **Step 1:** draw a standalone rack from a line, and make it editable.
- **Step 2:** rack-and-pinion meshing (a circle gear's "Mesh with" can be a rack, and vice versa).

## Step 1: Standalone rack

### User-facing behaviour

1. In a sketch, the user selects a line and runs GearGremlin (or right-clicks and chooses Edit Gear on an existing rack).
2. The dialog shows the rack options (below). The preview draws the teeth live, like it does for circles.
3. On OK, the line becomes construction geometry (as circles do today) and the rack outline is drawn in the sketch. Gear settings are stored as attributes so the rack can be edited later.

**Recommendation: same command, not a new one.** The pitch selection input accepts `SketchCircles` and `SketchLines`, and the dialog shows or hides inputs depending on what was picked. The user's mental model is "select a thing, make it a gear". If this turns out to be awkward with Fusion's selection filters, a separate "Rack" command is an acceptable fallback; say so in your report.

### Dialog inputs for a rack

| Input | Notes |
|---|---|
| Pitch line | `SketchLines` filter. The line is the pitch line. Direction is start → end. |
| **Teeth side** | **Required, user-selectable.** Dropdown: "Left of line" / "Right of line", relative to the start → end direction. The add-in cannot infer this, so it must be an explicit choice. Show it clearly in the preview, and remember the last choice. |
| Module | Shared with gears (`commands/common/inputs.py`). |
| Pressure angle | Shared. 14.5° / 20° / 25°. |
| Tooth height factor | Shared (`ci.add_height`). Same meaning as for gears. |
| Backlash | Shared. See "Backlash convention" below. |
| Offset along line | Length input, default 0. Shifts the tooth pattern along the line. This is the rack equivalent of "Rotation offset". |
| Resize line to whole teeth | Bool, default on. Mirrors "Resize circle to pitch diameter". |
| Body thickness | Length, default `3 × module`. The solid is built on the side away from the teeth, from the root line. `0` means draw only the toothed edge, with no closing lines. See decision point 1. |
| Info box | Pitch (`π × module`), tooth count, drawn length, warnings. |

Hide the gear-only inputs when a line is selected: gear type, teeth, rotation, reference circles, and (in Step 1) Mesh with.

### Geometry

- Pitch `p = π · module`. The pitch line is the line in the sketch.
- The tooth count comes from the line length: `N = floor(L / p)`. It is **derived**, not an input. Only **whole teeth** are drawn.
- **Resize on:** move the line's end point along the line direction so `L = N · p`. Use the same approach as `resize_circle` in `commands/gearProfile/drawing.py` (read it first: it handles dimensions, constraints, and a "hold" list, and returns a result with `ok`/`error`/`warnings`). If the line cannot be resized because a dimension or constraint stops it, return a clear error just as the circle path does. Do not silently draw a pattern that doesn't fit.
- **Resize off:** draw whole teeth from the line start, and warn how much of the line is left over.
- The tooth shape is a straight-flanked trapezoid. Flanks are inclined at the pressure angle to the normal of the pitch line. Addendum and dedendum follow the existing conventions. Read `cutter()`, `Cutter` and `gear_radii()` in `gearmath.py`, which already model an ISO 53 profile A rack cutter. **Reuse those constants** (addendum, dedendum, root fillet radius, how the height factor applies) so a rack and a gear made with the same settings agree. Don't invent new numbers.
- The root fillet is the same as the cutter's tip radius. If you can't reproduce it cleanly with sketch arcs, say so, and fall back to a straight root with a note in the report.
- Tooth positions: tooth `i` is centred at `s = p/2 + i·p + offset`, measured along the line from its start. If the offset pushes a tooth past either end, drop it. Document whatever convention you actually implement in the docstring: Step 2 depends on it.

### Backlash convention

Match exactly how `gearmath.py` applies backlash to gear teeth (see `tooth_half_angle` and `GearParams.backlash`). Then a rack and a gear with the same backlash setting end up with the same total clearance in Step 2. Write down in a docstring which convention you found (thinner tooth by `backlash`? split between the two?) and use the same one.

### Where the code goes

- **`gearmath.py` (pure Python, no `adsk`):** rack params, rack tooth profile builder returning a `Profile` made of `Line` and `Arc` segments in mm, a rack record with `to_attribute`/`from_attribute` like `GearRecord`, and check functions. Everything here must be unit-testable without Fusion.
- **`tests/test_gearmath.py`:** add pytest tests for the above (see "Tests").
- **`commands/gearProfile/drawing.py`** (or a new `rack_drawing.py` next to it if `drawing.py` is getting unwieldy): `make_rack(line, params, ...)` returning a `GearResult`-like object. It should mirror `make_gear`: `resize`, `finalize`, `edit`, preview vs. execute, `isComputeDeferred` around the drawing, and tagging parts (see below).
- **`commands/gearProfile/entry.py`:** the dialog changes. The pitch selection currently uses `addSelectionFilter('SketchCircles')` (about line 148) and `SketchCircle.cast` in `_selected_circle` and `command_pre_select`. Generalize them. The `inputChanged` handler shows or hides inputs.
- **Metadata:** store the rack record as an attribute on the line (group `GearGremlin`, a new name such as `rack`, distinct from `gear`). Tag drawn curves with the owning id (`PART_NAME`) the same way gears do. The existing note applies: attribute adds are slow, so tag minimally, as `tag_part` does, and not at all in preview.
- **Guards:** the equivalents of "This circle is already a gear." and "not a gear made by this tool". A line that already holds a rack record can't be made into a second rack.
- **Edit Gear:** `gear_for_entity` currently resolves a circle or one of its teeth to the gear circle. Extend it to resolve a line or one of a rack's teeth to the rack's line. Edit reopens the dialog with the stored settings, deletes the old parts, and redraws under the same id.
- **Feature re-pointing:** edits of circle gears keep extrudes and revolves attached (`capture_features` / `_Repoint` in `drawing.py`). Try to reuse that for racks. If it doesn't work, say so and **don't** fake it. It is a stretch goal for Step 1, not a blocker. Report what you found.
- **Settings:** remembered settings (`settings.py`) should include the teeth side, offset, and body thickness. Make sure old saved settings without these keys still load.

### Decision points: stop and ask the user

1. **Body thickness.** The spec above closes the outline (end lines plus a back line) so the rack extrudes straight away. If the user prefers only the toothed edge, flip the default to 0. Ask before shipping if it's unclear.
2. **Tooth phase convention** (the `p/2 + offset` rule above). If something else is more natural once you see it in Fusion, propose it. Don't change it silently, because Step 2 builds on it.
3. Anything the Fusion API forces you to do differently from this spec. Report it rather than working around it quietly.

### Tests

**Pure-Python (`python -m pytest tests`, no Fusion needed).** At minimum:
- tooth count vs. line length (exact multiple, just under, just over);
- flank angle equals the pressure angle;
- tooth thickness at the pitch line equals `p/2` minus backlash, per the convention you found;
- addendum and dedendum match `cutter()` for the same settings;
- tooth height factor changes tooth height as it does for gears;
- both sides give mirror-image profiles;
- the profile is continuous (each segment starts where the last ended) and closed when body thickness > 0;
- the rack record round-trips through its attribute string, and a malformed string returns `None` rather than raising;
- offset shifts the pattern by exactly the offset, modulo pitch.

The existing suite takes about 1.5 minutes. Run all of it, not just your new tests.

**In Fusion.** Add `dev_scripts/test_rack.py`, following the conventions of `dev_scripts/test_drawing.py` exactly: `ROOT` hard-coded at the top, a private package name via `load_drawing()`, a **new scratch design document** (never touch an existing one), `check(label, ok, detail)` printing PASS/FAIL, and results written to `tests/out/`. Run it through the Fusion MCP server. Cover:
- horizontal, vertical, and diagonal (e.g. 30° and 137°) lines, with the teeth on both sides of each. Check that the teeth really point to the chosen side, using geometry from the sketch and not a label;
- several module / pressure angle / height factor / backlash combinations;
- resize on: a line of an awkward length ends at `N · p`, and the end point moved along the line. Resize off: the leftover is reported and no partial tooth is drawn;
- a line with a fixed-length dimension and with horizontal/vertical constraints. The resize must either succeed or fail with a clear error, and must never leave the sketch half-edited;
- a very short line (fewer than 1 tooth) and a very long one. Short gives a clean error. For long, report the time taken, as `dev_scripts/time_preview.py` does for gears;
- the guard: running it on a line that is already a rack errors cleanly;
- the sketch has exactly the expected closed profile(s): count regions, and compare the profile's area to the analytic value. Then **extrude it** and confirm a body results;
- edit: change module, side, and offset on an existing rack. The old curves are gone, no orphans are left, and the id is unchanged;
- edit with an extrude built on the rack (the re-pointing stretch goal). Report what happens;
- a rack and circle gears in the same sketch don't interfere: `gear_circles`, `find_gear_circle` and `gear_for_entity` still return the right things.

**Regression.** The existing Fusion-side scripts (`test_drawing.py`, `test_edit.py`, `test_repoint.py`, `test_planetary.py`, `test_extent_settings.py`) must still pass. Run them before and after, and report any difference.

**Dialog (manual, if you can't drive it).** Some things can't be asserted from a script: the preview visibly updating, input visibility toggling when the selection changes between a circle and a line, and the right-click Edit Gear menu entry on a line. Drive what you can through the MCP server. For the rest, give the user a short numbered checklist to eyeball.

### Fusion gotchas already known in this repo

- The API works in **centimetres**; `gearmath.py` works in **millimetres**. Convert only at the boundary (`to_cm`/`to_mm` in `drawing.py`).
- Wrap bulk drawing in `sketch.isComputeDeferred = True`.
- Attribute adds are slow (a few ms each, growing with the design). Don't tag every curve.
- After changing add-in code, **stop and restart GearGremlin** in Scripts and Add-Ins before testing the real command. The dev scripts import under a private package name, so they pick up changes without a restart.
- Errors are also logged to `gear_gremlin.log` in the add-in folder.
- Pre-existing gears made by older versions carry older records; don't break reading them.

### Definition of done for Step 1

- All new and existing pytest tests pass.
- `dev_scripts/test_rack.py` passes in Fusion, and the existing Fusion scripts show no regressions.
- Make a rack from a line, extrude it, edit it, and confirm it all works. Report what you actually observed.
- README updated: a "Making a rack" usage section, the Features list, and the Limitations section (racks are no longer unsupported, but a pinion can't mesh with one yet).
- Report back before starting Step 2. Include the decision points above, anything that deviated from this spec, and what is still unverified.

## Step 2: Rack-and-pinion meshing (outline only)

Start only after Step 1 is reviewed. Rough shape, to be refined with the user:

- A circle gear's **Mesh with** accepts a rack's line, and a rack can mesh with a circle gear. Pressure angle, module, and tooth height must match, as they already do for gear pairs.
- A pinion tangent to the pitch line has its center at exactly `pitch_radius` from the line. The check is a point-to-line distance, plus a line-to-circle tangent constraint for the user's sketch.
- Phase alignment: set the pinion's `theta0` (or the rack's offset) from the contact point so a tooth faces a space there, using the stored tooth-phase convention from Step 1. See `align_theta0`, `contact_directions` and `mesh_error` in `gearmath.py` for the circle-circle versions.
- Contact ratio and collision checks against a rack (a linear version of `pair_report`/`pair_collides`).
- Undercut: a small pinion against a rack cut with the same cutter, as already modelled.
- Edit-propagation: editing a rack that a pinion meshes with should warn the pinion is now out of phase, as gear edits do.
