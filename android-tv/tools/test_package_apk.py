#!/usr/bin/env python3
"""Exercise APK export/version rules with fake APK bytes and Gradle metadata.

Run: python android-tv/tools/test_package_apk.py
These fixtures do not validate an APK manifest, a signing key or a signature.
"""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import package_apk
from package_apk import export_apk, git_revision, read_version, resolve_revision, verify_signature


COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40


class PackageApkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="jzmedia-apk-export-test-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "android-tv"
        self.project.mkdir()
        self.output = Path(temporary.name) / "delivery"
        self.write_version()
        signing = patch("package_apk.verify_signature", return_value={
            "verified": True, "certificateSha256": "c" * 64, "debugCertificate": False,
        })
        self.signature = signing.start()
        self.addCleanup(signing.stop)

    def write_version(self, name="0.5.1", code=7):
        (self.project.parent / "version.properties").write_text(
            f"versionName={name}\nversionCode={code}\n", encoding="utf-8")

    def build_fixture(self, variant="debug", *, filename=None, contents=b"fake APK fixture", revision=None):
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
        record = {"versionName": name, "versionCode": code,
                  "source": revision or {"commit": COMMIT, "dirty": False}}
        with zipfile.ZipFile(directory / filename, "w") as archive:
            archive.writestr(zipfile.ZipInfo("assets/jzmedia-build.json"), json.dumps(record))
            archive.writestr(zipfile.ZipInfo("fixture-content.bin"), contents)
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
        revision = {"commit": COMMIT, "dirty": True}
        self.build_fixture(contents=contents, revision=revision)
        contents = (self.project / "app/build/outputs/apk/debug/app-debug.apk").read_bytes()
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

    def test_source_archive_can_be_packaged_without_git_installed(self):
        self.build_fixture(revision={"commit": None, "dirty": None})
        with patch("package_apk.subprocess.run", side_effect=FileNotFoundError("git")):
            revision = git_revision(self.project)
        self.assertEqual(revision, {"commit": None, "dirty": None})
        artifact = export_apk(self.project, "debug", self.output, revision)
        manifest = json.loads(artifact.with_suffix(".apk.json").read_text())
        self.assertEqual(manifest["source"], revision)

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
        revision = {"commit": COMMIT, "dirty": False}
        export_apk(self.project, "debug", self.output, revision)
        before = self.exported_files()
        export_apk(self.project, "debug", self.output, revision)
        self.assertEqual(self.exported_files(), before)

    def test_stale_apk_cannot_be_exported_as_a_different_commit(self):
        self.build_fixture()
        with self.assertRaisesRegex(ValueError, "source differs"):
            export_apk(self.project, "debug", self.output, {"commit": OTHER_COMMIT, "dirty": False})
        self.assertFalse(self.output.exists())

    def test_existing_manifest_cannot_hide_different_source(self):
        self.build_fixture()
        artifact = export_apk(self.project, "debug", self.output)
        manifest = artifact.with_suffix(".apk.json")
        record = json.loads(manifest.read_text())
        record["source"]["commit"] = OTHER_COMMIT
        manifest.write_text(json.dumps(record))
        self.assert_export_rejected()

    def test_missing_embedded_record_requires_a_rebuild(self):
        self.build_fixture()
        with zipfile.ZipFile(self.project / "app/build/outputs/apk/debug/app-debug.apk", "w") as archive:
            archive.writestr("fixture-content.bin", b"old build")
        self.assert_export_rejected()

    def test_debug_signed_release_is_not_a_formal_delivery(self):
        self.build_fixture("release")
        self.signature.return_value["debugCertificate"] = True
        self.assert_export_rejected("release")

    def test_unsigned_release_does_not_claim_verified_signature(self):
        self.build_fixture("release", filename="app-release-unsigned.apk")
        artifact = export_apk(self.project, "release", self.output)
        record = json.loads(artifact.with_suffix(".apk.json").read_text())
        self.signature.assert_not_called()
        self.assertFalse(record["signing"]["verified"])
        self.assertIsNone(record["signing"]["certificateSha256"])

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
        (self.project.parent / "version.properties").write_text(
            "versionName=0.5.1\nversionName=0.5.2\nversionCode=7\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_version(self.project)

    def test_android_local_version_file_cannot_override_the_shared_source(self):
        (self.project / "version.properties").write_text("versionName=9.9.9\nversionCode=999\n")
        self.assertEqual(read_version(self.project), ("0.5.1", 7))

    def test_commit_override_must_match_source_and_be_a_full_hash(self):
        with patch("package_apk.git_revision", return_value={"commit": COMMIT, "dirty": False}):
            self.assertEqual(resolve_revision(self.project, COMMIT), {"commit": COMMIT, "dirty": False})
            for invalid in (OTHER_COMMIT, "abc123", COMMIT.upper()):
                with self.subTest(commit=invalid), self.assertRaises(ValueError):
                    resolve_revision(self.project, invalid)

    def test_git_archive_accepts_explicit_commit_with_clean_provenance(self):
        with patch("package_apk.git_revision", return_value={"commit": None, "dirty": None}):
            self.assertEqual(resolve_revision(self.project, COMMIT), {"commit": COMMIT, "dirty": False})
            self.assertEqual(resolve_revision(self.project), {"commit": None, "dirty": None})

    def test_cli_passes_commit_and_offline_to_gradle_and_exports_to_requested_directory(self):
        revision = {"commit": COMMIT, "dirty": False}
        artifact = self.output / "jzmedia-tv-0.5.1-debug.apk"
        with patch.object(package_apk, "PROJECT", self.project), \
                patch.object(package_apk, "resolve_revision", return_value=revision), \
                patch.object(package_apk.subprocess, "run") as run, \
                patch.object(package_apk, "export_apk", return_value=artifact) as export:
            package_apk.main(["debug", "--offline", "--commit", COMMIT, "--output", str(self.output)])
        command = run.call_args.args[0]
        self.assertIn("--offline", command)
        self.assertIn(f"-PjzmediaGitCommit={COMMIT}", command)
        self.assertIn("-PjzmediaGitDirty=false", command)
        export.assert_called_once_with(self.project, "debug", self.output, revision)

    def test_cli_rejects_revision_or_version_drift_before_export(self):
        cases = (([{"commit": COMMIT, "dirty": False}, {"commit": OTHER_COMMIT, "dirty": False}],
                  [("0.5.1", 7), ("0.5.1", 7)]),
                 ([{"commit": COMMIT, "dirty": False}] * 2, [("0.5.1", 7), ("0.5.2", 8)]))
        for revisions, versions in cases:
            with self.subTest(revisions=revisions, versions=versions), \
                    patch.object(package_apk, "resolve_revision", side_effect=revisions), \
                    patch.object(package_apk, "read_version", side_effect=versions), \
                    patch.object(package_apk.subprocess, "run"), \
                    patch.object(package_apk, "export_apk") as export:
                with self.assertRaisesRegex(ValueError, "changed during"):
                    package_apk.main(["debug"])
                export.assert_not_called()


class SignatureTests(unittest.TestCase):
    def test_signature_certificate_digest_comes_from_successful_apksigner_verification(self):
        output = "Signer #1 certificate DN: CN=Android Debug, O=Android, C=US\n" \
                 + "Signer #1 certificate SHA-256 digest: " + "C" * 64 + "\n"
        with patch("package_apk.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=output)) as run:
            record = verify_signature(Path("example.apk"), Path("/sdk/apksigner"))
        self.assertEqual(record, {"verified": True, "certificateSha256": "c" * 64, "debugCertificate": True})
        self.assertEqual(run.call_args.args[0], ["/sdk/apksigner", "verify", "--print-certs", "example.apk"])

    def test_debug_common_name_is_detected_at_the_end_of_a_certificate_dn(self):
        output = "Signer #1 certificate DN: C=US, O=Android, CN=Android Debug\n" \
                 + "Signer #1 certificate SHA-256 digest: " + "c" * 64 + "\n"
        with patch("package_apk.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=output)):
            self.assertTrue(verify_signature(Path("example.apk"), Path("/sdk/apksigner"))["debugCertificate"])

    def test_invalid_signature_or_missing_certificate_fails(self):
        for code, output in ((1, "invalid signature"), (0, "no certificate digest")):
            with self.subTest(code=code), patch("package_apk.subprocess.run", return_value=SimpleNamespace(
                    returncode=code, stdout=output, stderr="")):
                with self.assertRaises(ValueError):
                    verify_signature(Path("example.apk"), Path("/sdk/apksigner"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
