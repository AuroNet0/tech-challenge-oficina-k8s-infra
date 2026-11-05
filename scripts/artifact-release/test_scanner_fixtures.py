import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import release
import check_secret_files as guard
import test_release


def archive_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def image_archive(path, payload, location="app/app.jar", service="os"):
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as archive:
        member = tarfile.TarInfo(location)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    layer_bytes = layer.getvalue()
    configuration = release.canonical({
        "os": "linux", "architecture": "amd64",
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + release.digest(layer_bytes)]},
        "config": {"User": "10001", "Env": [], "Labels": {
            "org.opencontainers.image.source": "https://github.com/" + release.REPOSITORIES[service],
            "org.opencontainers.image.revision": test_release.REVISION}},
    })
    with tarfile.open(path, "w") as archive:
        for name, content in {
            "manifest.json": release.canonical([{"Config": "config.json", "Layers": ["layer.tar"]}]),
            "config.json": configuration, "layer.tar": layer_bytes,
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return "sha256:" + release.digest(configuration)


class NettyFixtureTests(unittest.TestCase):
    def setUp(self):
        self.repository = release.REPOSITORIES["os"]
        self.class_name = "io/netty/handler/ssl/SslUtils.class"
        self.key = b"-----BEGIN " + b"PRIVATE KEY-----\n" + b"A" * 64 + b"\n-----END PRIVATE KEY-----"
        self.bytecode = b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + self.key
        self.dependency_name = "BOOT-INF/lib/netty-handler-4.2.17.Final.jar"
        self.metadata_name = "META-INF/maven/io.netty/netty-handler/pom.properties"
        self.dependency_entries = {
            self.class_name: self.bytecode,
            self.metadata_name: b"groupId=io.netty\nartifactId=netty-handler\nversion=4.2.17.Final\n",
        }
        self.dependency = archive_bytes(self.dependency_entries)
        self.addCleanup(patch.stopall)
        patch.object(release, "NETTY_HANDLER_SHA256", release.digest(self.dependency)).start()
        patch.object(release, "NETTY_PROBING_KEY_SHA256", release.digest(self.key)).start()
        patch.dict(release.NETTY_CLASS_SHA256, {self.class_name: release.digest(self.bytecode)}, clear=True).start()

    def application(self, dependency=None, name=None, extra=None, main=None):
        application = main or release.NETTY_APPLICATIONS[self.repository]
        entries = {
            "META-INF/MANIFEST.MF": b"Main-Class: org.springframework.boot.loader.launch.JarLauncher\nStart-Class: " + application.encode() + b"\n",
            "BOOT-INF/classes/" + application.replace(".", "/") + ".class": b"\xca\xfe\xba\xbe\x00\x00\x00\x41",
            name or self.dependency_name: self.dependency if dependency is None else dependency,
        }
        entries.update(extra or {})
        return archive_bytes(entries)

    def reject(self, payload, repository=None):
        with self.assertRaises(release.Rejected):
            release.zip_contents(payload, source_repository=repository or self.repository)

    def test_exact_fixture_requires_complete_context(self):
        release.zip_contents(self.application(), source_repository=self.repository)

    def test_default_scan_remains_strict(self):
        with self.assertRaises(release.Rejected):
            release.zip_contents(self.application())

    def test_foreign_repository_and_auth_rejected(self):
        for repository in ("Other/foreign", release.REPOSITORIES["auth"], release.REPOSITORIES["execution"]):
            self.reject(self.application(), repository)

    def test_wrong_boot_application_rejected(self):
        self.reject(self.application(main="foreign.Main"))

    def test_wrong_dependency_location_rejected(self):
        self.reject(self.application(name="BOOT-INF/lib/other.jar"))

    def test_bare_dependency_rejected(self):
        self.reject(self.dependency)

    def test_duplicate_or_nested_dependency_cannot_inherit_exception(self):
        nested = archive_bytes({"nested.jar": self.dependency})
        self.reject(self.application(dependency=nested, name="BOOT-INF/lib/other.jar"))

    def test_changed_dependency_hash_rejected(self):
        changed = archive_bytes({**self.dependency_entries, "changed.txt": b"changed"})
        self.reject(self.application(changed))

    def test_changed_class_hash_rejected_even_with_matching_dependency_hash(self):
        changed = archive_bytes({**self.dependency_entries, self.class_name: self.bytecode + b"changed"})
        with patch.object(release, "NETTY_HANDLER_SHA256", release.digest(changed)):
            self.reject(self.application(changed))

    def test_changed_fixture_bytes_rejected_independently(self):
        changed_class = self.bytecode.replace(b"A" * 64, b"B" * 64)
        changed = archive_bytes({**self.dependency_entries, self.class_name: changed_class})
        with patch.object(release, "NETTY_HANDLER_SHA256", release.digest(changed)), patch.dict(
                release.NETTY_CLASS_SHA256, {self.class_name: release.digest(changed_class)}, clear=True):
            self.reject(self.application(changed))

    def test_other_private_pem_outside_fixture_rejected(self):
        self.reject(self.application(extra={"BOOT-INF/classes/other.pem": self.key}))

    def test_other_private_key_class_in_same_dependency_rejected(self):
        changed = archive_bytes({**self.dependency_entries, "foreign.class": self.bytecode})
        with patch.object(release, "NETTY_HANDLER_SHA256", release.digest(changed)):
            self.reject(self.application(changed))

    def test_different_maven_origin_rejected(self):
        changed = archive_bytes({**self.dependency_entries, self.metadata_name: b"groupId=foreign\nartifactId=netty-handler\nversion=4.2.17.Final\n"})
        with patch.object(release, "NETTY_HANDLER_SHA256", release.digest(changed)):
            self.reject(self.application(changed))

    def test_different_maven_artifact_and_version_rejected(self):
        for metadata in (
            b"groupId=io.netty\nartifactId=foreign\nversion=4.2.17.Final\n",
            b"groupId=io.netty\nartifactId=netty-handler\nversion=4.2.18.Final\n",
        ):
            changed = archive_bytes({**self.dependency_entries, self.metadata_name: metadata})
            with patch.object(release, "NETTY_HANDLER_SHA256", release.digest(changed)):
                self.reject(self.application(changed))

    def test_future_dependency_filename_rejected(self):
        self.reject(self.application(name="BOOT-INF/lib/netty-handler-4.2.18.Final.jar"))

    def test_unauthorized_access_key_jwt_and_env_rejected(self):
        for name, content in {
            "BOOT-INF/classes/access.txt": b"AKIA" + b"Z" * 16,
            "BOOT-INF/classes/token.txt": b"eyJ" + b"A" * 32 + b"." + b"B" * 32 + b"." + b"C" * 32,
            "BOOT-INF/classes/.env": b"PASSWORD=synthetic_private_value",
            "BOOT-INF/classes/foreign.pem": b"-----BEGIN " + b"RSA PRIVATE KEY-----\n" + b"Z" * 64,
        }.items():
            with self.subTest(name=name):
                self.reject(self.application(extra={name: content}))

    def test_image_archive_uses_only_verified_application_location(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.tar"
            image_id = image_archive(path, self.application())
            release.verify_image_archive(path, "os", test_release.REVISION, image_id)
            for location in ("other/app.jar", "app/other.jar"):
                image_id = image_archive(path, self.application(), location)
                with self.assertRaises(release.Rejected):
                    release.verify_image_archive(path, "os", test_release.REVISION, image_id)
            image_id = image_archive(path, self.application(), service="execution")
            with self.assertRaises(release.Rejected):
                release.verify_image_archive(path, "execution", test_release.REVISION, image_id)

    def test_image_archive_preserves_nested_secret_detection(self):
        payload = self.application(extra={"BOOT-INF/lib/foreign.jar": archive_bytes({"private.pem": self.key})})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.tar"
            image_id = image_archive(path, payload)
            with self.assertRaises(release.Rejected):
                release.verify_image_archive(path, "os", test_release.REVISION, image_id)

    def test_receipt_attachment_and_validation_rescan_image(self):
        fixture = test_release.ReleaseTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.build_fixtures()
        folder = fixture.folder / "os"
        for payload, accepted in (
            (self.application(), True),
            (self.application(extra={"BOOT-INF/classes/private.pem": self.key}), False),
        ):
            image_id = image_archive(folder / "image.tar", payload)
            receipt = release.read_json(folder / "receipt.json")
            receipt["image"] = {**release.file_evidence(folder / "image.tar"), "image_id": image_id}
            (folder / "receipt.json").write_bytes(release.canonical(receipt))
            if accepted:
                release.attach_build(fixture.manifest, "os", folder, fixture.folder, fixture.catalog)
                release.validate_manifest(fixture.manifest, fixture.catalog, fixture.folder)
            else:
                with self.assertRaises(release.Rejected):
                    release.attach_build(fixture.manifest, "os", folder, fixture.folder, fixture.catalog)

    def test_direct_private_key_scan_remains_strict(self):
        with self.assertRaises(release.Rejected):
            release.scan(self.class_name, self.bytecode)


class SourceFixtureTests(unittest.TestCase):
    def setUp(self):
        self.repository = "AuroNet0/tech-challenge-oficina-auth"
        self.path = ".env.example"
        self.content = b"PASSWORD=REPLACE_WITH_PASSWORD\n"
        self.addCleanup(patch.stopall)
        self.table = patch.dict(guard.PUBLIC_SOURCE_FIXTURES, {}, clear=True)
        self.table.start()

    def allow(self, content, kind="example", payload=None):
        guard.PUBLIC_SOURCE_FIXTURES[(self.repository, self.path)] = {
            "sha256": hashlib.sha256(content).hexdigest(), "kind": kind,
            "payload_sha256": hashlib.sha256(payload).hexdigest() if payload else None,
        }

    def test_example_is_exactly_bound_to_origin_path_and_hash(self):
        self.allow(self.content)
        self.assertFalse(guard.unsafe_file(self.path, self.content, self.repository))
        self.assertTrue(guard.unsafe_file(self.path, self.content))
        self.assertTrue(guard.unsafe_file(self.path, self.content, "foreign/repository"))
        self.assertTrue(guard.unsafe_file("other/.env.example", self.content, self.repository))
        self.assertTrue(guard.unsafe_file(self.path, self.content + b"\n", self.repository))

    def test_example_does_not_disable_private_key_detection(self):
        content = self.content + b"-----BEGIN " + b"PRIVATE KEY-----\n" + b"B" * 64
        self.allow(content)
        self.assertTrue(guard.unsafe_file(self.path, content, self.repository))

    def test_example_does_not_disable_jwt_detection(self):
        content = self.content + b"eyJ" + b"A" * 32 + b"." + b"B" * 32 + b"." + b"C" * 32
        self.allow(content)
        self.assertTrue(guard.unsafe_file(self.path, content, self.repository))

    def test_example_does_not_disable_access_key_detection(self):
        content = self.content + b"ASIA" + b"B" * 16
        self.allow(content)
        self.assertTrue(guard.unsafe_file(self.path, content, self.repository))

    def test_negative_test_key_has_exact_file_and_payload_checksums(self):
        self.path = "src/test/Fixture.java"
        payload = b"ASIA" + b"B" * 16
        content = b"negative fixture " + payload
        self.allow(content, "test_access_key_id", payload)
        self.assertFalse(guard.unsafe_file(self.path, content, self.repository))
        self.assertTrue(guard.unsafe_file(self.path, content))
        self.assertTrue(guard.unsafe_file(self.path, content + b"x", self.repository))
        self.assertTrue(guard.unsafe_file("src/main/Fixture.java", content, self.repository))

    def test_other_key_does_not_inherit_fixture_exception(self):
        self.path = "src/test/Fixture.java"
        payload = b"ASIA" + b"B" * 16
        content = b"negative fixture " + b"ASIA" + b"C" * 16
        self.allow(content, "test_access_key_id", payload)
        self.assertTrue(guard.unsafe_file(self.path, content, self.repository))

    def test_test_key_exception_does_not_disable_jwt_detection(self):
        self.path = "src/test/Fixture.java"
        payload = b"ASIA" + b"B" * 16
        content = payload + b"\neyJ" + b"A" * 32 + b"." + b"B" * 32 + b"." + b"C" * 32
        self.allow(content, "test_access_key_id", payload)
        self.assertTrue(guard.unsafe_file(self.path, content, self.repository))

    def test_git_origin_is_observed_not_a_success_flag(self):
        self.allow(self.content)
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / self.path).write_bytes(self.content)
            for repository, expected in ((self.repository, 0), ("foreign/repository", 1)):
                origin = SimpleNamespace(returncode=0, stdout=("https://github.com/" + repository + ".git\n").encode())
                with patch.object(guard.subprocess, "run", return_value=origin), patch("sys.stdout", new=io.StringIO()):
                    self.assertEqual(guard.scan(folder, paths=[self.path]), expected)


if __name__ == "__main__":
    unittest.main()
