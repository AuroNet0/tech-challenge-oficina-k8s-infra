import copy
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from release import ARTIFACTS, HANDLER, INTERNAL, INFRA_REPOSITORY, ROOT, REPOSITORIES, OfflinePublisher, Rejected, attach_build, canonical, digest, dockerfile_contract, image_evidence, jwt_contract, local_environment, maven_evidence, plan, provenance, scan, scope, validate_manifest, verify_jar, verify_layer, write_json, zip_contents


ACCOUNT = "123456789012"
REVISION = "a" * 40


def jar_bytes(helper=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(zipfile.ZipInfo("oficina/edge/AuthDatabaseBootstrap.class" if helper else HANDLER, (2026, 11, 2, 0, 0, 0)), b"\xca\xfe\xba\xbe\x00\x00\x00\x41")
        archive.writestr(zipfile.ZipInfo("software/amazon/awssdk/services/secretsmanager/SecretsManagerClient.class", (2026, 11, 2, 0, 0, 0)), b"fixture")
        if not helper:
            archive.writestr(zipfile.ZipInfo(INTERNAL + ".class", (2026, 11, 2, 0, 0, 0)), b"fixture")
    return stream.getvalue()


def reports(folder):
    target = folder / "source/target"
    (target / "surefire-reports").mkdir(parents=True)
    (target / "surefire-reports/TEST-fixture.xml").write_text('<testsuite tests="10" failures="0" errors="0" skipped="0"/>')
    (target / "site/jacoco").mkdir(parents=True)
    (target / "site/jacoco/jacoco.xml").write_text('<report><counter type="LINE" covered="80" missed="20"/></report>')


def image_tar(path, service):
    config = {"os": "linux", "architecture": "amd64", "rootfs": {"type": "layers", "diff_ids": []}, "config": {
        "User": "10001", "Labels": {"org.opencontainers.image.source": "https://github.com/" + REPOSITORIES[service],
                                      "org.opencontainers.image.revision": REVISION}, "Env": []}}
    if service == "auth":
        config["config"]["Entrypoint"] = ["java", "-cp", "/app/app.jar", INTERNAL.replace("/", ".")]
    config_bytes = canonical(config)
    image_id = "sha256:" + digest(config_bytes)
    with tarfile.open(path, "w") as archive:
        for name, content in [("manifest.json", canonical([{"Config": "config.json", "Layers": []}])), ("config.json", config_bytes)]:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return image_id


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.catalog = json.loads((ROOT / "phase4/environments/catalog.json").read_text())
        self.request = {"scope": {"environment_id": "demo-a", "account": ACCOUNT, "region": "us-east-1"},
                        "infra_revision": REVISION, "applications": {
                            name: {"repository": repository, "revision": REVISION} for name, repository in REPOSITORIES.items()}}
        self.manifest = plan(self.request, self.catalog)
        self.jwt = {"issuer": "https://fixture.execute-api.us-east-1.amazonaws.com", "audience": "tc-oficina-p4-demo-a",
                    "jwks_uri": "https://fixture.execute-api.us-east-1.amazonaws.com/.well-known/jwks.json"}

    def build_fixtures(self):
        for service in REPOSITORIES:
            folder = self.folder / service
            folder.mkdir()
            reports(folder)
            (folder / "application.jar").write_bytes(jar_bytes())
            image_id = image_tar(folder / "image.tar", service)
            with tarfile.open(folder / "source.tar", "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": REVISION}) as archive:
                member = tarfile.TarInfo("origin.txt")
                member.size = len(b"fixture")
                archive.addfile(member, io.BytesIO(b"fixture"))
            receipt = {"service": service, "source": self.manifest["applications"][service], "remote": None,
                       "source_archive_sha256": digest((folder / "source.tar").read_bytes()),
                       "jar": {"file": "application.jar", "sha256": digest((folder / "application.jar").read_bytes()), "size": (folder / "application.jar").stat().st_size},
                       "image": {"file": "image.tar", "sha256": digest((folder / "image.tar").read_bytes()), "size": (folder / "image.tar").stat().st_size, "image_id": image_id}}
            write_json(folder / "receipt.json", receipt)
            attach_build(self.manifest, service, folder, self.folder, self.catalog)

    def remote_fixture(self):
        self.build_fixtures()
        self.catalog["environments"]["demo-a"].update(enabled=True, aws_account_id=ACCOUNT)
        clients = SimpleNamespace(ecr=Mock(), s3=Mock(), functions=Mock(), layer_version=7)
        prefix = "tc-oficina-p4-demo-a"
        remote_manifests = {}
        for service in REPOSITORIES:
            receipt = json.loads((self.folder / service / "receipt.json").read_text())
            content = canonical({"config": {"digest": receipt["image"]["image_id"]}})
            remote_manifests[prefix + "-" + service] = content
        clients.ecr.describe_repositories.side_effect = lambda repositoryNames: {"repositories": [{
            "registryId": ACCOUNT, "repositoryUri": f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/{repositoryNames[0]}", "imageTagMutability": "IMMUTABLE"}]}
        clients.ecr.describe_images.side_effect = lambda repositoryName, imageIds: {"imageDetails": [{
            "registryId": ACCOUNT, "repositoryName": repositoryName, "imageTags": [REVISION], "imageDigest": "sha256:" + digest(remote_manifests[repositoryName])}]}
        clients.ecr.batch_get_image.side_effect = lambda repositoryName, imageIds: {"images": [{"imageManifest": remote_manifests[repositoryName].decode()}]}
        clients.s3.get_bucket_location.return_value = {"LocationConstraint": None}
        clients.s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
        clients.s3.get_public_access_block.return_value = {"PublicAccessBlockConfiguration": {name: True for name in ["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"]}}
        clients.s3.get_object.side_effect = lambda **arguments: {"VersionId": "synthetic-version-only", "Body": io.BytesIO(jar_bytes())}
        clients.functions.get_layer_version.return_value = {"LayerVersionArn": f"arn:aws:lambda:us-east-1:{ACCOUNT}:layer:{prefix}-auth-bootstrap:7", "CompatibleRuntimes": ["java21"], "CompatibleArchitectures": ["x86_64"]}
        observed = {"account": ACCOUNT, "region": "us-east-1", "arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/{prefix}-deploy/synthetic"}
        return clients, observed

    def simulate(self, clients, observed):
        return OfflinePublisher().simulate(self.manifest, self.catalog, clients, observed, self.jwt, self.jwt, self.folder)

    def test_planned_release_is_non_sensitive_and_not_published(self):
        self.assertEqual(len(self.manifest["artifacts"]), 6)
        validate_manifest(self.manifest, self.catalog)
        self.assertTrue(all(artifact["remote"] is None and artifact["state"] == "planned" for artifact in self.manifest["artifacts"].values()))

    def test_build_environment_does_not_inherit_credentials(self):
        with patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "synthetic_only", "AWS_WEB_IDENTITY_TOKEN_FILE": "/foreign", "MAVEN_OPTS": "foreign", "JAVA_TOOL_OPTIONS": "foreign", "DOCKER_CONFIG": "/foreign"}):
            environment = local_environment(self.folder)
        for field in ["AWS_SECRET_ACCESS_KEY", "AWS_WEB_IDENTITY_TOKEN_FILE", "MAVEN_OPTS", "JAVA_TOOL_OPTIONS"]:
            self.assertNotIn(field, environment)
        self.assertEqual(environment["AWS_EC2_METADATA_DISABLED"], "true")
        self.assertEqual(Path(environment["AWS_SHARED_CREDENTIALS_FILE"]).read_bytes(), b"")
        self.assertEqual(Path(environment["AWS_CONFIG_FILE"]).read_bytes(), b"")
        self.assertEqual(Path(environment["DOCKER_CONFIG"]), self.folder / "docker-config")

    def test_invalid_environment_and_region_account(self):
        for values in [{"environment_id": "demo-c"}, {"account": "123"}, {"region": "us-west-2"}]:
            with self.subTest(values=values), self.assertRaises(Rejected):
                scope(self.catalog, {**self.request["scope"], **values})
        with self.assertRaises(Rejected):
            scope(self.catalog, self.request["scope"], operational=True)
        self.catalog["environments"]["demo-a"]["aws_account_id"] = "999999999999"
        with self.assertRaises(Rejected):
            scope(self.catalog, self.request["scope"])

    def test_source_and_full_commit_required(self):
        for change in [{"revision": "main"}, {"revision": "abcdef0"}, {"repository": INFRA_REPOSITORY}]:
            request = copy.deepcopy(self.request)
            request["applications"]["auth"].update(change)
            with self.subTest(change=change), self.assertRaises(Rejected):
                plan(request, self.catalog)

    def test_source_checkout_revision_cleanliness_and_remote(self):
        good = [b"https://github.com/" + REPOSITORIES["auth"].encode() + b".git\n", (REVISION + "\n").encode(), b"", b"archive"]
        for index, invalid in [(0, b"https://github.com/foreign/repo.git"), (1, b"b" * 40), (2, b" M pom.xml")]:
            responses = good.copy()
            responses[index] = invalid
            with patch("release.command", side_effect=responses), self.assertRaises(Rejected):
                provenance(self.folder, REPOSITORIES["auth"], REVISION)

    def test_maven_reports_fail_closed(self):
        reports(self.folder)
        self.assertEqual(maven_evidence(self.folder / "source")["tests"], 10)
        report = self.folder / "source/target/surefire-reports/TEST-fixture.xml"
        for field in ["failures", "errors", "skipped"]:
            report.write_text(f'<testsuite tests="10" {field}="1"/>')
            with self.assertRaises(Rejected):
                maven_evidence(self.folder / "source")
        report.write_text('<testsuite tests="10"/>')
        (self.folder / "source/target/site/jacoco/jacoco.xml").write_text('<report><counter type="LINE" covered="79" missed="21"/></report>')
        with self.assertRaises(Rejected):
            maven_evidence(self.folder / "source")

    def test_jar_checks_format_java_handler_harness_and_secrets(self):
        path = self.folder / "auth.jar"
        path.write_bytes(jar_bytes())
        verify_jar(path)
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("org/junit/Test.class", b"fixture")
        with self.assertRaises(Rejected):
            verify_jar(path)
        for bad in [b"not-a-zip", b""]:
            path.write_bytes(bad)
            with self.assertRaises((Rejected, zipfile.BadZipFile)):
                verify_jar(path)

    def test_missing_file_and_checksum_changes(self):
        self.build_fixtures()
        path = self.folder / "auth/application.jar"
        path.write_bytes(b"tampered")
        with self.assertRaises((Rejected, FileNotFoundError)):
            validate_manifest(self.manifest, self.catalog, self.folder)
        path.unlink()
        with self.assertRaises((Rejected, FileNotFoundError)):
            validate_manifest(self.manifest, self.catalog, self.folder)

    def test_receipt_and_manifest_tampering(self):
        fingerprint = validate_manifest(self.manifest, self.catalog)
        changed = copy.deepcopy(self.manifest)
        changed["created_at"] = "changed"
        with self.assertRaises(Rejected):
            validate_manifest(changed, self.catalog, expected_sha256=fingerprint)
        self.build_fixtures()
        (self.folder / "auth/receipt.json").write_text("{}")
        with self.assertRaises(Rejected):
            validate_manifest(self.manifest, self.catalog, self.folder)
        for field, value in [("state", "published"), ("remote", {"verified": True}), ("platform", "linux/arm64")]:
            manifest = plan(self.request, self.catalog)
            manifest["artifacts"]["auth"][field] = value
            with self.assertRaises(Rejected):
                validate_manifest(manifest, self.catalog)

    def test_source_archive_changed_after_build(self):
        self.build_fixtures()
        (self.folder / "auth/source.tar").write_bytes(b"different commit")
        with self.assertRaises(Rejected):
            validate_manifest(self.manifest, self.catalog, self.folder)

    def test_image_platform_source_revision_and_user(self):
        config = {"Os": "linux", "Architecture": "amd64", "Id": "sha256:" + "1" * 64,
                  "Config": {"User": "10001", "Labels": {"org.opencontainers.image.revision": REVISION,
                            "org.opencontainers.image.source": "https://github.com/" + REPOSITORIES["os"]}}}
        image_evidence(config, "os", REVISION)
        for field, value in [("Architecture", "arm64"), ("Os", "windows")]:
            with patch.dict(config, {field: value}), self.assertRaises(Rejected):
                image_evidence(config, "os", REVISION)
        with patch.dict(config["Config"], {"User": "root"}), self.assertRaises(Rejected):
            image_evidence(config, "os", REVISION)
        config["Config"]["Labels"]["org.opencontainers.image.revision"] = "b" * 40
        with self.assertRaises(Rejected):
            image_evidence(config, "os", REVISION)

    def test_stale_target_and_root_dockerfiles_rejected(self):
        dockerfile = "FROM java:21 AS build\nRUN mvn package\nFROM java:21\nCOPY --from=build /target/app.jar /app.jar\nUSER app\n"
        dockerfile_contract(dockerfile, "os")
        for change in [dockerfile.replace("--from=build ", ""), dockerfile.replace("USER app", "USER root"), dockerfile + "COPY . .\n"]:
            with self.assertRaises(Rejected):
                dockerfile_contract(change, "os")

    def test_fixture_layer_cannot_become_approved(self):
        path = self.folder / "layer.zip"
        wrapper = (ROOT / "phase4/stacks/edge/lambda-bootstrap/bin/auth-bootstrap").read_bytes().replace(b"\r\n", b"\n")
        with zipfile.ZipFile(path, "w") as archive:
            for name, data, mode in [("bin/auth-bootstrap", wrapper, 0o755), ("java/lib/auth-bootstrap.jar", jar_bytes(True), 0o644),
                                     ("auth-home/.postgresql/root.crt", b"FIXTURE_NOT_AN_RDS_CERTIFICATE\n", 0o644)]:
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(entry, data)
        self.assertTrue(verify_layer(path, {}, fixture=True)["fixture"])
        with self.assertRaisesRegex(Rejected, "APPROVED_RDS_CA_REQUIRED"):
            verify_layer(path, {})
        self.manifest["artifacts"]["auth-layer"].update(state="built_tested", local={"fixture": False})
        with self.assertRaises(Rejected):
            validate_manifest(self.manifest, self.catalog, self.folder)

    def test_secret_detection_in_package_and_manifest(self):
        markers = [(".env", b"anything"), ("key.txt", ("-----BEGIN " + "PRIVATE KEY-----").encode()),
                   ("token.txt", ("AKIA" + "Z" * 16).encode()), ("data.json", b'{"password":"synthetic_value_only"}')]
        for name, content in markers:
            with self.subTest(name=name), self.assertRaises(Rejected):
                scan(name, content)
        self.manifest["password"] = "synthetic_value_only"
        with self.assertRaises(Rejected):
            validate_manifest(self.manifest, self.catalog)

    def test_compiled_parser_marker_is_not_private_key_material(self):
        marker = ("-----BEGIN " + "PRIVATE KEY-----").encode()
        scan("Parser.class", b"\xca\xfe\xba\xbe" + marker)
        with self.assertRaises(Rejected):
            scan("Parser.class", b"\xca\xfe\xba\xbe" + marker + b"\n" + b"A" * 64)

    def test_simulation_never_promotes_fixtures_or_remote_state(self):
        clients, observed = self.remote_fixture()
        evidence = self.simulate(clients, observed)
        self.assertFalse(evidence["publish_allowed"])
        self.assertFalse(evidence["deploy_allowed"])
        self.assertTrue(all(artifact["remote"] is None for artifact in self.manifest["artifacts"].values()))
        with self.assertRaises(Rejected):
            OfflinePublisher().publish(authorized=True)

    def test_foreign_ecr_and_immutable_tag_different_content(self):
        clients, observed = self.remote_fixture()
        clients.ecr.describe_repositories.side_effect = None
        clients.ecr.describe_repositories.return_value = {"repositories": [{"registryId": ACCOUNT, "repositoryUri": f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-b-auth", "imageTagMutability": "IMMUTABLE"}]}
        with self.assertRaises(Rejected):
            self.simulate(clients, observed)
        clients.ecr.describe_repositories.side_effect = lambda repositoryNames: {"repositories": [{"registryId": ACCOUNT, "repositoryUri": f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/{repositoryNames[0]}", "imageTagMutability": "IMMUTABLE"}]}
        foreign = '{"config":{"digest":"sha256:' + 'f' * 64 + '"}}'
        clients.ecr.describe_images.side_effect = lambda repositoryName, imageIds: {"imageDetails": [{
            "registryId": ACCOUNT, "repositoryName": repositoryName, "imageTags": [REVISION], "imageDigest": "sha256:" + digest(foreign.encode())}]}
        clients.ecr.batch_get_image.side_effect = lambda **arguments: {"images": [{"imageManifest": foreign}]}
        with self.assertRaisesRegex(Rejected, "IMMUTABLE_TAG_CONTENT_MISMATCH"):
            self.simulate(clients, observed)

    def test_s3_foreign_owner_missing_version_and_layer_arn(self):
        clients, observed = self.remote_fixture()
        clients.s3.get_bucket_location.side_effect = Rejected("FOREIGN_BUCKET_OWNER")
        with self.assertRaises(Rejected):
            self.simulate(clients, observed)
        clients.s3.get_bucket_location.side_effect = None
        clients.s3.get_object.side_effect = lambda **arguments: {"VersionId": "null", "Body": io.BytesIO(jar_bytes())}
        with self.assertRaises(Rejected):
            self.simulate(clients, observed)
        clients.s3.get_object.side_effect = lambda **arguments: {"VersionId": "fixture", "Body": io.BytesIO(jar_bytes())}
        clients.functions.get_layer_version.return_value["LayerVersionArn"] = f"arn:aws:lambda:us-east-1:{ACCOUNT}:layer:foreign:7"
        with self.assertRaises(Rejected):
            self.simulate(clients, observed)
        clients.functions.get_layer_version.return_value["LayerVersionArn"] = f"arn:aws:lambda:us-east-1:{ACCOUNT}:layer:tc-oficina-p4-demo-a-auth-bootstrap:8"
        with self.assertRaisesRegex(Rejected, "LAYER_ARN_REJECTED"):
            self.simulate(clients, observed)

    def test_observed_account_region_and_jwt_mismatch(self):
        clients, observed = self.remote_fixture()
        for change in [{"account": "999999999999"}, {"region": "us-west-2"}, {"arn": "foreign"}]:
            with self.subTest(change=change), self.assertRaises(Rejected):
                self.simulate(clients, {**observed, **change})
        for change in [{"issuer": "https://foreign"}, {"audience": "foreign"}]:
            with self.assertRaises(Rejected):
                jwt_contract(self.jwt, {**self.jwt, **change}, "tc-oficina-p4-demo-a")

    def test_manifest_changed_during_readback(self):
        clients, observed = self.remote_fixture()
        original = clients.functions.get_layer_version.return_value
        def mutate(**arguments):
            self.manifest["created_at"] = "modified"
            return original
        clients.functions.get_layer_version.side_effect = mutate
        with self.assertRaisesRegex(Rejected, "MANIFEST_CHANGED_DURING_VERIFICATION"):
            self.simulate(clients, observed)


if __name__ == "__main__":
    unittest.main()
