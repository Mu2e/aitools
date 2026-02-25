---
name: finding-data-metacat
description: Find and manage Mu2e data using modern metacat, Rucio, and mdh tools. Use when querying with metacat MQL, uploading files, checking locations, prestaging from tape, or working with the future data handling stack.
compatibility: Requires mu2einit, muse setup ops, metacat, mdh, Rucio
metadata:
  version: "1.0.0"
  last-updated: "2026-02-13"
---

# Mu2e Data Handling - Metacat, Rucio, and mdh

**Modern data handling tools** (replacing SAM as of 2025-2026)

See also: [datahandling-overview.md](datahandling-overview.md) for architecture and [datahandling-sam.md](datahandling-sam.md) for legacy SAM tools (still ~90% in use during transition).

---

## Quick Reference

**Setup:**
```bash
mu2einit
muse setup ops
```

**Common commands:**
```bash
# Find datasets
metacat dataset list mu2e:*              # All production datasets
metacat dataset list $USER:*             # Your datasets

# Query files
metacat query files from <dataset>       # List files in dataset
metacat file show -m <file>              # Show file metadata

# Check locations and status
mdh print-url -s path -l tape <file>     # Show dCache path
mdh verify-dataset <dataset>             # Check if files on disk/tape
mdh query-dcache -o -v <dataset>         # Check ONLINE/NEARLINE status

# Prestage from tape
mdh prestage-files <dataset>             # Copy from tape to disk

# Upload personal files
mdh create-metadata <file> > <file>.json # Create metadata
mdh declare-file -                       # Declare to metacat (from stdin)
mdh copy-file -s -l scratch -            # Copy to dCache scratch
```

---

## Introduction

**Modern Mu2e data handling stack:**
- **metacat**: File catalog database and query tool
- **Rucio**: File location catalog and data movement service
- **mdh**: Mu2e convenience scripts wrapping metacat and Rucio
- **dCache**: Distributed storage (tape/persistent/scratch)

**Authentication:**
- Generally automatic if you have a Kerberos ticket
- Run `mu2einit` to get credentials

**Dataset Monitor:**
- View all datasets and status: https://mu2e.fnal.gov/atwork/computing/ops/datasetMon.html

---

## Files and Datasets

### Registered vs Unregistered Files

**Registered files:**
- Cataloged in metacat database
- Must follow naming conventions
- Located in standard dCache directories
- Subject to collaboration policies
- Can be owned by collaboration (`mu2e`) or user (your username)

**Unregistered files:**
- For local/temporary use
- Not in catalog
- No naming restrictions
- Your scratch area: `/pnfs/mu2e/scratch/users/$USER`

### File Naming Convention

All registered files must follow the six-field pattern:

```
tier.owner.description.config.sequencer.format
```

**Example:**
```
sim.MyUsername.dh_tutorial.2026-02-12.001000_000001.art
```

**Fields:**
- **tier**: Data processing level (sim, dig, mcs, nts, etc.)
- **owner**: `mu2e` (collaboration) or your username
- **description**: Brief descriptor for this file type
- **config**: Configuration identifier or date
- **sequencer**: Run/subrun number (unique per file)
- **format**: File format (art, root, tar, tbz, fcl)

### Dataset Names

**Dataset** = file name without the sequencer field:

```
tier.owner.description.config.format
```

**Example:**
```
sim.MyUsername.dh_tutorial.2026-02-12.art
```

All files in a dataset are considered "more of the same."

### Namespaces and Data Identifiers (DIDs)

**Namespace** = owner prefix:

```
owner:tier.owner.description.config.sequencer.format   # File DID
owner:tier.owner.description.config.format             # Dataset DID
```

**Example:**
```
MyUsername:sim.MyUsername.dh_tutorial.2026-02-12.001000_000001.art   # File
MyUsername:sim.MyUsername.dh_tutorial.2026-02-12.art                 # Dataset
```

**Your namespace:**
- Created once (see [Rucio wiki](https://mu2ewiki.fnal.gov/wiki/Rucio#create_user_metacat_namespace))
- Check if it exists: `metacat namespace list $USER`

---

## Finding Files

### List Datasets

**All production datasets:**
```bash
metacat dataset list mu2e:*
```

**Your datasets:**
```bash
metacat dataset list $USER:*
```

**View on monitor page:**
- https://mu2e.fnal.gov/atwork/computing/ops/datasetMon.html

### Query Files in a Dataset

**List all files:**
```bash
DS=mu2e:mcs.mu2e.dh_test.000.art
metacat query files from $DS
```

**Query with conditions:**
```bash
metacat query files from $DS where rs.first_subrun=2
```

**Count files:**
```bash
metacat query "files from $DS" | wc -l
```

### Show File Metadata

**Display file metadata:**
```bash
FILE=mu2e:mcs.mu2e.dh_test.000.001200_000002.art
metacat file show -m $FILE
```

**Metadata fields include:**
- File size, checksum (CRC), event counts
- Run/subrun information
- Parent files, data tier
- File format, art version

---

## File Locations and Protocols

### dCache Storage Areas

Three types of dCache storage:

| Nickname | Description | Path Prefix | Retention |
|----------|-------------|-------------|-----------|
| **tape** | Tape-backed persistent | `/pnfs/mu2e/tape/` | Permanent, migrates to/from tape |
| **disk** | Persistent disk | `/pnfs/mu2e/persistent/` | Permanent, disk-only |
| **scratch** | Temporary | `/pnfs/mu2e/scratch/` | Auto-purged in 1-2 weeks |

### Find File Locations

**Print dCache path for a file:**
```bash
FILE=mu2e:mcs.mu2e.dh_test.000.001200_000002.art

# Show path in tape area
mdh print-url -s path -l tape $FILE

# Show path in scratch area  
mdh print-url -s path -l scratch $FILE
```

**From stdin (pipe files from dataset query):**
```bash
echo $FILE | mdh print-url -s path -l tape -
```

**List with ls:**
```bash
ls -l $(mdh print-url -s path -l tape $FILE)
```

### Generate File URLs

**Root URLs (for art jobs):**
```bash
DS=mu2e:mcs.mu2e.dh_test.000.art

# Root URLs for tape-backed files
metacat query files from $DS | mdh print-url -l tape -s root -
```

**Output formats:**
- `-s path`: Filesystem path (`/pnfs/mu2e/...`)
- `-s root`: Root protocol URL (`root://...`)
- `-s xrootd`: XRootD URL
- `-s uri`: Generic URI

**Storage locations:**
- `-l tape`: Tape-backed area
- `-l disk`: Persistent disk
- `-l scratch`: Scratch area

### Verify Dataset Locations

**Check which files are on disk vs tape:**
```bash
DS=mu2e:mcs.mu2e.dh_test.000.art
mdh verify-dataset $DS
```

**Query dCache status:**
```bash
mdh query-dcache -o -v $DS
```

**Status meanings:**
- `NEARLINE`: Only on tape (needs prestaging)
- `ONLINE`: Only on disk (ready to read)
- `ONLINE_AND_NEARLINE`: On both disk and tape

---

## Prestaging Files

Files in tape-backed dCache must be **prestaged** (copied from tape to disk) before efficient reading.

### Check if Prestaging Needed

**Query dCache status:**
```bash
DS=mu2e:mcs.mu2e.dh_test.000.art
mdh query-dcache -o -v $DS
```

If results show `NEARLINE`, files are tape-only and need prestaging.

### Request Prestaging

**Prestage entire dataset:**
```bash
mdh prestage-files $DS
```

**This command:**
- Issues prestage requests to tape system
- Monitors progress periodically
- Blocks until all files are staged
- Can be interrupted and restarted

**Monitor-only mode (skip new requests):**
```bash
mdh prestage-files -m $DS
```

**Verbose progress updates:**
```bash
mdh prestage-files -v $DS
```

**Prestage time varies:**
- Small dataset, drives available: Minutes
- Large dataset, busy drives: Hours to days

### Work with Partially Prestaged Datasets

**List only files already on disk:**
```bash
mdh query-dcache -o -v $DS \
  | grep ONLINE | awk '{print $2}' \
  | mdh print-url -s root -
```

This generates root URLs only for files ready to read.

---

## Uploading Personal Files

**Important:** All registered files must use `mdh` commands to implement proper protocols and policies.

### Prerequisites

**Check your namespace exists:**
```bash
metacat namespace list $USER
```

If missing, create it once: [Rucio namespace procedure](https://mu2ewiki.fnal.gov/wiki/Rucio#create_user_metacat_namespace)

### Step 1: Create Files with Proper Names

**Example: Create test files**
```bash
SFILE1=sim.${USER}.dh_tutorial.$(date +%F).001000_000001.art
SFILE2=sim.${USER}.dh_tutorial.$(date +%F).001000_000002.art
DS=${USER}:sim.${USER}.dh_tutorial.$(date +%F).art

# Copy from example sources (or create your own)
SDIR=/cvmfs/mu2e.opensciencegrid.org/DataFiles/Validation
cp $SDIR/sim.OWNER.dh_tutorial.CONFIG.001000_000001.art ./$SFILE1
cp $SDIR/sim.OWNER.dh_tutorial.CONFIG.001000_000002.art ./$SFILE2

# Verify names
ls -l sim.*.art
```

### Step 2: Create Metadata

**Generate metadata JSON for each file:**
```bash
mdh create-metadata $SFILE1 > ${SFILE1}.json
mdh create-metadata $SFILE2 > ${SFILE2}.json
```

**Or batch process:**
```bash
ls -1 sim.*.art | while read FF; do
  mdh create-metadata $FF > ${FF}.json
done

# Verify metadata files created
ls -l sim*.art.json
```

**Metadata includes:**
- File size, checksums
- Run/subrun, event counts
- Art version, data tier
- Parent files (if applicable)

### Step 3: Declare Files to Metacat

**Declare all metadata files:**
```bash
ls -1 sim.*.art.json | mdh declare-file -
```

**The `-` reads file list from stdin.**

**Useful switches:**
- `-v`: Verbose output
- `-n`: Dry-run (show what would be done)
- Run `mdh declare-file -h` for all options

### Step 4: Copy Files to dCache

**Copy to scratch dCache:**
```bash
ls -1 sim.*.art | mdh copy-file -s -l scratch -
```

**Options:**
- `-s`: Read file list from stdin
- `-l scratch`: Target scratch area
- `-l disk`: Target persistent disk (if approved)

**Verify file in dCache:**
```bash
ls -l $(mdh print-url -l scratch -s path $SFILE1)
```

### Step 5: Verify in Metacat

**Query your dataset:**
```bash
metacat query files from $DS
```

**Show file metadata:**
```bash
metacat file show -m ${USER}:${SFILE1}
```

### Optional: Declare Locations to Rucio

**For permanent datasets or grid job input:**

If files will be used in grid jobs or should be tracked by Rucio, declare locations:

```bash
# WARNING: Rucio records are permanent!
# Only do this for finalized datasets

mdh locate-dataset -l scratch $DS
```

**Note:** The tutorial skips this step for practice datasets because Rucio records cannot be easily deleted.

### Cleanup: Delete Test Files

**Delete catalog records and physical files:**
```bash
metacat query files from $DS | mdh delete-files -v -d -l scratch -c -
```

**Options:**
- `-d`: Delete physical files
- `-l scratch`: From scratch location
- `-c`: Delete catalog records
- `-v`: Verbose output
- `-`: Read file list from stdin

**Remove local files:**
```bash
rm *dh_tutorial*
```

---

## Metacat Command Reference

### Authentication

```bash
metacat auth whoami          # Show current user
metacat auth login           # Log in
metacat auth mydn            # Show DN
metacat auth list            # List auth tokens
metacat auth export          # Export token
metacat auth import          # Import token
```

### Dataset Commands

```bash
metacat dataset create <dataset>         # Create new dataset
metacat dataset show <dataset>           # Show dataset info
metacat dataset files <dataset>          # List files in dataset
metacat dataset list <pattern>           # List datasets matching pattern
metacat dataset add-files <dataset> ...  # Add files to dataset
metacat dataset remove-files ...         # Remove files from dataset
metacat dataset update <dataset>         # Update dataset metadata
metacat dataset remove <dataset>         # Remove dataset
```

### File Commands

```bash
metacat file show <file>              # Show file info
metacat file show -m <file>           # Show with metadata
metacat file declare <file> <json>    # Declare file with metadata
metacat file declare-many <jsonfile>  # Declare multiple files
metacat file datasets <file>          # Show datasets containing file
metacat file update <file>            # Update file record
metacat file update-meta <file>       # Update metadata
metacat file retire <file>            # Mark file as retired
metacat file name <fid>               # Get file name from ID
metacat file fid <name>               # Get file ID from name
```

### Query Commands

**Query syntax uses MQL (Metacat Query Language):**

```bash
metacat query "files from <dataset>"
metacat query "files from <dataset> where <condition>"
metacat query -q <query-file>
```

**Common query patterns:**
```bash
# Files in dataset with conditions
metacat query "files from mu2e:dataset.art where rs.first_subrun > 100"

# Files by metadata
metacat query "files where dh.tier=sim and file_size > 1000000000"

# Multiple datasets
metacat query "files from mu2e:dataset1.art, mu2e:dataset2.art"
```

### Namespace Commands

```bash
metacat namespace list <pattern>     # List namespaces
metacat namespace show <namespace>   # Show namespace info
metacat namespace create <namespace> # Create namespace
```

### Other Commands

```bash
metacat category list           # List metadata categories
metacat category show <cat>     # Show category details
metacat named_query create ...  # Create named query
metacat named_query list        # List named queries
metacat named_query show <name> # Show named query
metacat version                 # Show versions
metacat validate <jsonfile>     # Validate metadata JSON
```

---

## mdh Command Reference

### File Operations

**Compute CRC checksum:**
```bash
mdh compute-crc <file>
```

**Print file URLs/paths:**
```bash
mdh print-url [options] <file>
  -s path|root|xrootd|uri    # Output format
  -l tape|disk|scratch       # Storage location
  -                          # Read files from stdin
```

**Query dCache status:**
```bash
mdh query-dcache [options] <dataset>
  -o          # Show online/nearline status
  -v          # Verbose output
```

**Create metadata:**
```bash
mdh create-metadata <file> > <file>.json
```

### Catalog Operations

**Declare files to metacat:**
```bash
mdh declare-files [options]
  -             # Read file list from stdin
  -v            # Verbose
  -n            # Dry-run
```

**Locate dataset in Rucio:**
```bash
mdh locate-dataset [options] <dataset>
  -l tape|disk|scratch    # Storage location to register
```

### Data Movement

**Copy files to/from/within dCache:**
```bash
mdh copy-files [options]
  -s              # Read file list from stdin
  -l tape|disk|scratch   # Target location
  -v              # Verbose
```

**Prestage files from tape:**
```bash
mdh prestage-files [options] <dataset>
  -m     # Monitor only (skip new requests)
  -v     # Verbose progress updates
```

### Dataset Management

**Verify dataset:**
```bash
mdh verify-dataset <dataset>
```

Checks:
- File existence in dCache
- Location status (tape/disk)
- File counts match catalog

**Delete files:**
```bash
mdh delete-files [options]
  -c              # Delete catalog records
  -d              # Delete physical files
  -l tape|disk|scratch   # Location to delete from
  -v              # Verbose
  -               # Read file list from stdin
```

### Grid Operations

**Upload grid job outputs:**
```bash
mdh upload-grid [options]
```

See `mdh upload-grid -h` for details on grid upload workflows.

---

## Common Workflows

### 1. Find and Query a Dataset

```bash
# Search for datasets
metacat dataset list mu2e:*beam-g4s1*

# Pick a dataset
DS=mu2e:sim.mu2e.example-beam-g4s1.1812a.art

# List files
metacat query files from $DS

# Count files
metacat query files from $DS | wc -l

# Show first file metadata
FILE=$(metacat query files from $DS | head -1)
metacat file show -m mu2e:$FILE
```

### 2. Check Location and Prestage

```bash
DS=mu2e:sim.mu2e.example-beam-g4s1.1812a.art

# Check if files are on disk
mdh verify-dataset $DS

# Query detailed status
mdh query-dcache -o -v $DS

# If NEARLINE (tape-only), prestage
mdh prestage-files -v $DS

# Generate root URLs for art job
metacat query files from $DS | mdh print-url -l tape -s root -
```

### 3. Upload Personal Simulation

```bash
# Create files with proper names
MYFILE=sim.${USER}.myanalysis.$(date +%F).001000_000001.art
MYDS=${USER}:sim.${USER}.myanalysis.$(date +%F).art

# (Generate or copy your file to $MYFILE)

# Create and declare metadata
mdh create-metadata $MYFILE > ${MYFILE}.json
echo ${MYFILE}.json | mdh declare-file -

# Copy to dCache scratch
echo $MYFILE | mdh copy-file -s -l scratch -

# Verify
metacat query files from $MYDS
ls -l $(mdh print-url -l scratch -s path ${USER}:${MYFILE})
```

### 4. Work with Partially Staged Dataset

```bash
DS=mu2e:dig.mu2e.CeEndpointMix1BBTriggered.MDC2020ar_best_v1_3.art

# Start prestaging in background
mdh prestage-files -v $DS &

# While waiting, work with files already on disk
mdh query-dcache -o -v $DS \
  | grep ONLINE | awk '{print $2}' \
  | mdh print-url -s root - > online_files.txt

# Use online_files.txt as input to art job
```

---

## Tips and Best Practices

**File naming:**
- Always follow six-field convention for registered files
- Use meaningful description field
- Sequencer must be unique across all your files

**Storage choices:**
- **Scratch**: Testing, temporary results (auto-purged)
- **Persistent disk**: Important user files (must request quota)
- **Tape-backed**: Large datasets, permanent storage

**Prestaging:**
- Request prestaging early (can take days for large datasets)
- Use `-v` flag to monitor progress
- Consider prestaging overnight for large datasets

**Metadata:**
- `mdh create-metadata` extracts most fields automatically from art files
- Verify JSON before declaring to metacat
- Cannot easily change metadata after declaration

**Cleanup:**
- Delete test files from scratch when done
- Use `mdh delete-files -d -l scratch -c -` to remove both files and records

**Namespaces:**
- Create your namespace once before first upload
- Namespace = your username
- Cannot be deleted once created

---

## Transition from SAM

If you're familiar with SAM, here are key differences:

| SAM | Metacat/mdh | Notes |
|-----|-------------|-------|
| `samweb` | `metacat` or `mdh` | mdh wraps many common operations |
| `samweb list-files` | `metacat query` | MQL is more powerful |
| `samweb get-metadata` | `metacat file show -m` | Similar output |
| `samweb locate-file` | `mdh print-url` | Multiple output formats |
| `samweb prestage-dataset` | `mdh prestage-files` | Similar functionality |
| Dataset dimensions | MQL queries | Different syntax, more flexible |
| `dh.dataset=...` | `files from ...` | Dataset specification |

**SAM files still work** during transition:
- Most production datasets are in both SAM and metacat
- Use whichever tool is most convenient for your workflow
- New files should use metacat/mdh

---

## Documentation and References

**Primary resources:**
- **Data Handling Tutorial**: https://mu2ewiki.fnal.gov/wiki/DataHandlingTutorial
- **Metacat Documentation**: https://fermitools.github.io/metacat
- **Rucio Wiki**: https://mu2ewiki.fnal.gov/wiki/Rucio
- **Dataset Monitor**: https://mu2e.fnal.gov/atwork/computing/ops/datasetMon.html
- **File Naming Convention**: https://mu2ewiki.fnal.gov/wiki/FileNames
- **dCache Information**: https://mu2ewiki.fnal.gov/wiki/Dcache

**Getting help:**
- Slack: `#help-data` channel
- Email: mu2e-data-handling@fnal.gov
- Computing help page: https://mu2ewiki.fnal.gov/wiki/ComputingHelp

---

**Last updated:** February 2026 based on tutorial (January 2026) and tool help
