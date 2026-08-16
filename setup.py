#!/usr/bin/env python3
"""Place One Pace and supplemental Jellyfin NFO metadata safely."""

import argparse
import datetime
import json
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections import namedtuple
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
METADATA_DIR = SCRIPT_DIR / "metadata"
SUPPLEMENTS_PATH = METADATA_DIR / "supplements.json"

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v"}

# Matches standard One Pace filenames:
#   [One Pace][218-220] Jaya 01 [1080p][2BBCD106].mkv
#   [One Pace][302-303] Skypiea 25 Alternate (G-8) [1080p][90C45C25].mkv
#   [One Pace][303] Long Ring Long Land 00 [1080p][En Sub][7582DAC2].mp4
FILENAME_PATTERN = re.compile(
    r"^\[One Pace\]\[[^\]]+\]\s+(.+?)\s+(\d+)"
    r"(\s+Alternate[^\[]*?)?"
    r"\s+\[",
    re.IGNORECASE,
)

SUPPLEMENTAL_NUMBER_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9])Episode\s*0*(\d{1,4})(?!\d)"),
    re.compile(r"(?i)(?<![A-Za-z0-9])Ep\s*0*(\d{1,4})(?!\d)"),
    re.compile(r"(?i)(?<![A-Za-z0-9])E\s*0*(\d{1,4})(?!\d)"),
    re.compile(r"(?i)\bOne[ ._-]*Piece\s*-\s*0*(\d{1,4})(?![A-Za-z0-9])"),
    re.compile(r"^\s*\[\s*0*(\d{1,4})\s*\]"),
    re.compile(r"^\s*0*(\d{1,4})(?=\s*(?:-|–|—|\[|$))"),
)

CopyAction = namedtuple("CopyAction", "source destination description category")
WriteTextAction = namedtuple(
    "WriteTextAction", "content destination description category"
)


class FatalError(RuntimeError):
    """A validation or path error that prevents all planned writes."""

    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


class RunReport:
    def __init__(self):
        self.actions = []
        self.skipped = []
        self.unmatched = []
        self.ambiguous = []
        self.unsafe = []


NUMBER_WORDS = {
    "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def is_positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_within(path, root):
    """Return whether a resolved absolute path is within a resolved root."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def normalize(name):
    """Lowercase and replace digit/word numbers for fuzzy arc matching."""
    normalized = name.lower().strip()
    for digit, word in NUMBER_WORDS.items():
        normalized = re.sub(r"\b{}\b".format(digit), word, normalized)
    return normalized


def normalize_folder_alias(name):
    return " ".join(name.casefold().split())


def load_seasons():
    with open(str(METADATA_DIR / "seasons.json"), encoding="utf-8") as handle:
        return json.load(handle)


def find_season_number(arc_name, seasons):
    """Match an arc name to its unchanged One Pace season number."""
    if arc_name in seasons:
        return seasons[arc_name]
    arc_lower = arc_name.lower()
    for name, season_number in seasons.items():
        if name.lower() == arc_lower:
            return season_number
    arc_normalized = normalize(arc_name)
    for name, season_number in seasons.items():
        if normalize(name) == arc_normalized:
            return season_number
    for name, season_number in seasons.items():
        if arc_lower in name.lower() or name.lower() in arc_lower:
            return season_number
    return None


def one_pace_nfo_name(filename, season_number):
    """Return the metadata filename selected by a One Pace video filename."""
    match = FILENAME_PATTERN.match(filename)
    if not match:
        return None
    episode_number = int(match.group(2))
    if match.group(3):
        return "S{:02d}E{:02d}_alternate.nfo".format(
            season_number, episode_number
        )
    return "S{:02d}E{:02d}.nfo".format(season_number, episode_number)


def validate_iso_date(value, field_name):
    if value is None:
        return
    if not isinstance(value, str):
        raise FatalError("{} must be an ISO date string".format(field_name))
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise FatalError("Invalid ISO date for {}: {}".format(field_name, value)) from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise FatalError("Invalid ISO date for {}: {}".format(field_name, value))


def validate_placement(placement, arc_id):
    if not isinstance(placement, dict) or not placement:
        raise FatalError("Arc '{}' is missing placement".format(arc_id))
    allowed = {"after_season", "before_season", "before_episode"}
    unknown = set(placement) - allowed
    if unknown:
        raise FatalError(
            "Arc '{}' has unsupported placement fields: {}".format(
                arc_id, ", ".join(sorted(unknown))
            )
        )
    has_after = "after_season" in placement
    has_before = "before_season" in placement
    if has_after == has_before:
        raise FatalError("Arc '{}' must use exactly one placement strategy".format(arc_id))
    if "before_episode" in placement and not has_before:
        raise FatalError(
            "Arc '{}' cannot use before_episode without before_season".format(arc_id)
        )
    for field_name, value in placement.items():
        if not is_positive_integer(value):
            raise FatalError(
                "Arc '{}' has invalid {} value".format(arc_id, field_name)
            )


def load_supplements(path=SUPPLEMENTS_PATH):
    """Load and completely validate the supplemental metadata manifest."""
    try:
        with open(str(path), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise FatalError("Could not load supplemental manifest: {}".format(exc)) from exc

    if data.get("schema_version") != 1:
        raise FatalError("Missing or unsupported supplemental schema_version")

    specials = data.get("specials_season")
    if not isinstance(specials, dict):
        raise FatalError("Missing specials_season configuration")
    if specials.get("number") != 0:
        raise FatalError("specials_season.number must be 0")
    for field_name in ("title", "plot"):
        if not isinstance(specials.get(field_name), str) or not specials[field_name].strip():
            raise FatalError("specials_season.{} is required".format(field_name))
    folder_aliases = specials.get("folder_aliases")
    if not isinstance(folder_aliases, list) or not folder_aliases:
        raise FatalError("specials_season.folder_aliases must not be empty")
    if any(not isinstance(alias, str) or not alias.strip() for alias in folder_aliases):
        raise FatalError("Every supplemental folder alias must be a non-empty string")

    arcs = data.get("arcs")
    if not isinstance(arcs, list):
        raise FatalError("Supplemental arcs must be a list")

    arc_ids = set()
    source_episodes = set()
    special_episodes = set()
    catalog = {}
    exact_aliases = {}
    regex_entries = []

    for arc in arcs:
        if not isinstance(arc, dict):
            raise FatalError("Every supplemental arc must be an object")
        arc_id = arc.get("id")
        if not isinstance(arc_id, str) or not arc_id.strip():
            raise FatalError("Every supplemental arc requires an ID")
        if arc_id in arc_ids:
            raise FatalError("Duplicate supplemental arc ID: {}".format(arc_id))
        arc_ids.add(arc_id)
        title = arc.get("title")
        if not isinstance(title, str) or not title.strip():
            raise FatalError("Arc '{}' is missing a title".format(arc_id))
        placement = arc.get("placement")
        validate_placement(placement, arc_id)
        episodes = arc.get("episodes")
        if not isinstance(episodes, list):
            raise FatalError("Arc '{}' episodes must be a list".format(arc_id))

        arc_episode_numbers = set()
        for raw_episode in episodes:
            if not isinstance(raw_episode, dict):
                raise FatalError("Arc '{}' contains an invalid episode".format(arc_id))
            source_episode = raw_episode.get("source_episode")
            arc_episode = raw_episode.get("arc_episode")
            special_episode = raw_episode.get("special_episode", source_episode)
            for field_name, value in (
                ("source_episode", source_episode),
                ("arc_episode", arc_episode),
                ("special_episode", special_episode),
            ):
                if not is_positive_integer(value):
                    raise FatalError(
                        "Arc '{}' has invalid {}".format(arc_id, field_name)
                    )
            if source_episode in source_episodes:
                raise FatalError(
                    "Duplicate supplemental source_episode: {}".format(source_episode)
                )
            if special_episode in special_episodes:
                raise FatalError(
                    "Duplicate supplemental special_episode: {}".format(special_episode)
                )
            if arc_episode in arc_episode_numbers:
                raise FatalError(
                    "Duplicate arc_episode {} in arc '{}'".format(arc_episode, arc_id)
                )
            source_episodes.add(source_episode)
            special_episodes.add(special_episode)
            arc_episode_numbers.add(arc_episode)

            validate_iso_date(raw_episode.get("aired"), "aired")
            validate_iso_date(raw_episode.get("premiered"), "premiered")

            filename_aliases = raw_episode.get("filename_aliases", [])
            filename_regexes = raw_episode.get("filename_regexes", [])
            if not isinstance(filename_aliases, list) or any(
                not isinstance(alias, str) or not alias for alias in filename_aliases
            ):
                raise FatalError("filename_aliases must be a list of strings")
            if not isinstance(filename_regexes, list) or any(
                not isinstance(pattern, str) or not pattern for pattern in filename_regexes
            ):
                raise FatalError("filename_regexes must be a list of strings")

            compiled_regexes = []
            for pattern in filename_regexes:
                try:
                    compiled_regexes.append(re.compile(pattern, re.IGNORECASE))
                except re.error as exc:
                    raise FatalError(
                        "Invalid filename regex for source episode {}: {}".format(
                            source_episode, exc
                        )
                    ) from exc

            episode = dict(raw_episode)
            episode["source_episode"] = source_episode
            episode["special_episode"] = special_episode
            episode["arc_episode"] = arc_episode
            episode["arc"] = arc
            episode["compiled_filename_regexes"] = compiled_regexes
            catalog[source_episode] = episode
            for alias in filename_aliases:
                exact_aliases.setdefault(alias.casefold(), set()).add(source_episode)
            for compiled in compiled_regexes:
                regex_entries.append((compiled, source_episode))

    data["catalog"] = catalog
    data["exact_aliases"] = exact_aliases
    data["regex_entries"] = regex_entries
    data["normalized_folder_aliases"] = {
        normalize_folder_alias(alias) for alias in folder_aliases
    }
    return data


def match_supplemental_filename(filename, manifest):
    """Match a supplemental video conservatively, returning status and candidates."""
    stem = Path(filename).stem
    exact_candidates = manifest["exact_aliases"].get(stem.casefold(), set())
    if exact_candidates:
        candidates = sorted(exact_candidates)
        status = "matched" if len(candidates) == 1 else "ambiguous"
        return status, candidates

    regex_candidates = {
        source_episode
        for pattern, source_episode in manifest["regex_entries"]
        if pattern.search(stem)
    }
    if regex_candidates:
        candidates = sorted(regex_candidates)
        status = "matched" if len(candidates) == 1 else "ambiguous"
        return status, candidates

    extracted = set()
    for pattern in SUPPLEMENTAL_NUMBER_PATTERNS:
        for match in pattern.finditer(stem):
            candidate = int(match.group(1))
            if candidate in manifest["catalog"]:
                extracted.add(candidate)
    candidates = sorted(extracted)
    if len(candidates) == 1:
        return "matched", candidates
    if len(candidates) > 1:
        return "ambiguous", candidates
    return "unmatched", []


def indent_xml(element, level=0):
    """Indent XML without ElementTree.indent(), which requires Python 3.9."""
    whitespace = "\n" + level * "  "
    child_whitespace = "\n" + (level + 1) * "  "
    children = list(element)
    if children:
        if not element.text or not element.text.strip():
            element.text = child_whitespace
        for index, child in enumerate(children):
            indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = child_whitespace if index < len(children) - 1 else whitespace


def xml_text(root):
    indent_xml(root)
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return "<?xml version='1.0' encoding='UTF-8'?>\n{}\n".format(body)


def supplemental_nfo_text(episode):
    arc = episode["arc"]
    source_episode = episode["source_episode"]
    arc_episode = episode["arc_episode"]
    title = episode.get("title") or "{} {:02d} (Original Episode {})".format(
        arc["title"], arc_episode, source_episode
    )
    plot = episode.get("plot") or (
        "Anime-original {} episode. Original One Piece episode {}.".format(
            arc["title"], source_episode
        )
    )

    root = ET.Element("episodedetails")
    ET.SubElement(root, "title").text = title
    ET.SubElement(root, "showtitle").text = "One Pace"
    ET.SubElement(root, "season").text = "0"
    ET.SubElement(root, "episode").text = str(episode["special_episode"])
    ET.SubElement(root, "plot").text = plot
    if episode.get("aired"):
        ET.SubElement(root, "aired").text = episode["aired"]
    if episode.get("premiered"):
        ET.SubElement(root, "premiered").text = episode["premiered"]

    placement = arc["placement"]
    if "after_season" in placement:
        ET.SubElement(root, "airsafter_season").text = str(placement["after_season"])
    else:
        ET.SubElement(root, "airsbefore_season").text = str(placement["before_season"])
        if "before_episode" in placement:
            ET.SubElement(root, "airsbefore_episode").text = str(
                placement["before_episode"]
            )

    ET.SubElement(root, "tag").text = "Supplemental"
    ET.SubElement(root, "tag").text = arc["title"]
    source_tag = {
        "original_anime": "Anime Original",
    }.get(
        arc.get("source_type"),
        str(arc.get("source_type", "Supplemental")).replace("_", " ").title(),
    )
    ET.SubElement(root, "tag").text = source_tag
    return xml_text(root)


def supplemental_season_nfo_text(manifest):
    specials = manifest["specials_season"]
    root = ET.Element("season")
    ET.SubElement(root, "title").text = specials["title"]
    ET.SubElement(root, "seasonnumber").text = str(specials["number"])
    ET.SubElement(root, "plot").text = specials["plot"]
    return xml_text(root)


def validate_metadata_source(source):
    metadata_root = METADATA_DIR.resolve()
    source = Path(source)
    try:
        resolved = source.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FatalError("Metadata source is unavailable: '{}'".format(source)) from exc
    if not is_within(resolved, metadata_root) or not resolved.is_file():
        raise FatalError("Refusing metadata source outside repository: '{}'".format(source))
    return resolved


def destination_error(series_root, destination):
    """Return an unsafe-path explanation, or None for a safe file destination."""
    series_root = Path(series_root).resolve()
    destination = Path(destination).absolute()
    if os.path.lexists(str(destination)) and os.path.islink(str(destination)):
        return "Refusing symlink destination: '{}'".format(destination)

    try:
        relative_destination = destination.relative_to(series_root)
    except ValueError:
        return "Refusing destination outside One Pace directory: '{}'".format(destination)

    current = series_root
    for part in relative_destination.parts[:-1]:
        current = current / part
        if os.path.lexists(str(current)) and os.path.islink(str(current)):
            return "Refusing symlinked directory: '{}'".format(current)

    try:
        resolved_parent = destination.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        return "Destination parent is unavailable: '{}'".format(destination.parent)
    if not is_within(resolved_parent, series_root):
        return "Refusing destination outside One Pace directory: '{}'".format(destination)
    if destination.exists() and not destination.is_file():
        return "Destination is not an ordinary file: '{}'".format(destination)
    return None


class Planner:
    def __init__(self, series_root, force, report):
        self.series_root = Path(series_root).resolve()
        self.force = force
        self.report = report
        self.destinations = set()

    def _prepare_destination(self, destination, description):
        destination = Path(destination).absolute()
        error = destination_error(self.series_root, destination)
        if error:
            self.report.unsafe.append(error)
            return None
        resolved_key = str(destination.resolve())
        if resolved_key in self.destinations:
            raise FatalError(
                "More than one action targets '{}'".format(destination), self.report
            )
        if destination.exists() and not self.force:
            self.report.skipped.append("{} (already exists)".format(description))
            return None
        self.destinations.add(resolved_key)
        return destination

    def add_copy(self, source, destination, description, category):
        source = validate_metadata_source(source)
        destination = self._prepare_destination(destination, description)
        if destination is not None:
            self.report.actions.append(
                CopyAction(source, destination, description, category)
            )

    def add_text(self, content, destination, description, category):
        destination = self._prepare_destination(destination, description)
        if destination is not None:
            self.report.actions.append(
                WriteTextAction(content, destination, description, category)
            )


def atomic_copy(action, series_root):
    source = validate_metadata_source(action.source)
    error = destination_error(series_root, action.destination)
    if error:
        raise FatalError(error)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".one-pace-", suffix=".tmp", dir=str(action.destination.parent)
        )
        with os.fdopen(descriptor, "wb") as target, open(str(source), "rb") as source_file:
            shutil.copyfileobj(source_file, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, str(action.destination))
        temporary = None
        shutil.copystat(str(source), str(action.destination), follow_symlinks=False)
    finally:
        if temporary and os.path.lexists(temporary):
            os.unlink(temporary)


def atomic_write_text(action, series_root):
    error = destination_error(series_root, action.destination)
    if error:
        raise FatalError(error)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".one-pace-", suffix=".tmp", dir=str(action.destination.parent)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(action.content)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, str(action.destination))
        temporary = None
    finally:
        if temporary and os.path.lexists(temporary):
            os.unlink(temporary)


def apply_actions(actions, series_root):
    for action in actions:
        if isinstance(action, CopyAction):
            atomic_copy(action, series_root)
        else:
            atomic_write_text(action, series_root)


def find_supplemental_folder(series_root, manifest, report):
    matches = []
    aliases = manifest["normalized_folder_aliases"]
    for entry in sorted(series_root.iterdir()):
        if normalize_folder_alias(entry.name) in aliases:
            matches.append(entry)
    if len(matches) > 1:
        raise FatalError(
            "More than one supplemental folder exists: {}".format(
                ", ".join(str(path) for path in matches)
            ),
            report,
        )
    if not matches:
        return None
    folder = matches[0]
    if folder.is_symlink():
        report.unsafe.append("Refusing symlinked supplemental folder: '{}'".format(folder))
        return None
    if not folder.is_dir():
        report.unsafe.append("Supplemental path is not a directory: '{}'".format(folder))
        return None
    return folder


def add_one_pace_actions(series_root, supplemental_folder, planner, report):
    seasons = load_seasons()
    planner.add_copy(
        METADATA_DIR / "tvshow.nfo",
        series_root / "tvshow.nfo",
        "tvshow.nfo at series root",
        "metadata",
    )

    for arc_dir in sorted(series_root.iterdir()):
        if supplemental_folder is not None and arc_dir == supplemental_folder:
            continue
        if arc_dir.is_symlink():
            report.unsafe.append("Refusing symlinked internal path: '{}'".format(arc_dir))
            continue
        if not arc_dir.is_dir():
            continue

        arc_match = re.search(r"\]\s+(.+?)(?:\s+\[|$)", arc_dir.name)
        if not arc_match:
            arc_match = re.search(r"[-–]\s*(.+)$", arc_dir.name)
        if not arc_match:
            continue
        arc_name = arc_match.group(1).strip()
        season_number = find_season_number(arc_name, seasons)
        if season_number is None:
            continue
        season_metadata = METADATA_DIR / "seasons" / str(season_number)
        if not season_metadata.is_dir():
            continue

        planner.add_copy(
            season_metadata / "season.nfo",
            arc_dir / "season.nfo",
            "season.nfo for Season {} ({})".format(season_number, arc_name),
            "metadata",
        )
        poster = season_metadata / "poster.png"
        if poster.is_file():
            planner.add_copy(
                poster,
                arc_dir / "poster.png",
                "poster.png for Season {} ({})".format(season_number, arc_name),
                "metadata",
            )

        for video in sorted(arc_dir.iterdir()):
            if video.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            nfo_name = one_pace_nfo_name(video.name, season_number)
            if nfo_name is None:
                report.unmatched.append((video, None))
                continue
            source = season_metadata / nfo_name
            if not source.is_file():
                report.unmatched.append((video, None))
                continue
            planner.add_copy(
                source,
                video.with_suffix(".nfo"),
                "{} -> Season {} metadata".format(video.name, season_number),
                "one_pace_episode",
            )


def placement_description(placement):
    if "after_season" in placement:
        return "after Season {}".format(placement["after_season"])
    if "before_episode" in placement:
        return "before Season {} Episode {}".format(
            placement["before_season"], placement["before_episode"]
        )
    return "before Season {}".format(placement["before_season"])


def add_supplemental_actions(folder, manifest, planner, report):
    if folder is None:
        return
    planner.add_text(
        supplemental_season_nfo_text(manifest),
        folder / "season.nfo",
        "season.nfo for Season 00 supplemental episodes",
        "metadata",
    )
    poster = METADATA_DIR / "supplements" / "poster.png"
    if poster.is_file():
        planner.add_copy(
            poster,
            folder / "poster.png",
            "poster.png for Season 00 supplemental episodes",
            "metadata",
        )

    for video in sorted(folder.iterdir()):
        if video.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        status, candidates = match_supplemental_filename(video.name, manifest)
        if status == "unmatched":
            report.unmatched.append(
                (video, "Use the deterministic form 'One Piece - 0196.ext'.")
            )
            continue
        if status == "ambiguous":
            report.ambiguous.append((video, candidates))
            continue

        episode = manifest["catalog"][candidates[0]]
        arc = episode["arc"]
        description = "{} -> {} {:02d} / S00E{} / {}".format(
            video.name,
            arc["title"],
            episode["arc_episode"],
            episode["special_episode"],
            placement_description(arc["placement"]),
        )
        planner.add_text(
            supplemental_nfo_text(episode),
            video.with_suffix(".nfo"),
            description,
            "supplemental_episode",
        )


def build_plan(media_path, force=False, manifest_path=SUPPLEMENTS_PATH):
    manifest = load_supplements(manifest_path)
    series_path = Path(media_path)
    if not series_path.is_dir():
        raise FatalError("'{}' is not a valid directory".format(media_path))
    series_root = series_path.resolve()
    report = RunReport()
    planner = Planner(series_root, force, report)
    supplemental_folder = find_supplemental_folder(series_root, manifest, report)
    add_one_pace_actions(series_root, supplemental_folder, planner, report)
    add_supplemental_actions(supplemental_folder, manifest, planner, report)
    if report.unsafe:
        raise FatalError("Unsafe paths were rejected; no files were written", report)
    return series_root, report


def print_report(report, dry_run=False, applied=True):
    if dry_run:
        print("DRY RUN - no files will be changed")
    elif not applied:
        print("PLAN REJECTED - no files were changed")
    for action in report.actions:
        prefix = "Would copy" if isinstance(action, CopyAction) else "Would write"
        if dry_run or not applied:
            print("{}: {}".format(prefix, action.description))
        else:
            print("✓ {}".format(action.description))
    for description in report.skipped:
        print("· Skipped: {}".format(description))
    for path, recommendation in report.unmatched:
        print("⚠ Unmatched: {}".format(path))
        if recommendation:
            print("  {}".format(recommendation))
    for path, candidates in report.ambiguous:
        print(
            "⚠ Ambiguous: {} (candidate episodes: {})".format(
                path, ", ".join(str(candidate) for candidate in candidates)
            )
        )
    for unsafe in report.unsafe:
        print("✗ Unsafe path: {}".format(unsafe))

    counts = {
        "one_pace_episode": 0,
        "supplemental_episode": 0,
        "metadata": 0,
    }
    for action in report.actions:
        counts[action.category] += 1
    print("\nSummary{}:".format(" (planned)" if dry_run or not applied else ""))
    print("One Pace episode NFOs placed: {}".format(counts["one_pace_episode"]))
    print("Supplemental episode NFOs placed: {}".format(counts["supplemental_episode"]))
    print("Season/series metadata files placed: {}".format(counts["metadata"]))
    print("Existing files skipped: {}".format(len(report.skipped)))
    print("Unmatched files: {}".format(len(report.unmatched)))
    print("Ambiguous supplemental files: {}".format(len(report.ambiguous)))
    print("Unsafe paths rejected: {}".format(len(report.unsafe)))


def warn_if_root():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("WARNING: Do not run this script as root or with sudo.")


def process(media_path, force=False, dry_run=False, manifest_path=SUPPLEMENTS_PATH):
    warn_if_root()
    series_root, report = build_plan(media_path, force, manifest_path)
    if not dry_run:
        apply_actions(report.actions, series_root)
    print_report(report, dry_run=dry_run)
    return report


def place_file(source, destination, force, root):
    """Backward-compatible safe copy helper used by older callers/tests."""
    report = RunReport()
    planner = Planner(Path(root).resolve(), force, report)
    planner.add_copy(source, destination, str(destination), "metadata")
    if report.unsafe:
        raise FatalError(report.unsafe[0], report)
    if not report.actions:
        return False
    apply_actions(report.actions, Path(root).resolve())
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Place One Pace and supplemental NFO metadata for Jellyfin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 setup.py "/Volumes/MyDrive/Jellyfin/media/Anime/One Pace"
  python3 setup.py ~/media/One\\ Pace --dry-run
  python3 setup.py ~/media/One\\ Pace --force
        """,
    )
    parser.add_argument("path", help="Path to your One Pace series folder")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ordinary NFO/poster files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show every proposed action without writing files",
    )
    args = parser.parse_args(argv)
    try:
        process(args.path, force=args.force, dry_run=args.dry_run)
    except FatalError as exc:
        if exc.report is not None:
            print_report(exc.report, dry_run=args.dry_run, applied=False)
        print("Fatal error: {}".format(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
