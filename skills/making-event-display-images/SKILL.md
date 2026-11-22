---
name: making-event-display-images
description: Save pictures (PNG) of Mu2e events with the Offline EventDisplay in batch, with no X display and no clicks, from an art file. Use when asked for an event display, an event picture, "show me event run:subrun:event", to draw MC trajectories, track fits, straw hits, CRV pulses or calorimeter hits of an event, or to make display images for a note, a talk or a list of events. Covers the imageOutput fcl table (Mu2e/Offline#2203), finding collection names, views, ranges and true scale, and which Offline to use while no release has the feature.
compatibility: Requires a Mu2e gpvm with cvmfs and muse, an Offline that has Mu2e/Offline#2203 (an official release newer than v13_47_01, or the HEAD CI build of main until one exists), and read access to the art file. No X display.
metadata:
  version: "1.0.0"
  last-updated: "2026-11-22"
---

# Making event display images

The Offline `EventDisplay` module can save pictures of events by itself: no window, no clicks,
no X display. An fcl table, `imageOutput`, names what to draw and which views to save
(Mu2e/Offline#2203, merged 2026-11-22).

Use it for "show me event run:subrun:event", for pictures in a note or a talk, or for a set of
events. To browse events by hand, a person runs the same display without the table, on a screen.

**Freedom:** low for steps 1 to 6, follow them in order. High for the choice of views and ranges.

Every `muse setup` needs its own new shell, and `source
/cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh` before it. Run `muse setup` plainly: not in a
pipe, not in `$( )`.

## 1. Set up an Offline that has `imageOutput`

Use the official SimJob release if it has the feature, and HEAD (the latest CI build of `main`)
until one does. Build nothing yourself.

Offline up to and including v13_47_01 does not have it; that covers every SimJob release up to
MDC2025be. **Such a release ignores the table without a message** and tries to open a window:
the job then crashes (exit 139, no screen) or waits for clicks forever (with a screen). So
check before anything else:

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup SimJob <campaign>          # the official release; or: Offline <version>
for d in ${MU2E_SEARCH_PATH//:/ }; do
  f=$d/Offline/EventDisplay/fcl/EventDisplayBase.fcl
  [ -f $f ] && { grep -c imageOutput $f; break; }
done
```

- A number above 0: this release has it. Use it.
- 0: it does not. Use HEAD, in a new shell:

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup HEAD                       # the latest CI build of main; no workdir needed
```

Run the check loop again in that shell: it must print a number above 0.

- HEAD is not a release, it moves with `main`: a new shell can get a newer build. Say in your
  report which build you used: `echo $MUSE_WORK_DIR` (the folder name starts with the Offline
  commit).
- Go back to the official release as soon as one has the feature (the first Offline tag after
  v13_47_01, and the SimJob built on it).

## 2. Find the art file and the event

- The reconstruction output (`mcs`) is the usual input: it has the MC truth, the track fits and
  the CRV pulses. If the event comes from an ntuple (`nts`), take the `mcs` file with the same
  sequencer (the `001430_00000002` part of the name).
- Locate it (see `finding-data-sam`) and keep the full path for step 5:

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops                        # its own shell, not the one of step 1
f=mcs.mu2e.CosmicSignalOnSpill.MDC2025au_best_v1_5.001430_00000002.art
d=$(SAM_EXPERIMENT=mu2e samweb locate-file $f | sed 's/^[a-z]*://; s/(.*//')
cat "$d/.(get)($f)(locality)"         # ONLINE...: on disk. NEARLINE alone: on tape, prestage first
echo $d/$f                            # the full path
```

- Read the file in place. Never copy a tape-backed file to disk. On a gpvm the `/pnfs` path
  needs no token.
- `-e run:subrun:event` on the `mu2e` command line jumps straight to the event, also in an
  18 GB file.
- A newer Offline usually reads an older file: an MDC2025au file made with v13_33_00 was read
  with HEAD of 2026-11-22. That is not guaranteed. If it fails, say so; do not switch files
  silently.
- Not every event of an `mcs` file has a fit or hits. In a conversion-electron file the first
  three events had neither, and their pictures showed the MC particles only. If no event was
  given, list which events have a fit, in a shell set up as in step 1 (here for the downstream
  electron fit, `KKDe`):

```bash
mu2e -c Offline/Print/fcl/printKalSeed.fcl -s <full path of the file> -n 20 2>&1 |
  awk '/PrintModule Run\/Subrun\/Event/{ev=$4":"$5":"$6}
       /ProductPrint mu2e::KalSeeds_KKDe_/{getline; print ev, $3, "fits"}'
```

  It prints lines like `1430:0:8 0 fits` and `1430:0:9 1 fits`. Take an event with a fit.

## 3. Write the fcl

Start from the display configuration of the sample's detector geometry, and add the table:

```
#include "Offline/EventDisplay/fcl/EventDisplayRun1a.fcl"

physics.analyzers.eventdisplay.imageOutput : {
  tracks      : [ "SimParticle:compressRecoMCs:", "KalSeed:KKDe:" ]
  trackerHits : "KalSeed:KKDe:"
  crvHits     : "CrvRecoPulse:SelectReco:"
  views   : [
    { view : "side" file : "evd-%r-%s-%e-side.png" range : [ -2500, 2700, -1000, 4500, -5750, 3719 ] },
    { view : "end"  file : "evd-%r-%s-%e-end.png" },
    { view : "perspective" file : "evd-%r-%s-%e-3d.png" }
  ]
}
```

| key | meaning | default |
|---|---|---|
| `views` | list of images to save for every event (required, not empty) | |
| `views[].view` | `side`, `end`, `top`, `allTracks` or `perspective` | |
| `views[].file` | file name; `%r`, `%s`, `%e` become run, subrun, event; the extension sets the format (PNG is the tested one) | |
| `views[].range` | `[minx, maxx, miny, maxy, minz, maxz]` in mm | the view's own box |
| `tracks` | entries of the display's track list to draw | `SimParticle:g4run:` if the file has it |
| `trackerHits` | one entry of the tracker hit list | `StrawHit:makeSH:` if the file has it |
| `caloHits` | one entry of the calorimeter hit list | `CaloHit:CaloHitsMaker:` if the file has it |
| `crvHits` | one entry of the CRV hit list | none |
| `imageSize` | `[width, height]` in pixels | `[1298, 754]` |

An official `mcs` file has none of the three defaults, so name what you want, or the picture
shows the detector only. `"Nothing Selected"` is a valid entry of every hit list and turns that
kind off.

Which configuration (all in `Offline/EventDisplay/fcl/`):

| sample | configuration | tested in batch |
|---|---|---|
| official MDC2025 samples in the Run 1A geometry, such as `CosmicSignalOnSpill` | `EventDisplayRun1a.fcl` | yes |
| samples with `Extracted` in the name (extracted position, no field) | `EventDisplayExtracted.fcl`, draws the CRV bars | yes |
| other geometries | `EventDisplayRun1b.fcl`, `EventDisplayRun1.fcl`, `EventDisplayRun2.fcl`, `EventDisplayMDC2020.fcl`, the `Wideband` and `CrvOnly` ones | no |

The job log prints `Geometry file:`. It should be the geometry the sample was made with. If you
do not know that geometry, say which configuration you assumed. Never use
`EventDisplayBase.fcl` directly.

## 4. Find the names of the collections

An entry is `Class:moduleLabel:instanceName`, as the display's lists show it. To see what a
file offers, ask for a name that does not exist. The job stops with exit status 1 and prints
the list. `views` must still be there:

```
physics.analyzers.eventdisplay.imageOutput : {
  caloHits : "?"                      # or trackerHits : "?", crvHits : "?", tracks : [ "?" ]
  views    : [ { view : "side" file : "list.png" } ]
}
```

```
"?" is not offered as calorimeter hits for this event. The list has:
  "Nothing Selected"
  "StepPointMC:compressRecoMCs:virtualdetector"
  "CaloDigi:SelectReco:"
  "CaloHit:CaloHitMaker:"
  ...
```

It checks `trackerHits`, `caloHits`, `crvHits` and then `tracks`, and stops at the first name
it does not find. So ask for one kind at a time.

What an official MDC2025 `mcs` file offers:

| kind | entries |
|---|---|
| tracks | `SimParticle:compressRecoMCs:` (MC truth), the fits `KalSeed:KKDe:`, `KalSeed:KKUe:`, `KalSeed:KKDmu:`, `KalSeed:KKUmu:`, and trigger fits (`KalSeed:Trig...`). Extracted position: `KalSeed:KKLine:` |
| tracker hits | `KalSeed:` entries, the same names as the fits: the straw hits of that fit. `StepPointMC:` collections. No `StrawHit` collection |
| calorimeter hits | `CaloHit:CaloHitMaker:`, `CaloHit:CaloHitFastMaker:calo`, `CaloHit:CaloHitFastMaker:caphri`, `CaloDigi:SelectReco:`, and `StepPointMC:` collections |
| CRV hits | `CrvRecoPulse:SelectReco:` |

Fit names: `KK` is the KinKal fit, `D` or `U` the downstream or upstream direction, `e` or `mu`
the particle hypothesis. `KKLine` is the straight-line fit.

## 5. Run

In a shell set up as in step 1, with the full path from step 2:

```bash
mu2e -c evd.fcl -s <full path of the file> -e 1430:42354:139597 -n 1 > evd.log 2>&1; echo "exit $?"
```

- Exit status 0, and one line `png file ... has been created` per image in the log. The images
  land in the current directory unless `file` has a path.
- 5 to 7 s for one event. The first job of a session can take about a minute, while the
  conditions database loads.
- No X display is used, whether or not `DISPLAY` is set.
- Any other exit status is a failure. Read the end of the log and report it. Common ones: a
  name that is not offered (step 4); exit 139 on an Offline without `imageOutput` (step 1).

## 6. Look at the picture

Open each image (an agent reads the PNG) and check, before showing it to anyone:

- the run, subrun and event printed in the picture are the ones asked for;
- the legend names the track collections asked for, and their lines are in the picture;
- hits are there: they have no legend entry, only coloured markers and the time scale at the
  top right;
- the range holds what the picture is about.

A picture with no fit line and `nan` in the time scale is not a failure of the display: that
event has no fit and no hits in the collections asked for. Pick another event (step 2).

## Views, ranges and true scale

- Coordinates are the display's frame, in mm: origin at the tracker centre, x = Mu2e x + 3904,
  z = Mu2e z - 10171, y unchanged.
- `side`: z across, y up. `end`: x across, y up. `top`: z across, x up. `perspective`: 3D.
  `allTracks` is the side view with a box that holds every selected track.
- Without a `range`, `side`, `end` and `top` show the detector with the same scale on both
  axes. `allTracks` does not: it stretches the box of the tracks.
- A `range` sets what the drawing area shows. It does not clip: what lies outside is still drawn
  in the margins of the image. For the same scale on both axes make (range across) / (range up)
  = width / height, 1.72 for the default image size.
- The range of the third axis (x in `side`, z in `end`, y in `top`) changes nothing in the
  picture. It only has to be valid: min below max.

| picture | view | range (same scale on both axes) |
|---|---|---|
| detector region | `side` | `[ -2500, 2700, -1000, 4500, -5750, 3719 ]` |
| a cosmic from 15 m above | `side` | `[ -2500, 2700, -1000, 15500, -15500, 12908 ]` |
| looking along the beam | `end` | `[ -5165, 5165, -1000, 5000, -5750, 3719 ]` |
| from above | `top` | `[ -2750, 2750, -1000, 4500, -5750, 3719 ]` |

## What the picture shows, and traps

- **Top view: the numbers on the x axis are wrong.** The tracker centre, x = 0, sits at the
  label 2000. The display on a screen has the same fault. The z axis, the scale and the content
  are right. Do not read x values off a top view.
- The display draws the stopping target, the tracker and the calorimeter. The building, the
  shielding and, outside the extracted and wideband configurations, the CRV bars are not drawn:
  a cosmic muon seems to start in empty space.
- CRV pulses of the top modules are drawn near y = 4800 mm.
- A SimParticle without a stored MC trajectory is a straight line from its start to its end.
  Official `mcs` files keep trajectories for few particles.
- Options of the display's Setup dialog (white background, CRV bars, support structures) cannot
  be set from the fcl.
- With `-n` larger than 1, every event gets its images. Without `%e` in the file name each event
  overwrites the last one.
- An event with no selected hits shows `nan` in the time legend.
- The images of two runs are identical byte for byte. They differ slightly from what the Save
  button of the display gives on a screen (fonts), with the same content.
