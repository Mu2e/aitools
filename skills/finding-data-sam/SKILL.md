---
name: finding-data-sam
description: Find and access Mu2e data using SAM tools. Use with samweb commands, dataset queries, file locations, prestaging from tape, or SAM dimensions. Currently the primary tool (~90% usage) during transition period.
compatibility: Requires mu2einit, muse setup ops, samweb, dhtools
metadata:
  version: "1.0.0"
  last-updated: "2026-02-13"
---

# SAM Data Handling - Reference

SAM (Sequential Access to Metadata) is the primary file catalog and location service for approximately 90% of Mu2e data workflows. It manages:
- File metadata (names, properties, creation time, etc.)
- File locations (in dCache and other storage systems)
- Dataset definitions and snapshots
- File queries and selections

This guide covers the **samweb** command-line tool, which is the user interface to SAM.

**For architectural context**, see [datahandling-overview.md](datahandling-overview.md)
**For next-generation tools**, see datahandling-metacat-rucio-mdh.md
**Complete references**: https://mu2ewiki.fnal.gov/wiki/SAM

## Setup and Authentication

Before using SAM tools, set up your environment:

```bash
mu2einit                  # Sets SAM_EXPERIMENT=mu2e
muse setup ops            # Brings in samweb and dhtools
```

For interactive work with write access (declaring files):

```bash
mu2einit
kinit                     # Get Kerberos ticket
getToken                  # Get SAM authentication token
```

Optional convenience tools:

```bash
setup dhtools             # Adds mu2e-specific SAM utility scripts
```

## File Naming and dCache Locations

All registered files follow a strict six-field naming convention:

```
data_tier.owner.description.configuration.sequencer.file_format
```

Example:
```
sim.mu2e.beam_g4s1_dsregion.0429a.123456_12345678.art
```

Files are physically located in dCache at paths constructed from these fields. SAM tracks the exact location.

For more details, see [datahandling-overview.md](datahandling-overview.md#file-naming-and-datasets)

## Discovering Datasets

Before querying SAM, it's helpful to browse available datasets. The Mu2e **Dataset Monitor** provides a curated list of all active, viable datasets with high-level summaries.

### Dataset Monitor

**Quick way to explore all datasets:**
- https://mu2e-exp.fnal.gov/computing/ops/samMon.html

This page shows:
- All datasets with their status
- Approximate size and file counts
- Storage locations (tape, disk, etc.)
- Created and updated dates
- Links to detailed metadata

**Why use this instead of raw SAM queries?**
- The list is **curated** — only viable datasets included
- Avoids obsolete or discarded datasets
- Quick overview without running queries
- Good for discovering what data exists

**After finding a candidate dataset**, use samweb commands (below) for detailed queries and file operations.

## Selecting and Examining Files

### Basic File Queries

**Count files matching criteria:**
```bash
samweb count-files "dh.dataset=sim.mu2e.example-beam-g4s1.1812a.art"
```

**List files matching criteria:**
```bash
samweb list-files "dh.dataset=sim.mu2e.example-beam-g4s1.1812a.art"
```

**List with summary info (file count, total size):**
```bash
samweb list-files --summary "dh.dataset=sim.mu2e.example-beam-g4s1.1812a.art"
```

**List with file details (size, event count):**
```bash
samweb list-files --fileinfo "dh.dataset=sim.mu2e.example-beam-g4s1.1812a.art"
```

### Selection Criteria Syntax

**By dataset (most common):**
```bash
"dh.dataset=sim.mu2e.example-beam-g4s1.1812a.art"
```

**By exact filename:**
```bash
"file_name=sim.mu2e.example-beam-g4s1.1812a.123456_000016.art"
```

**By filename pattern (use % for any string, ? for single char):**
```bash
"file_name like ???.mu2e.example-beam-g4s1.1812a.%"
```

**Multiple files by name:**
```bash
"file_name in (sim.mu2e.example-beam-g4s1.1812a.123456_000016.art,
                sim.mu2e.example-beam-g4s1.1812a.123456_000014.art)"
```

**By metadata field values:**
```bash
"data_tier=sim and mc.generator_type=beam"
```

**By creation date:**
```bash
"dh.dataset=dig.mu2e.ensembleMDS1eOnSpillTriggerable.MDC2020aq_best_v1_3.art 
 and create_date > '2025-02-17T08:00:00-05:00'"
```

**Warning**: Wildcard queries can cause poor performance on large datasets. Avoid unless necessary.

## File Metadata

### Viewing File Metadata

**Complete metadata for a file:**
```bash
export SAM_FILE=sim.mu2e.example-beam-g4s1.1812a.123456_000016.art
samweb get-metadata $SAM_FILE
```

**Available metadata fields:**
```bash
samweb list-parameters          # All available fields
samweb list-parameters <filter> # Filtered fields
samweb list-values --help-categories  # Meta-field categories
samweb list-values <category>   # Allowed values for a category
```

### Understanding File Status and Location

**Locate a file (find where it is stored):**
```bash
samweb locate-file $SAM_FILE
```

Output example:
```
enstore:/pnfs/mu2e/usr-sim/sim/mu2e/example-beam-g4s1/1812a/000/000(30@vpe007)
```

**Location format:**
- Protocol: `enstore:` (tape-backed) or `dcache:` (disk-only)
- Path: `/pnfs/mu2e/...` — Directory in dCache
- Tape reference: `(30@vpe007)` — File 30 on tape cartridge vpe007
- If no tape information, file is disk-resident only

**Interpreting location strings for filesystem access:**

The location string shows the directory path; append the filename to get the full path:

```bash
# Example: Get location for one file
FILE="dig.mu2e.CeEndpointMix1BBTriggered.MDC2020ar_best_v1_3.001210_00000684.art"
samweb locate-file $FILE
# Output: enstore:/pnfs/mu2e/tape/phy-sim/dig/mu2e/CeEndpointMix1BBTriggered/MDC2020ar_best_v1_3/art/fd/d4(21197@fb6195l9)

# Extract directory by removing protocol and tape reference
LOCATION=$(samweb locate-file $FILE)
PNFS_DIR=$(echo $LOCATION | sed 's/enstore://' | sed 's/(.*)//')
FULL_PATH="${PNFS_DIR}/${FILE}"

# Now can use standard filesystem commands
ls -l $FULL_PATH
# Output: -rw-r--r-- 1 mu2epro mu2e 2540259628 Mar 14  2025 /pnfs/mu2e/tape/phy-sim/dig/...
```

**Note:** Files on tape may need prestaging (see workflows below) before they can be read efficiently.

**Find location details for all files in a dataset:**
```bash
samweb list-file-locations --dimensions='dh.dataset=sim.mu2e.example-beam-g4s1.1812a.art'
```

Output columns:
- Column 1: dCache path
- Column 2: File name
- Column 3: File size (bytes)

### Files Without Recorded Locations

Some files (e.g., FCL configuration files) may exist in dCache but not have a location recorded in SAM. Include them in queries:

```bash
samweb count-files "dh.dataset=cnf.mu2e.cd3-beam-g4s4-flate.v0.fcl and availability:anylocation"
```

## Dataset Concepts

### Datasets vs. Filenames

A **dataset** is the logical grouping of files. It's formed by removing the sequencer (run/subrun) from the filename.

**File name:**
```
sim.mu2e.example-beam-g4s1.1812a.123456_000016.art
```

**Dataset:**
```
sim.mu2e.example-beam-g4s1.1812a.art
```

All files with the same tier, owner, description, configuration, and format are in the same dataset.

### Automatic Dataset Definitions

For every unique dataset, SAM automatically creates a dataset definition:

```bash
samweb describe-definition sim.mu2e.example-beam-g4s1.1812a.art
```

This means you almost always use the dataset name directly as your selection criteria:

```bash
samweb list-files "dh.dataset=sim.mu2e.example-beam-g4s1.1812a.art"
```

### Custom Dataset Definitions

You can create custom dataset definitions for complex selections:

```bash
# Choose a unique name (include your username)
export SAM_DD=${USER}_test_selection_0

# Create the definition with selection criteria
samweb create-definition $SAM_DD "data_tier=sim and mc.generator_type=beam"

# List files matching the definition
samweb list-definition-files $SAM_DD

# Examine the definition
samweb describe-definition $SAM_DD

# Remove the definition when done
samweb delete-definition $SAM_DD
```

**Naming rule**: Always start dataset definition names with your username to avoid conflicts.

## Snapshots (Fixed File Lists)

A **snapshot** locks in the exact list of files matching a definition at a point in time. Useful when you need a fixed, unchanging file list.

### Creating and Using Snapshots

**Take a snapshot:**
```bash
export SAM_SNAP_ID=$(samweb take-snapshot $SAM_DD)
echo $SAM_SNAP_ID
```

**List files in snapshot:**
```bash
samweb list-files "snapshot_id=$SAM_SNAP_ID"
```

**Create a definition from snapshot (needed for job submission):**
```bash
export SAM_DD_SNAP=${USER}_test_snap_0
samweb create-definition $SAM_DD_SNAP "snapshot_id=$SAM_SNAP_ID"
```

**Use part of a snapshot:**
```bash
samweb create-definition $SAM_DD_SNAP "snapshot_id=$SAM_SNAP_ID and snapshot_file_number<=100"
```

## Prestaging Files from Tape

Files on tape-backed dCache must be prestaged (copied from tape to disk cache) before they can be efficiently read.

### Check if files are on disk or tape

**Key utility:**
```bash
setup dhtools
samOnDisk sim.mu2e.example-beam-g4s1.1812a.art
```

This picks a few random files from the dataset and checks if they're currently on disk.

### Request prestaging

**Prestage entire dataset:**
```bash
samweb prestage-dataset "dh.dataset=sim.mu2e.example-beam-g4s1.1812a.art"
```

**Prestage a dataset definition:**
```bash
samweb prestage-dataset-definition $SAM_DD
```

**Monitor prestaging progress:**
Check the Mu2e data handling status page for prestaging job status.

## Running Grid Jobs with SAM

When submitting grid jobs using **jobsub**, SAM provides file names and locations:

```bash
export SAM_DD=${USER}_myanalysis
samweb create-definition $SAM_DD "dh.dataset=mcs.mu2e.dtrkclust.MDC2020w.art"

jobsub_submit -G mu2e \
  --role=Analysis \
  --dataset=$SAM_DD \
  file:///path/to/my/job.sh
```

Inside the job, art's FileListProducer uses SAM to retrieve file URLs and locations.

## Mu2e SAM Utility Scripts

The **dhtools** product provides convenience scripts for common operations.

```bash
setup dhtools
```

**Available scripts:**

| Command | Purpose |
|---------|---------|
| `samDatasets` | List all mu2e datasets in SAM |
| `samGet` | Find and copy specific files locally (for testing) |
| `samOnDisk` | Check if random files from dataset are on disk |
| `samOnTape` | Count/summarize files on tape |
| `samToPnfs` | List dCache paths for all files in dataset |
| `samSplit` | Split large dataset into smaller subsets |

All have `-h` help option.

**samGet example:**
```bash
samGet -d sim.mu2e.example-beam-g4s1.1812a.art -n 2
# Copies 2 files locally for testing
```

**samToPnfs example:**
```bash
samToPnfs sim.mu2e.example-beam-g4s1.1812a.art
# Prints full /pnfs paths for all files in dataset
```

## File Ownership and Permissions

**File ownership:**
- When you declare a file to SAM, you own the record
- Only you (and admins) can modify it
- Other users can read it but not edit

**Cannot transfer ownership** once established

**Admin permissions:**
- Admins can modify any user's records
- View current admins: https://sammu2e.fnal.gov:8483/sam/mu2e/admin/users

## Common Workflows

### 1. Find and List a Dataset

```bash
# Find existing dataset
samweb list-files "dh.dataset=mcs.mu2e.dtrkclust.MDC2020w.art" | head -20

# Count total files
samweb count-files "dh.dataset=mcs.mu2e.dtrkclust.MDC2020w.art"

# Check if on disk or tape
samOnDisk mcs.mu2e.dtrkclust.MDC2020w.art
```

### 2. Prestage and Retrieve File Paths

```bash
export DATASET="mcs.mu2e.dtrkclust.MDC2020w.art"

# Prestage from tape if needed
samweb prestage-dataset "dh.dataset=$DATASET"

# Get dCache paths for all files
samToPnfs $DATASET > file_list.txt
```

### 3. Create a Custom Selection and Snapshot

```bash
export SAM_DD=${USER}_selected_files
samweb create-definition $SAM_DD \
  "dh.dataset=mcs.mu2e.dtrkclust.MDC2020w.art and run_number>=1225"

# Lock in the files
export SAM_SNAP_ID=$(samweb take-snapshot $SAM_DD)

# Create definition from snapshot for job submission
export SAM_DD_SNAP=${USER}_snapshot_0
samweb create-definition $SAM_DD_SNAP "snapshot_id=$SAM_SNAP_ID"
```

### 4. Copy a Few Files Locally for Testing

```bash
samGet -d sim.mu2e.example-beam-g4s1.1812a.art -n 3

# Files are copied to current directory
ls -lh sim.mu2e.*.art
```

## Troubleshooting

**"Not registered in SAM database" error**
- First use? Contact mu2e computing support via servicedesk ticket
- Should be automatic when account is created

**Slow wildcard queries**
- Avoid `%` wildcards on large datasets
- Use specific criteria instead (e.g., by dataset or run number)

**File found in SAM but can't read it**
- File may be on tape only (check with `samOnDisk`)
- Must prestage before reading: `samweb prestage-dataset`

**Location shows `NEARLINE` (tape only)**
- File has been migrated to tape and purged from disk
- Prestaging can take minutes to days depending on tape load
- Use `samOnDisk` to monitor progress

## Related Topics

- [File naming conventions](https://mu2ewiki.fnal.gov/wiki/FileNames)
- [Uploading files to SAM](https://mu2ewiki.fnal.gov/wiki/Upload)
- [SAM metadata fields](https://mu2ewiki.fnal.gov/wiki/SamMetadata)
- [dCache storage details](https://mu2ewiki.fnal.gov/wiki/Dcache)
- [Prestaging procedures](https://mu2ewiki.fnal.gov/wiki/Prestage)

## References

- **SAM main documentation**: https://mu2ewiki.fnal.gov/wiki/SAM
- **samweb command reference**: https://cdcvs.fnal.gov/redmine/projects/sam-main/wiki/Sam_web_client_Command_Reference
- **samweb user guide**: https://cdcvs.fnal.gov/redmine/projects/sam/wiki/User_Guide_for_SAM
- **Metadata format**: https://cdcvs.fnal.gov/redmine/projects/sam-web/wiki/Metadata_format
- **Query syntax**: https://cdcvs.fnal.gov/redmine/projects/sam-web/wiki/Dimension_Syntax
- **Mu2e dataset monitor**: http://mu2e.fnal.gov/atwork/computing/ops/samMon.html

---

**Last updated**: 2026-02-12  
**Status**: SAM is the current standard; for transition to metacat/Rucio, see datahandling-metacat-rucio-mdh.md

