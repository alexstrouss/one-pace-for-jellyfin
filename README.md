# One Pace for Jellyfin

Automatic NFO metadata setup for watching [One Pace](https://onepace.net) and selected supplemental episodes on [Jellyfin](https://jellyfin.org).

Gives you proper arc names, episode titles, descriptions, and season artwork — no internet connection required during scan, no broken plugins.

![Jellyfin showing One Pace arcs with proper names and posters](docs/preview.png)

---

## Background

The original [Jellyfin One Pace plugin](https://github.com/jwueller/jellyfin-plugin-onepace) relies on a GraphQL API from onepace.net that is currently unavailable (see [issue #92](https://github.com/jwueller/jellyfin-plugin-onepace/issues/92)). This project provides a file-based alternative that works offline and never breaks.

Metadata is sourced from [one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex) and adapted for Jellyfin's NFO format. All 36 One Pace arcs (600+ episodes) are covered. Supplemental support begins with the complete G-8 arc from original anime episodes 196 through 206.

---

## Requirements

- Python 3.7+
- One Pace video files using the **original filenames** from the One Pace project
  e.g. `[One Pace][218-220] Jaya 01 [1080p][2BBCD106].mkv`

Run the script as your normal user, never with `sudo` or as `root`. The script
rejects symlinked metadata destinations and internal media directories, checks
that every write remains inside the resolved One Pace series directory, and
uses atomic file replacement. It never moves, renames, transcodes, deletes, or
modifies video files.

---

## Folder Structure

Your Jellyfin library should point to a folder containing a single **`One Pace`** series folder. Keep One Pace arc folders in their existing format and put supplemental videos directly in `Season 00 - Supplemental Episodes`:

```
/media/Anime/                                        ← Jellyfin library root
└── One Pace/                                        ← series folder
    ├── [One Pace][237-303] Skypiea [1080p]/
    │   ├── [One Pace][237-238] Skypiea 01 [1080p][...].mp4
    │   └── ...
    ├── Season 00 - Supplemental Episodes/
    │   ├── One Piece - 0196.mkv
    │   ├── One Piece - 0197.mkv
    │   ├── ...
    │   └── One Piece - 0206.mkv
    ├── [One Pace][303-321] Long Ring Long Land [1080p]/
    │   └── ...
    └── ...
```

G-8 remains Season 00 so all existing One Pace seasons retain their current
numbers. Its NFOs place it chronologically after Skypiea (Season 16), and the
Season 00 episode numbers remain the original anime numbers: S00E196 through
S00E206.

The aliases `Season 00`, `Season 0`, `Specials`, and `Supplemental Episodes`
are also accepted case-insensitively. Use only one supplemental folder. Nested
arc folders such as `Season 00/G-8/` are not scanned.

---

## Setup

### 1. Clone this repository

```bash
git clone https://github.com/luucaslfs/one-pace-for-jellyfin.git
cd one-pace-for-jellyfin
```

### 2. Run the setup script

Point it at your `One Pace` series folder. Start with a dry run:

```bash
python3 setup.py "/path/to/your/One Pace" --dry-run
```

Review every proposed action, unmatched file, ambiguous match, and unsafe path.
When the plan looks correct, apply it:

```bash
python3 setup.py "/path/to/your/One Pace"
```

The script will:
- Place `tvshow.nfo` at the series root
- Place `season.nfo` and `poster.png` in each arc folder
- Place an episode `.nfo` file alongside every matched video file
- Generate Season 00 and G-8 episode NFOs when a supplemental folder is present
- Leave every video file exactly where it is and never modify its contents

**To overwrite existing metadata:**
```bash
python3 setup.py "/path/to/your/One Pace" --force
```

`--force` replaces ordinary metadata files only. Symlink destinations are
always rejected, including broken symlinks.

### 3. Configure your Jellyfin library

Create or edit a Jellyfin **Shows** library and add the folder that contains the
`One Pace` series folder:

- **Content type:** `Shows`
- Under **Metadata readers**, enable `NFO` and place it first; the local NFO
  files are the metadata source
- Do not enable the NFO metadata saver unless you intentionally want Jellyfin
  to write changes back into these files
- Enable **Dashboard → Library → Display → Display specials within their series they aired in**

Then trigger a library scan: **Dashboard → Libraries → Scan All Libraries**.

G-8 remains visible in Season 00 and also appears chronologically after
Skypiea when the specials display option is enabled. Test this workflow in a
temporary or separate Jellyfin library before refreshing an established
library. If a test library was previously scanned with wrong metadata,
right-click the series and choose **Refresh Metadata → Replace all metadata**.

---

## What gets placed

| File | Location | Purpose |
|------|----------|---------|
| `tvshow.nfo` | Series root | Series title, description |
| `season.nfo` | Each arc folder | Arc name, description |
| `poster.png` | Each arc folder | Arc artwork |
| `[video name].nfo` | Alongside each video | Episode title, description, air date |
| `season.nfo` | Season 00 folder | Supplemental-season title and description |
| `One Piece - 0196.nfo` … `0206.nfo` | Season 00 folder | G-8 metadata and chronological placement |

---

## Re-running after new downloads

Just run the script again — it skips files that already have NFOs:

```bash
python3 setup.py "/path/to/your/One Pace" --dry-run
python3 setup.py "/path/to/your/One Pace"
```

Use `--force` only if you want to refresh existing ordinary metadata files.

## Supplemental filename matching

The deterministic G-8 naming format is:

```text
One Piece - 0196.mkv
One Piece - 0197.mkv
...
One Piece - 0206.mkv
```

Common forms such as `One Piece Episode 196.mkv`, `One Piece Ep 196.mkv`, and
`[196] A State of Emergency.mkv` are also recognized. Matching is conservative:
resolution, bit-depth, year, and CRC-like values are not treated as episode
numbers. An unmatched file is left untouched and reported with the recommended
format. If a filename contains more than one valid catalog episode number, it
is reported as ambiguous and receives no NFO.

To add another supplemental arc, edit `metadata/supplements.json`. The manifest
holds folder aliases, placement, episode mappings, optional exact filename
aliases or regular expressions, and optional title, plot, aired, and premiered
metadata. The entire manifest is validated before any files are written, so a
duplicate number, invalid date, invalid expression, or conflicting placement
stops the run safely.

---

## Troubleshooting

**Arc not matched:**
The script couldn't find the arc name in the folder name. Make sure the folder name contains the arc name (e.g. "Jaya", "Skypiea"). Open an issue if the arc name format is unexpected.

**Episode not matched:**
The video filename doesn't follow the standard One Pace naming format. The script expects filenames like `[One Pace][chapters] Arc Name EE [res][CRC].ext`.

**Supplemental episode not matched or ambiguous:**
Rename it to the deterministic form `One Piece - 0196.ext` using the correct
original anime episode number, then run `--dry-run` again. The script never
renames the file for you.

**Unsafe path rejected:**
Remove the symlink from the internal arc/supplemental directory or metadata
destination. A One Pace root path may itself be a symlink, but directories and
metadata files inside the resolved root must be ordinary filesystem entries.

**Jellyfin still shows wrong metadata:**
Right-click the series → **Refresh Metadata** → check **Replace all metadata** and **Replace all images**.

---

## Coverage

All 36 released One Pace arcs retain their existing numbering:

| # | Arc | # | Arc |
|---|-----|---|-----|
| 1 | Romance Dawn | 19 | Enies Lobby |
| 2 | Orange Town | 20 | Post-Enies Lobby |
| 3 | Syrup Village | 21 | Thriller Bark |
| 4 | Gaimon | 22 | Sabaody Archipelago |
| 5 | Baratie | 23 | Amazon Lily |
| 6 | Arlong Park | 24 | Impel Down |
| 7 | The Adventures of Buggy's Crew | 25 | The Adventures of the Straw Hats |
| 8 | Loguetown | 26 | Marineford |
| 9 | Reverse Mountain | 27 | Post-War |
| 10 | Whisky Peak | 28 | Return to Sabaody |
| 11 | The Trials of Koby-Meppo | 29 | Fishman Island |
| 12 | Little Garden | 30 | Punk Hazard |
| 13 | Drum Island | 31 | Dressrosa |
| 14 | Alabasta | 32 | Zou |
| 15 | Jaya | 33 | Whole Cake Island |
| 16 | Skypiea | 34 | Reverie |
| 17 | Long Ring Long Land | 35 | Wano |
| 18 | Water Seven | 36 | Egghead |

Supplemental Season 00 currently includes G-8 (original anime episodes
196–206), positioned after Season 16 without renumbering Long Ring Long Land or
later One Pace arcs.

---

## Credits

- **[One Pace](https://onepace.net)** — the fan project this is all about
- **[one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex)** by [@SpykerNZ](https://github.com/SpykerNZ) — NFO files and season artwork sourced from this project
- **[jellyfin-plugin-onepace](https://github.com/jwueller/jellyfin-plugin-onepace)** by [@jwueller](https://github.com/jwueller) — the original Jellyfin plugin (currently unavailable due to API changes)

---

## Contributing

New arc NFOs will be added as One Pace releases them. Pull requests welcome — especially for missing episodes, supplemental catalog entries, metadata corrections, or improved arc matching logic.
