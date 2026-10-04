#!/usr/bin/env python3
"""Exercise APK export/version rules with fake APK bytes and Gradle metadata.

Run: python android-tv/tools/test_package_apk.py
These fixtures do not validate an APK manifest, a signing key or a signature.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from package_apk import export_apk, read_version


class PackageApkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="jzmedia-apk-export-test-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "android-tv"
        self.project.mkdir()
        self.output = Path(temporary.name) / "delivery"
        self.write_version()

    def write_version(self, name="0.5.1", code=7):
        (self.project / "version.properties").write_text(
            f"versionName={name}\nversionCode={code}\n", encoding="utf-8")

    def build_fixture(self, variant="debug", *, filename=None, contents=b"fake APK fixture"):
        name, code = read_version(self.project)
        directory = self.project / "app/build/outputs/apk" / variant
        directory.mkdir(parents=True, exist_ok=True)
        filename = filename or f"app-{variant}.apk"
        metadata = {
            "version": 3,
            "artifactType": {"type": "APK", "kind": "Directory"},
            "applicationId": "org.jzmedia.tv" + (".debug" if variant == "debug" else ""),
            "variantName": variant,
            "elements": [{
                "type": "SINGLE", "filters": [], "attributes": [],
                "versionCode": code,
                "versionName": name + ("-debug" if variant == "debug" else ""),
                "outputFile": filename,
            }],
        }
        (directory / filename).write_bytes(contents)
        self.write_metadata(variant, metadata)
        return metadata

    def write_metadata(self, variant, metadata):
        directory = self.project / "app/build/outputs/apk" / variant
        (directory / "output-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    def exported_files(self):
        return {path.name: path.read_bytes() for path in self.output.glob("*")}

    def assert_export_rejected(self, variant="debug"):
        before = self.exported_files()
        with self.assertRaises(ValueError):
            export_apk(self.project, variant, self.output)
        self.assertEqual(self.exported_files(), before)

    def test_debug_export_records_version_hash_and_source_without_filename_dates(self):
        contents = b"fake debug APK bytes; not a signed APK"
        self.build_fixture(contents=contents)
        revision = {"commit": "abc123", "dirty": True}
        artifact = export_apk(self.project, "debug", self.output, revision)
        self.assertEqual(artifact.name, "jzmedia-tv-0.5.1-debug.apk")
        self.assertEqual(artifact.read_bytes(), contents)
        digest = hashlib.sha256(contents).hexdigest()
        self.assertEqual(artifact.with_suffix(".apk.sha256").read_text(), f"{digest}  {artifact.name}\n")
        manifest = json.loads(artifact.with_suffix(".apk.json").read_text())
        self.assertEqual(manifest["file"], artifact.name)
        self.assertEqual(manifest["versionName"], "0.5.1-debug")
        self.assertEqual(manifest["versionCode"], 7)
        self.assertEqual(manifest["applicationId"], "org.jzmedia.tv.debug")
        self.assertEqual(manifest["variant"], "debug")
        self.assertFalse(manifest["unsigned"])
        self.assertEqual(manifest["sha256"], digest)
        self.assertEqual(manifest["source"], revision)
        self.assertTrue(manifest["exportedAt"])

    def test_release_filenames_distinguish_unsigned_build_output(self):
        # Naming follows Gradle output; fake fixtures do not prove a signature.
        for filename, expected, unsigned in (
            ("app-release.apk", "jzmedia-tv-0.5.1.apk", False),
            ("app-release-unsigned.apk", "jzmedia-tv-0.5.1-unsigned.apk", True),
        ):
            with self.subTest(filename=filename):
                self.build_fixture("release", filename=filename)
                artifact = export_apk(self.project, "release", self.output)
                self.assertEqual(artifact.name, expected)
                manifest = json.loads(artifact.with_suffix(".apk.json").read_text())
                self.assertEqual(manifest["versionName"], "0.5.1")
                self.assertEqual(manifest["applicationId"], "org.jzmedia.tv")
                self.assertEqual(manifest["unsigned"], unsigned)

    def test_repeat_export_preserves_original_manifest_and_package(self):
        self.build_fixture()
        export_apk(self.project, "debug", self.output, {"commit": "original"})
        before = self.exported_files()
        export_apk(self.project, "debug", self.output, {"commit": "later"})
        self.assertEqual(self.exported_files(), before)

    def test_changed_package_cannot_replace_already_delivered_version(self):
        self.build_fixture(contents=b"first build")
        export_apk(self.project, "debug", self.output)
        self.build_fixture(contents=b"different or re-signed build")
        self.assert_export_rejected()

    def test_conflicting_delivery_manifest_is_not_preserved_as_valid(self):
        self.build_fixture()
        artifact = export_apk(self.project, "debug", self.output)
        manifest_path = artifact.with_suffix(".apk.json")
        original = json.loads(manifest_path.read_text())
        for key, value in (("sha256", "0" * 64), ("versionCode", 6),
                           ("variant", "release"), ("applicationId", "org.jzmedia.tv"),
                           ("file", "different.apk"), ("versionName", "0.5.0-debug"),
                           ("unsigned", True)):
            with self.subTest(field=key):
                manifest_path.write_text(json.dumps({**original, key: value}), encoding="utf-8")
                self.assert_export_rejected()

    def test_mismatched_build_metadata_requires_rebuild(self):
        changes = (
            ("versionName", "0.5.0-debug"), ("versionName", "0.5.1"),
            ("versionCode", 6), ("versionCode", "7"),
        )
        for key, value in changes:
            with self.subTest(field=key, value=value):
                metadata = self.build_fixture()
                metadata["elements"][0][key] = value
                self.write_metadata("debug", metadata)
                self.assert_export_rejected()
        for key, value in (("variantName", "release"), ("applicationId", "org.jzmedia.tv")):
            with self.subTest(field=key):
                metadata = self.build_fixture()
                metadata[key] = value
                self.write_metadata("debug", metadata)
                self.assert_export_rejected()

    def test_split_or_multiple_apks_are_rejected_without_partial_export(self):
        for case in ("filtered", "multiple", "none"):
            with self.subTest(case=case):
                metadata = self.build_fixture()
                if case == "filtered":
                    metadata["elements"][0]["filters"] = [{"filterType": "ABI", "value": "arm64-v8a"}]
                elif case == "multiple":
                    metadata["elements"].append(dict(metadata["elements"][0]))
                else:
                    metadata["elements"] = []
                self.write_metadata("debug", metadata)
                self.assert_export_rejected()

    def test_metadata_cannot_export_a_path_outside_the_build_directory(self):
        for filename in ("../app-debug.apk", "/tmp/app-debug.apk", "app-debug.zip"):
            with self.subTest(filename=filename):
                metadata = self.build_fixture()
                metadata["elements"][0]["outputFile"] = filename
                self.write_metadata("debug", metadata)
                self.assert_export_rejected()

    def test_unsigned_debug_output_is_rejected(self):
        self.build_fixture(filename="app-debug-unsigned.apk")
        self.assert_export_rejected()

    def test_name_and_code_must_both_increase_for_a_new_delivered_version(self):
        self.write_version("0.5.1", 7)
        self.build_fixture()
        export_apk(self.project, "debug", self.output)
        for name, code in (("0.5.2", 7), ("0.5.2", 6), ("0.5.0", 8), ("0.5.0", 6), ("0.5.1", 8)):
            with self.subTest(name=name, code=code):
                self.write_version(name, code)
                # Check across variants too: global versionCode must not reset.
                self.build_fixture("release")
                self.assert_export_rejected("release")

    def test_same_version_can_be_exported_for_both_variants(self):
        self.build_fixture()
        debug = export_apk(self.project, "debug", self.output)
        self.build_fixture("release")
        release = export_apk(self.project, "release", self.output)
        self.assertTrue(debug.exists())
        self.assertTrue(release.exists())
        self.assertNotEqual(debug.name, release.name)

    def test_version_order_is_numeric_and_code_does_not_reset(self):
        for name, code in (("0.9.9", 99), ("0.10.0", 100), ("1.0.0", 101)):
            self.write_version(name, code)
            self.build_fixture()
            exported = export_apk(self.project, "debug", self.output)
            self.assertEqual(exported.name, f"jzmedia-tv-{name}-debug.apk")
        self.assertEqual(len(list(self.output.glob("*.apk"))), 3)

    def test_version_source_rejects_suffixes_dates_invalid_numbers_and_duplicates(self):
        for name, code in (("0.5.1-dev", "7"), ("0.5.1-20261004", "7"), ("0.05.1", "7"),
                           ("0.5", "7"), ("0.5.1", "0"), ("0.5.1", "-1"),
                           ("0.5.1", "2100000001"), ("0.5.1", "seven")):
            with self.subTest(name=name, code=code):
                self.write_version(name, code)
                with self.assertRaises(ValueError):
                    read_version(self.project)
        (self.project / "version.properties").write_text(
            "versionName=0.5.1\nversionName=0.5.2\nversionCode=7\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_version(self.project)


if __name__ == "__main__":
    unittest.main(verbosity=2)
