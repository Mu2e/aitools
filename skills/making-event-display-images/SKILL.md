---
name: making-event-display-images
description: Save pictures (PNG) of Mu2e events with the Offline EventDisplay in batch, with no X display and no clicks, from an art file. Use when asked for an event display, an event picture, "show me event run:subrun:event", to draw MC trajectories, track fits, straw hits, CRV pulses or calorimeter hits of an event, or to make display images for a note, a talk or a list of events. Covers the imageOutput fcl table (Mu2e/Offline#2203), finding collection names, views, ranges and true scale, and which Offline to use while no release has the feature.
compatibility: Requires a Mu2e gpvm with cvmfs and muse, an Offline that has Mu2e/Offline#2203 (an official release newer than v13_47_01, or the HEAD CI build of main until one exists), and read access to the art file. No X display.
metadata:
  version: "1.0.0"
  last-updated: "2026-11-22"
---

# Making event display images

The Offline `EventDisplay` saves pictures of events in batch: no window, no clicks, no X display.
An fcl table, `imageOutput`, says what to draw and which views to save (Mu2e/Offline#2203).

Follow steps 1 to 6 in order; the choice of views and ranges is free. Every `muse setup` needs a
new shell, `source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh` before it, and must not run
in a pipe or in `$( )`.

## 1. Set up an Offline that has `imageOutput`

Use the official SimJob release if it has the feature, else HEAD. Build nothing. Offline up to
v13_47_01 (SimJob up to MDC2025be) does not have it and **ignores the table without a message**:
the job crashes (exit 139) or waits for clicks. So check:

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup SimJob <campaign>
for d in ${MU2E_SEARCH_PATH//:/ }; do
  f=$d/Offline/EventDisplay/fcl/EventDisplayBase.fcl
  [ -f $f ] && { grep -c imageOutput $f; break; }
done
```

If it prints 0, use `muse setup HEAD` (the latest CI build of `main`) in a new shell, and check
again. HEAD moves with `main`: report the build you used (`echo $MUSE_WORK_DIR`).

## 2. Find the file and the event

Use the reconstruction output (`mcs`): it has the MC truth, the fits and the CRV pulses. Locate
it in a shell of its own, and read it in place (never copy a tape-backed file):

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
f=mcs.mu2e.CosmicSignalOnSpill.MDC2025au_best_v1_5.001430_00000002.art
d=$(SAM_EXPERIMENT=mu2e samweb locate-file $f | sed 's/^[a-z]*://; s/(.*//')
cat "$d/.(get)($f)(locality)"         # NEARLINE alone: on tape, prestage first
echo $d/$f                            # the full path, for the next steps
```

Many events have no fit and no hits. If no event was given, list the ones with a fit, in the
shell of step 1 (here the downstream electron fit, `KKDe`), and take one:

```bash
mu2e -c Offline/Print/fcl/printKalSeed.fcl -s <full path> -n 20 2>&1 |
  awk '/PrintModule Run\/Subrun\/Event/{ev=$4":"$5":"$6}
       /ProductPrint mu2e::KalSeeds_KKDe_/{getline; print ev, $3, "fits"}'
```

## 3. Write the fcl

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

| key | meaning |
|---|---|
| `views` | required; one image per entry and event |
| `view` | `side`, `end`, `top`, `allTracks` or `perspective` |
| `file` | `%r`, `%s`, `%e` become run, subrun, event; without `%e` each event overwrites the last |
| `range` | optional, `[minx, maxx, miny, maxy, minz, maxz]` in mm |
| `tracks` | list of entries of the track list |
| `trackerHits`, `caloHits`, `crvHits` | one entry each; `"Nothing Selected"` turns the kind off |
| `imageSize` | optional, `[width, height]` in pixels, default `[1298, 754]` |

Name what you want drawn: the display's own defaults do not exist in an `mcs` file.

Include `EventDisplayRun1a.fcl` for MDC2025 samples in the Run 1A geometry, and
`EventDisplayExtracted.fcl` for samples with `Extracted` in the name. The other configurations
in `Offline/EventDisplay/fcl/` are untested in batch. Never include `EventDisplayBase.fcl` alone.

## 4. Find the names of the collections

An entry is `Class:moduleLabel:instanceName`. To list what a file offers, ask for `"?"`, one
kind at a time (`tracks : [ "?" ]`, or `caloHits : "?"`, ...), with `views` still there. The job
stops with exit status 1 and prints the list.

Official MDC2025 `mcs` files offer:

| kind | entries |
|---|---|
| tracks | `SimParticle:compressRecoMCs:` (MC truth); fits `KalSeed:KKDe:`, `KKUe`, `KKDmu`, `KKUmu` (downstream or upstream, electron or muon); extracted position: `KalSeed:KKLine:` |
| tracker hits | the same `KalSeed:` names: the straw hits of that fit |
| calorimeter hits | `CaloHit:CaloHitMaker:`, `CaloDigi:SelectReco:` |
| CRV hits | `CrvRecoPulse:SelectReco:` |

## 5. Run

In the shell of step 1:

```bash
mu2e -c evd.fcl -s <full path> -e 1430:42354:139597 -n 1 > evd.log 2>&1; echo "exit $?"
```

Success is exit status 0 and one line `png file ... has been created` per image, in 5 to 7 s
(the first job of a session can take a minute). Any other exit status is a failure: report the
end of the log.

## 6. Check the picture

Read each PNG before showing it:

- the run, subrun and event printed in it are the ones asked for;
- the legend names the track collections, and their lines are there;
- hits show as coloured markers, with a time scale at the top right.

No fit line and `nan` in the time scale mean an event without fit and hits: pick another one
(step 2).

## Views and ranges

- Frame, in mm: origin at the tracker centre, x = Mu2e x + 3904, z = Mu2e z - 10171, y unchanged.
- `side`: z across, y up. `end`: x across, y up. `top`: z across, x up. `allTracks`: side view
  stretched to hold every track.
- Without `range`, `side`, `end` and `top` have the same scale on both axes. With one, make
  (range across) / (range up) = width / height (1.72 by default) to keep it. The range does not
  clip, and the range of the third axis changes nothing.

| picture | view | range, same scale on both axes |
|---|---|---|
| detector region | `side` | `[ -2500, 2700, -1000, 4500, -5750, 3719 ]` |
| cosmic from 15 m above | `side` | `[ -2500, 2700, -1000, 15500, -15500, 12908 ]` |
| along the beam | `end` | `[ -5165, 5165, -1000, 5000, -5750, 3719 ]` |
| from above | `top` | `[ -2750, 2750, -1000, 4500, -5750, 3719 ]` |

## Traps

- Top view: the numbers on the x axis are wrong (x = 0 sits at the label 2000). Scale and
  content are right.
- Not drawn: the building, the shielding, and the CRV bars (except in the extracted
  configuration). A cosmic muon seems to start in empty space.
- A SimParticle without a stored trajectory is a straight line from its start to its end.
- White background and the other options of the display's Setup dialog cannot be set.
