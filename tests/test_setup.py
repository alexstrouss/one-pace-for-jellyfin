import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "setup.py"
SPEC = importlib.util.spec_from_file_location("one_pace_setup", str(SCRIPT_PATH))
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class SetupTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = setup.load_supplements()

    def make_root(self, temp_dir):
        root = Path(temp_dir) / "One Pace"
        root.mkdir()
        return root.resolve()

    def make_arc(self, root, name="[One Pace][218-236] Jaya [1080p]"):
        arc = root / name
        arc.mkdir()
        return arc

    def make_video(self, directory, filename):
        video = directory / filename
        video.write_bytes(b"test video placeholder")
        return video

    def run_process(self, root, force=False, dry_run=False, manifest_path=None):
        output = io.StringIO()
        kwargs = {"force": force, "dry_run": dry_run}
        if manifest_path is not None:
            kwargs["manifest_path"] = manifest_path
        with contextlib.redirect_stdout(output):
            report = setup.process(root, **kwargs)
        return report, output.getvalue()

    def write_manifest(self, directory, data):
        path = Path(directory) / "supplements.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def assert_symlink_available(self, link, target, is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=is_directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Symlinks are unavailable: {}".format(exc))


class ExistingOnePaceCompatibilityTests(SetupTestCase):
    def test_normal_jaya_filename_selects_existing_metadata(self):
        filename = "[One Pace][218-220] Jaya 01 [1080p][2BBCD106].mkv"
        self.assertEqual(setup.one_pace_nfo_name(filename, 15), "S15E01.nfo")

    def test_skypiea_alternate_g8_selects_alternate_metadata(self):
        filename = (
            "[One Pace][302-303] Skypiea 25 Alternate (G-8) "
            "[1080p][90C45C25].mkv"
        )
        self.assertEqual(
            setup.one_pace_nfo_name(filename, 16), "S16E25_alternate.nfo"
        )

    def test_run_without_supplemental_folder_preserves_one_pace_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            arc = self.make_arc(root)
            video = self.make_video(
                arc, "[One Pace][218-220] Jaya 01 [1080p][2BBCD106].mkv"
            )

            report, _ = self.run_process(root)

            self.assertTrue((root / "tvshow.nfo").is_file())
            self.assertTrue((arc / "season.nfo").is_file())
            self.assertTrue((arc / "poster.png").is_file())
            self.assertTrue(video.with_suffix(".nfo").is_file())
            self.assertFalse(report.ambiguous)
            self.assertFalse(report.unsafe)

    def test_existing_nfo_is_skipped_without_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            arc = self.make_arc(root)
            video = self.make_video(
                arc, "[One Pace][218-220] Jaya 01 [1080p][2BBCD106].mkv"
            )
            destination = video.with_suffix(".nfo")
            destination.write_text("user metadata", encoding="utf-8")

            report, _ = self.run_process(root)

            self.assertEqual(destination.read_text(encoding="utf-8"), "user metadata")
            self.assertTrue(any(video.name in item for item in report.skipped))

    def test_regular_nfo_is_replaced_with_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            arc = self.make_arc(root)
            video = self.make_video(
                arc, "[One Pace][218-220] Jaya 01 [1080p][2BBCD106].mkv"
            )
            destination = video.with_suffix(".nfo")
            destination.write_text("user metadata", encoding="utf-8")

            self.run_process(root, force=True)

            expected = (setup.METADATA_DIR / "seasons" / "15" / "S15E01.nfo").read_text(
                encoding="utf-8"
            )
            self.assertEqual(destination.read_text(encoding="utf-8"), expected)

    def test_unsupported_video_extension_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            folder = root / "Season 00 - Supplemental Episodes"
            folder.mkdir()
            ignored = self.make_video(folder, "One Piece - 0196.mov")

            report, _ = self.run_process(root)

            self.assertFalse(ignored.with_suffix(".nfo").exists())
            self.assertFalse(report.unmatched)


class SupplementalFolderTests(SetupTestCase):
    def test_folder_alias_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            folder = root / "sPeCiAlS"
            folder.mkdir()
            video = self.make_video(folder, "One Piece - 0196.mkv")

            self.run_process(root)

            self.assertTrue((folder / "season.nfo").is_file())
            self.assertTrue(video.with_suffix(".nfo").is_file())

    def test_more_than_one_supplemental_folder_is_fatal_before_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "Season 00").mkdir()
            (root / "Specials").mkdir()

            with self.assertRaisesRegex(setup.FatalError, "More than one"):
                self.run_process(root)

            self.assertFalse((root / "tvshow.nfo").exists())

    def test_symlinked_supplemental_folder_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            outside = Path(temp_dir) / "outside"
            outside.mkdir()
            link = root / "Season 00 - Supplemental Episodes"
            self.assert_symlink_available(link, outside, is_directory=True)

            with self.assertRaisesRegex(setup.FatalError, "Unsafe paths") as raised:
                self.run_process(root)

            self.assertTrue(raised.exception.report.unsafe)
            self.assertFalse((root / "tvshow.nfo").exists())

    def test_root_supplied_through_symlink_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            real_root = Path(temp_dir) / "Real One Pace"
            real_root.mkdir()
            root_link = Path(temp_dir) / "One Pace"
            self.assert_symlink_available(root_link, real_root, is_directory=True)
            folder = real_root / "Season 00"
            folder.mkdir()
            self.make_video(folder, "One Piece - 0196.mkv")

            self.run_process(root_link)

            self.assertTrue((folder / "One Piece - 0196.nfo").is_file())


class SupplementalFilenameTests(SetupTestCase):
    def assert_matches(self, filename, episode):
        status, candidates = setup.match_supplemental_filename(filename, self.manifest)
        self.assertEqual(status, "matched", filename)
        self.assertEqual(candidates, [episode], filename)

    def test_all_recommended_g8_filename_forms_match(self):
        filenames = (
            "One Piece - 0196.mkv",
            "One Piece - 196 [1080p][Dual Audio].mkv",
            "One Piece Episode 196.mp4",
            "One Piece Ep 196.mkv",
            "[196] A State of Emergency.mkv",
            "196 - A State of Emergency.mkv",
            "[Release Group] One Piece - 196 [1080p].mkv",
        )
        for filename in filenames:
            with self.subTest(filename=filename):
                self.assert_matches(filename, 196)

    def test_resolution_bit_depth_year_and_crc_are_not_episode_numbers(self):
        filenames = (
            "One Piece [1080p][10bit][2004][ABC206FF].mkv",
            "1080 - One Piece 10bit 2004 ABC206FF.mkv",
            "[Release Group] One Piece [1080p][ABC206FF].mkv",
        )
        for filename in filenames:
            with self.subTest(filename=filename):
                status, candidates = setup.match_supplemental_filename(
                    filename, self.manifest
                )
                self.assertEqual(status, "unmatched")
                self.assertEqual(candidates, [])

    def test_two_valid_episode_numbers_are_ambiguous(self):
        status, candidates = setup.match_supplemental_filename(
            "One Piece Episode 196 - One Piece - 197.mkv", self.manifest
        )
        self.assertEqual(status, "ambiguous")
        self.assertEqual(candidates, [196, 197])

    def test_optional_exact_alias_and_regex_matching(self):
        data = json.loads(setup.SUPPLEMENTS_PATH.read_text(encoding="utf-8"))
        data["arcs"][0]["episodes"][0]["filename_aliases"] = ["Emergency Alias"]
        data["arcs"][0]["episodes"][1]["filename_regexes"] = [r"Special Cut 197$"]
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = setup.load_supplements(self.write_manifest(temp_dir, data))
            self.assertEqual(
                setup.match_supplemental_filename("emergency alias.MKV", manifest),
                ("matched", [196]),
            )
            self.assertEqual(
                setup.match_supplemental_filename("Special Cut 197.mkv", manifest),
                ("matched", [197]),
            )

    def test_unmatched_supplemental_file_creates_no_nfo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            folder = root / "Season 00 - Supplemental Episodes"
            folder.mkdir()
            video = self.make_video(folder, "A State of Emergency.mkv")

            report, output = self.run_process(root)

            self.assertFalse(video.with_suffix(".nfo").exists())
            self.assertEqual(len(report.unmatched), 1)
            self.assertIn("One Piece - 0196.ext", output)

    def test_ambiguous_supplemental_file_creates_no_nfo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            folder = root / "Season 00"
            folder.mkdir()
            video = self.make_video(
                folder, "One Piece Episode 196 - One Piece - 197.mkv"
            )

            report, _ = self.run_process(root)

            self.assertFalse(video.with_suffix(".nfo").exists())
            self.assertEqual(report.ambiguous[0][1], [196, 197])


class SupplementalXmlTests(SetupTestCase):
    def test_generated_nfo_xml_parses(self):
        content = setup.supplemental_nfo_text(self.manifest["catalog"][196])
        root = ET.fromstring(content)
        self.assertEqual(root.tag, "episodedetails")

    def test_g8_episode_196_has_required_numbering_and_placement(self):
        root = ET.fromstring(
            setup.supplemental_nfo_text(self.manifest["catalog"][196])
        )
        self.assertEqual(root.findtext("season"), "0")
        self.assertEqual(root.findtext("episode"), "196")
        self.assertEqual(root.findtext("airsafter_season"), "16")
        self.assertEqual(
            [node.text for node in root.findall("tag")],
            ["Supplemental", "G-8", "Anime Original"],
        )

    def test_episodes_196_through_206_have_stable_special_numbers(self):
        for source in range(196, 207):
            with self.subTest(source=source):
                episode = self.manifest["catalog"][source]
                root = ET.fromstring(setup.supplemental_nfo_text(episode))
                self.assertEqual(episode["special_episode"], source)
                self.assertEqual(root.findtext("season"), "0")
                self.assertEqual(root.findtext("episode"), str(source))

    def test_optional_fields_render_and_are_xml_escaped(self):
        episode = copy.deepcopy(self.manifest["catalog"][196])
        episode.update(
            {
                "title": "G-8 <One> & Two",
                "plot": "A <plot> & more",
                "aired": "2004-06-20",
                "premiered": "2004-06-21",
            }
        )

        content = setup.supplemental_nfo_text(episode)
        root = ET.fromstring(content)

        self.assertIn("&lt;One&gt; &amp; Two", content)
        self.assertEqual(root.findtext("title"), "G-8 <One> & Two")
        self.assertEqual(root.findtext("plot"), "A <plot> & more")
        self.assertEqual(root.findtext("aired"), "2004-06-20")
        self.assertEqual(root.findtext("premiered"), "2004-06-21")

    def test_season_zero_metadata_parses_and_has_title(self):
        root = ET.fromstring(setup.supplemental_season_nfo_text(self.manifest))
        self.assertEqual(root.findtext("seasonnumber"), "0")
        self.assertEqual(root.findtext("title"), "0. Supplemental Episodes")

    def test_before_season_and_episode_placement_tags(self):
        episode = copy.deepcopy(self.manifest["catalog"][196])
        episode["arc"]["placement"] = {"before_season": 17, "before_episode": 1}
        root = ET.fromstring(setup.supplemental_nfo_text(episode))
        self.assertIsNone(root.find("airsafter_season"))
        self.assertEqual(root.findtext("airsbefore_season"), "17")
        self.assertEqual(root.findtext("airsbefore_episode"), "1")


class FilesystemSafetyTests(SetupTestCase):
    def test_destination_symlink_is_rejected_even_with_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            target = Path(temp_dir) / "important.nfo"
            target.write_text("do not overwrite", encoding="utf-8")
            destination = root / "tvshow.nfo"
            self.assert_symlink_available(destination, target)

            with self.assertRaisesRegex(setup.FatalError, "Unsafe paths"):
                self.run_process(root, force=True)

            self.assertEqual(target.read_text(encoding="utf-8"), "do not overwrite")

    def test_broken_destination_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            destination = root / "tvshow.nfo"
            self.assert_symlink_available(destination, Path(temp_dir) / "missing.nfo")

            with self.assertRaisesRegex(setup.FatalError, "Unsafe paths"):
                self.run_process(root, force=True)

    def test_symlinked_one_pace_arc_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            outside = Path(temp_dir) / "outside"
            outside.mkdir()
            arc = root / "[One Pace][218-236] Jaya [1080p]"
            self.assert_symlink_available(arc, outside, is_directory=True)

            with self.assertRaisesRegex(setup.FatalError, "Unsafe paths"):
                self.run_process(root)

            self.assertFalse((root / "tvshow.nfo").exists())

    def test_repository_metadata_sources_are_not_modified(self):
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in setup.METADATA_DIR.rglob("*")
            if path.is_file()
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            folder = root / "Season 00"
            folder.mkdir()
            self.make_video(folder, "One Piece - 0196.mkv")
            self.run_process(root)
        after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before
        }
        self.assertEqual(before, after)


class DryRunAndPlanningTests(SetupTestCase):
    def test_dry_run_creates_no_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            arc = self.make_arc(root)
            self.make_video(
                arc, "[One Pace][218-220] Jaya 01 [1080p][2BBCD106].mkv"
            )
            folder = root / "Season 00 - Supplemental Episodes"
            folder.mkdir()
            self.make_video(folder, "One Piece - 0196.mkv")
            before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

            report, output = self.run_process(root, dry_run=True)

            after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertIn("DRY RUN", output)
            self.assertTrue(report.actions)

    def test_duplicate_manifest_episode_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            data = json.loads(setup.SUPPLEMENTS_PATH.read_text(encoding="utf-8"))
            duplicate = dict(data["arcs"][0]["episodes"][0])
            duplicate["arc_episode"] = 99
            data["arcs"][0]["episodes"].append(duplicate)
            manifest_path = self.write_manifest(temp_dir, data)

            with self.assertRaisesRegex(setup.FatalError, "Duplicate.*source_episode"):
                self.run_process(root, manifest_path=manifest_path)

            self.assertFalse((root / "tvshow.nfo").exists())

    def test_invalid_manifest_variants_are_fatal(self):
        base = json.loads(setup.SUPPLEMENTS_PATH.read_text(encoding="utf-8"))
        variants = []

        data = copy.deepcopy(base)
        data["schema_version"] = 2
        variants.append(("schema", data, "schema_version"))

        data = copy.deepcopy(base)
        data["arcs"].append(copy.deepcopy(data["arcs"][0]))
        variants.append(("arc id", data, "Duplicate supplemental arc ID"))

        data = copy.deepcopy(base)
        data["arcs"][0]["title"] = ""
        variants.append(("title", data, "missing a title"))

        data = copy.deepcopy(base)
        data["arcs"][0]["placement"] = {
            "after_season": 16,
            "before_season": 17,
        }
        variants.append(("placement", data, "exactly one placement"))

        data = copy.deepcopy(base)
        del data["arcs"][0]["placement"]
        variants.append(("missing placement", data, "missing placement"))

        data = copy.deepcopy(base)
        data["arcs"][0]["episodes"][0]["source_episode"] = 0
        variants.append(("number", data, "invalid source_episode"))

        data = copy.deepcopy(base)
        data["arcs"][0]["episodes"][0]["aired"] = "2004-99-99"
        variants.append(("date", data, "Invalid ISO date"))

        data = copy.deepcopy(base)
        data["arcs"][0]["episodes"][1]["special_episode"] = 196
        variants.append(("special episode", data, "Duplicate.*special_episode"))

        data = copy.deepcopy(base)
        data["arcs"][0]["episodes"][1]["arc_episode"] = 1
        variants.append(("arc episode", data, "Duplicate arc_episode"))

        data = copy.deepcopy(base)
        data["arcs"][0]["episodes"][0]["filename_regexes"] = ["["]
        variants.append(("regex", data, "Invalid filename regex"))

        with tempfile.TemporaryDirectory() as temp_dir:
            for name, data, message in variants:
                with self.subTest(name=name):
                    path = self.write_manifest(temp_dir, data)
                    with self.assertRaisesRegex(setup.FatalError, message):
                        setup.load_supplements(path)


if __name__ == "__main__":
    unittest.main()
