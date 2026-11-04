import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[2]
INFRA_REPOSITORY = "AuroNet0/tech-challenge-oficina-k8s-infra"
REPOSITORIES = {name: "AuroNet0/tech-challenge-oficina-" + suffix for name, suffix in {
    "auth": "auth", "os": "os-service", "execution": "execution-service", "billing": "billing-service"}.items()}
ARTIFACTS = {**{name: ("image", name) for name in REPOSITORIES}, "auth-jar": ("jar", "auth"), "auth-layer": ("layer", "infra")}
HANDLER = "br/com/fiap/oficina/auth/handler/AuthHandler.class"
INTERNAL = "br/com/fiap/oficina/auth/infrastructure/internal/InternalAuthMain"
sys.path.insert(0, str(ROOT / "scripts/platform-secrets"))
from check_secret_files import unsafe_file


class Rejected(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def revision(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "FULL_REVISION_REQUIRED")
    return value


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def scan(name, content):
    scanned = content
    if name.endswith(".class") and content.startswith(b"\xca\xfe\xba\xbe"):
        require(not re.search(rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----\s*[A-Za-z0-9+/=\r\n]{32,}', content), "PRIVATE_KEY_PAYLOAD_REJECTED")
        scanned = re.sub(rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----', b'JAVA_KEY_TYPE_MARKER', content)
    require(not unsafe_file(name, scanned), "SENSITIVE_ARTIFACT_REJECTED")
    if Path(name).suffix.lower() in {".json", ".yaml", ".yml", ".properties", ".conf", ".ini", ".txt"}:
        require(not re.search(rb'(?im)(?:^|[,{])\s*["\']?(?:aws_secret_access_key|aws_session_token|password|private_key)["\']?\s*[:=]\s*["\']?[^\s"\'${}]{8,}', content), "SECRET_VALUE_REJECTED")


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def write_json(path, document):
    content = canonical(document)
    scan("manifest.json", content)
    with Path(path).open("xb") as output:
        output.write(content + b"\n")


def scope(catalog, requested, operational=False):
    require(set(requested) == {"environment_id", "account", "region"}, "SCOPE_FIELDS_REJECTED")
    environment = requested["environment_id"]
    require(environment in {"demo-a", "demo-b"} and environment in catalog["environments"], "ENVIRONMENT_REJECTED")
    settings = catalog["environments"][environment]
    require(re.fullmatch(r"[0-9]{12}", requested["account"]), "ACCOUNT_REJECTED")
    require(requested["region"] == settings["aws_region"], "REGION_REJECTED")
    require(settings["aws_account_id"] in {None, requested["account"]}, "ACCOUNT_REJECTED")
    if operational:
        require(settings["enabled"] is True and settings["aws_account_id"] == requested["account"], "ENVIRONMENT_DISABLED")
    return "tc-oficina-p4-" + environment


def plan(request, catalog):
    require(set(request) == {"scope", "infra_revision", "applications"}, "REQUEST_FIELDS_REJECTED")
    scope(catalog, request["scope"])
    revision(request["infra_revision"])
    require(set(request["applications"]) == set(REPOSITORIES), "APPLICATION_SET_REJECTED")
    for name, source in request["applications"].items():
        require(set(source) == {"repository", "revision"} and source["repository"] == REPOSITORIES[name], "SOURCE_REJECTED")
        revision(source["revision"])
    artifacts = {}
    for name, (kind, owner) in ARTIFACTS.items():
        source = {"repository": INFRA_REPOSITORY, "revision": request["infra_revision"]} if owner == "infra" else request["applications"][owner]
        artifacts[name] = {"kind": kind, "source": dict(source), "platform": "linux/amd64", "java": 21,
                           "state": "planned", "local": None, "remote": None}
    return {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": dict(request["scope"]), "infra": {"repository": INFRA_REPOSITORY, "revision": request["infra_revision"]},
            "applications": request["applications"], "artifacts": artifacts}


def command(arguments, cwd=None, environment=None):
    result = subprocess.run(arguments, cwd=cwd, env=environment, capture_output=True, timeout=1200)
    if result.returncode and (b"offline mode" in result.stdout or b"has not been downloaded" in result.stdout):
        raise Rejected("OFFLINE_DEPENDENCY_UNAVAILABLE")
    require(result.returncode == 0, "LOCAL_COMMAND_FAILED")
    return result.stdout


def local_environment(output):
    environment = {key: value for key, value in os.environ.items() if key in {
        "PATH", "JAVA_HOME", "HOME", "USERPROFILE", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP"}}
    output = Path(output)
    docker_config = output / "docker-config"
    docker_config.mkdir()
    environment["DOCKER_CONFIG"] = str(docker_config)
    environment["AWS_EC2_METADATA_DISABLED"] = "true"
    for variable, filename in [("AWS_SHARED_CREDENTIALS_FILE", "empty-aws-credentials"), ("AWS_CONFIG_FILE", "empty-aws-config")]:
        path = output / filename
        path.write_bytes(b"")
        environment[variable] = str(path)
    return environment


def provenance(path, repository, expected_revision, git="git", fixture=False):
    path = Path(path).resolve()
    revision(expected_revision)
    prefix = [git, "-c", f"safe.directory={path.as_posix()}", "-C", str(path)]
    remote = command(prefix + ["remote", "get-url", "origin"]).decode().strip()
    require(remote in {f"https://github.com/{repository}.git", f"https://github.com/{repository}", f"git@github.com:{repository}.git"}, "SOURCE_REJECTED")
    require(command(prefix + ["rev-parse", "HEAD"]).decode().strip() == expected_revision, "REVISION_MISMATCH")
    require(fixture or not command(prefix + ["status", "--porcelain", "--untracked-files=all"]), "DIRTY_SOURCE")
    return command(prefix + ["archive", "--format=tar", expected_revision])


def snapshot(archive, destination):
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source.getmembers():
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts and not member.issym() and not member.islnk(), "UNSAFE_SOURCE_ARCHIVE")
            require(member.isdir() or member.isfile(), "UNSAFE_SOURCE_ARCHIVE")
            if member.isfile():
                target = Path(destination) / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(member).read())
                target.chmod(member.mode & 0o777)


def maven_evidence(workspace, pending_internal_image=False):
    tests = 0
    pending = False
    for folder in ["surefire-reports", "failsafe-reports"]:
        for report in (Path(workspace) / "target" / folder).glob("TEST-*.xml"):
            root = ET.parse(report).getroot()
            require(root.tag == "testsuite", "TEST_REPORT_REJECTED")
            tests += int(root.get("tests", "0"))
            require(all(int(root.get(field, "0")) == 0 for field in ["failures", "errors"]), "TESTS_NOT_APPROVED")
            skipped = int(root.get("skipped", "0"))
            if skipped:
                require(pending_internal_image and root.get("name") == "br.com.fiap.oficina.auth.infrastructure.internal.InternalContainerIT"
                        and skipped == int(root.get("tests", "0")) == 1, "TESTS_NOT_APPROVED")
                pending = True
    require(tests > 0, "TEST_REPORT_MISSING")
    report = Path(workspace) / "target/site/jacoco/jacoco.xml"
    require(report.is_file(), "COVERAGE_MISSING")
    counters = [counter for counter in ET.parse(report).getroot().findall("counter") if counter.get("type") == "LINE"]
    require(len(counters) == 1, "COVERAGE_REJECTED")
    covered, missed = (int(counters[0].get(field)) for field in ["covered", "missed"])
    require(covered + missed > 0 and covered / (covered + missed) >= 0.8, "COVERAGE_BELOW_80")
    return {"tests": tests, "line_covered": covered, "line_missed": missed, "pending_container_test": pending,
            "command": "mvn -o -B -ntp clean verify"}


def zip_contents(path, budget=None):
    content = path if isinstance(path, bytes) else Path(path).read_bytes()
    require(0 < len(content) <= 250 * 1024 * 1024, "PACKAGE_SIZE_REJECTED")
    budget = [250 * 1024 * 1024] if budget is None else budget
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "DUPLICATE_ARCHIVE_ENTRY")
        budget[0] -= sum(item.file_size for item in archive.infolist())
        require(budget[0] >= 0, "UNPACKED_SIZE_REJECTED")
        contents = {}
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            require(not name.is_absolute() and ".." not in name.parts and "\\" not in item.filename, "UNSAFE_PACKAGE_PATH")
            require(not stat.S_ISLNK(item.external_attr >> 16), "UNSAFE_PACKAGE_PATH")
            if not item.is_dir():
                content = archive.read(item)
                scan(item.filename, content)
                if item.filename.endswith(".jar"):
                    zip_contents(content, budget)
                contents[item.filename] = (content, item.external_attr >> 16)
        return contents


def verify_jar(path, helper=False):
    contents = zip_contents(path)
    expected = "oficina/edge/AuthDatabaseBootstrap.class" if helper else HANDLER
    require(expected in contents, "JAVA_ENTRYPOINT_MISSING")
    class_bytes = contents[expected][0]
    require(class_bytes[:4] == b"\xca\xfe\xba\xbe" and int.from_bytes(class_bytes[6:8], "big") == 65, "JAVA_21_REQUIRED")
    require("software/amazon/awssdk/services/secretsmanager/SecretsManagerClient.class" in contents, "SHADED_DEPENDENCY_MISSING")
    if not helper:
        require(not any(re.search(r"(^|/)(?:integration|harness|testcontainers|junit)(/|\.)", name, re.I) for name in contents), "HARNESS_IN_PRODUCTION_JAR")
        require(INTERNAL + ".class" in contents, "INTERNAL_ENTRYPOINT_MISSING")
    return file_evidence(path)


def file_evidence(path):
    path = Path(path)
    require(path.is_file(), "ARTIFACT_MISSING")
    data = path.read_bytes()
    return {"file": path.name, "size": len(data), "sha256": digest(data)}


def verify_receipt_source(receipt, folder):
    archive = (Path(folder) / "source.tar").read_bytes()
    require(digest(archive) == receipt["source_archive_sha256"], "SOURCE_ARCHIVE_CHANGED")
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        require(source.pax_headers.get("comment") == receipt["source"]["revision"], "SOURCE_ARCHIVE_REVISION_MISMATCH")
        for member in source:
            if member.isfile() and (member.name.startswith("src/") or member.name in {"pom.xml", "Dockerfile", ".dockerignore", "docker-entrypoint.sh"}):
                path = (Path(folder) / "source" / member.name).resolve()
                require(path.is_relative_to((Path(folder) / "source").resolve()) and path.is_file()
                        and path.read_bytes() == source.extractfile(member).read(), "BUILD_SOURCE_CHANGED")


def dockerfile_contract(content, service):
    require(not re.search(r"(?im)^\s*(?:COPY|ADD)\s+(?!.*--from=).*\btarget/", content), "STALE_TARGET_COPY")
    require(not re.search(r"(?im)^\s*(?:COPY|ADD)\s+\.\s", content), "BROAD_DOCKER_CONTEXT")
    stages = re.split(r"(?im)^FROM\s+", content)[1:]
    require(len(stages) >= 2 and all("21" in stage.splitlines()[0] for stage in stages), "JAVA_21_REQUIRED")
    require(re.search(r"(?im)^USER\s+(?!root\b|0(?:\s|:|$))\S+", stages[-1]), "NONROOT_REQUIRED")
    require("--from=build" in stages[-1] and "package" in content, "FRESH_MULTISTAGE_BUILD_REQUIRED")
    if service == "auth":
        require(INTERNAL.replace("/", ".") in stages[-1], "AUTH_ENTRYPOINT_CHANGED")
    return [stage.splitlines()[0].split()[0] for stage in stages]


def image_evidence(inspected, service, source_revision):
    require(inspected["Os"] == "linux" and inspected["Architecture"] == "amd64", "PLATFORM_REJECTED")
    config = inspected["Config"]
    require(config.get("User") and config["User"].split(":")[0] not in {"0", "root"}, "NONROOT_REQUIRED")
    require(config.get("Labels", {}).get("org.opencontainers.image.revision") == source_revision, "IMAGE_REVISION_MISMATCH")
    require(config.get("Labels", {}).get("org.opencontainers.image.source") == "https://github.com/" + REPOSITORIES[service], "IMAGE_SOURCE_MISMATCH")
    for value in config.get("Env", []):
        require(not re.match(r"(?i)(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|.*PASSWORD|.*TOKEN|.*PRIVATE_KEY)=.+", value), "IMAGE_SECRET_ENV")
    if service == "auth":
        require(config["Entrypoint"] == ["java", "-cp", "/app/app.jar", INTERNAL.replace("/", ".")], "AUTH_ENTRYPOINT_CHANGED")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", inspected["Id"]), "IMAGE_ID_REJECTED")
    return {"image_id": inspected["Id"], "platform": "linux/amd64"}


def build_app(service, repository_path, source_revision, output, git="git", maven="mvn", java="java", docker="docker"):
    require(service in REPOSITORIES, "SERVICE_REJECTED")
    output = Path(output).resolve()
    require(not output.exists(), "OUTPUT_EXISTS")
    archive = provenance(repository_path, REPOSITORIES[service], source_revision, git)
    output.mkdir(parents=True)
    (output / "source.tar").write_bytes(archive)
    workspace = output / "source"
    workspace.mkdir()
    snapshot(archive, workspace)
    dockerfile = (workspace / "Dockerfile").read_text()
    bases = dockerfile_contract(dockerfile, service)
    environment = local_environment(output)
    java_version = subprocess.run([java, "-version"], env=environment, capture_output=True, timeout=30)
    require(java_version.returncode == 0 and re.search(rb'version "21[.\"]', java_version.stderr + java_version.stdout), "JAVA_21_REQUIRED")
    settings = output / "settings.xml"
    settings.write_text('<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"/>', encoding="utf-8")
    command([maven, "-o", "-B", "-ntp", "-s", str(settings), "-gs", str(settings), "clean", "verify"], workspace, environment)
    quality = maven_evidence(workspace, pending_internal_image=service == "auth")
    candidates = [path for path in (workspace / "target").glob("*.jar") if not path.name.startswith("original-")]
    require(len(candidates) == 1, "MAVEN_ARTIFACT_AMBIGUOUS")
    if service == "auth":
        require(candidates[0].name == "tech-challenge-oficina-auth.jar", "AUTH_JAR_NAME_CHANGED")
        verify_jar(candidates[0])
    else:
        zip_contents(candidates[0])
    shutil.copyfile(candidates[0], output / "application.jar")
    receipt = {"schema_version": 1, "service": service,
               "source": {"repository": REPOSITORIES[service], "revision": source_revision},
               "source_archive_sha256": digest(archive), "maven": quality,
               "jar": file_evidence(output / "application.jar"), "image": None, "remote": None}
    write_json(output / "maven-receipt.json", receipt)
    for image in bases:
        inspected_base = json.loads(command([docker, "image", "inspect", image], environment=environment))[0]
        receipt.setdefault("docker_bases", []).append({"reference": image, "image_id": inspected_base["Id"]})
    tag = f"oficina-release-{service}:{source_revision}"
    command([docker, "build", "--network=none", "--pull=false", "--platform=linux/amd64",
             "--label", f"org.opencontainers.image.revision={source_revision}",
             "--label", f"org.opencontainers.image.source=https://github.com/{REPOSITORIES[service]}", "-t", tag, "."], workspace, environment)
    inspected = json.loads(command([docker, "image", "inspect", tag], environment=environment))[0]
    receipt["image"] = image_evidence(inspected, service, source_revision)
    if quality["pending_container_test"]:
        command([maven, "-o", "-B", "-ntp", "-s", str(settings), "-gs", str(settings),
                 f"-Dauth.internal.image={tag}", "verify"], workspace, environment)
        receipt["maven"] = maven_evidence(workspace)
    command([docker, "save", "-o", str(output / "image.tar"), tag], environment=environment)
    receipt["image"].update(file_evidence(output / "image.tar"))
    verify_image_archive(output / "image.tar", service, source_revision, receipt["image"]["image_id"])
    write_json(output / "receipt.json", receipt)
    return receipt


def verify_image_archive(path, service, source_revision, image_id):
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        require(all(member.isfile() or member.isdir() for member in members), "UNSAFE_IMAGE_ARCHIVE")
        require(len({member.name for member in members}) == len(members), "DUPLICATE_ARCHIVE_ENTRY")
        manifests = json.load(archive.extractfile("manifest.json"))
        require(len(manifests) == 1, "IMAGE_ARCHIVE_AMBIGUOUS")
        config_bytes = archive.extractfile(manifests[0]["Config"]).read()
        require("sha256:" + digest(config_bytes) == image_id, "IMAGE_ARCHIVE_CONFIG_MISMATCH")
        configuration = json.loads(config_bytes)
        require(configuration.get("rootfs", {}).get("type") == "layers"
                and len(configuration["rootfs"].get("diff_ids", [])) == len(manifests[0]["Layers"]), "IMAGE_ROOTFS_REJECTED")
        image_evidence({"Id": image_id, "Os": configuration["os"], "Architecture": configuration["architecture"],
                        "Config": configuration["config"]}, service, source_revision)
        for index, layer in enumerate(manifests[0]["Layers"]):
            layer_bytes = archive.extractfile(layer).read()
            require("sha256:" + digest(layer_bytes) == configuration["rootfs"]["diff_ids"][index], "IMAGE_LAYER_CHECKSUM_MISMATCH")
            with tarfile.open(fileobj=io.BytesIO(layer_bytes)) as layer_archive:
                for member in layer_archive:
                    if member.isfile():
                        require(member.size <= 250 * 1024 * 1024, "IMAGE_MEMBER_TOO_LARGE")
                        content = layer_archive.extractfile(member).read()
                        scan(member.name, content)
                        if member.name.endswith(".jar"):
                            zip_contents(content)


def verify_layer(path, ca_policy, fixture=False):
    contents = zip_contents(path)
    require(set(contents) == {"bin/auth-bootstrap", "java/lib/auth-bootstrap.jar", "auth-home/.postgresql/root.crt"}, "LAYER_LAYOUT_REJECTED")
    wrapper, permissions = contents["bin/auth-bootstrap"]
    require(permissions & 0o111 and b"\r" not in wrapper and wrapper.startswith(b"#!/bin/bash\n"), "LAYER_EXECUTABLE_REJECTED")
    require(wrapper == (ROOT / "phase4/stacks/edge/lambda-bootstrap/bin/auth-bootstrap").read_bytes().replace(b"\r\n", b"\n"), "WRAPPER_SOURCE_MISMATCH")
    ca = contents["auth-home/.postgresql/root.crt"][0]
    if fixture:
        require(ca == b"FIXTURE_NOT_AN_RDS_CERTIFICATE\n", "FIXTURE_CA_REJECTED")
    else:
        entry = ca_policy.get(digest(ca))
        require(entry and entry["region"] == "us-east-1" and entry["source"] == "https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem", "APPROVED_RDS_CA_REQUIRED")
        require(ca.startswith(b"-----BEGIN CERTIFICATE-----") and b"PRIVATE KEY" not in ca, "CA_FORMAT_REJECTED")
    with tempfile.TemporaryDirectory() as folder:
        jar = Path(folder) / "auth-bootstrap.jar"
        jar.write_bytes(contents["java/lib/auth-bootstrap.jar"][0])
        verify_jar(jar, helper=True)
    return {**file_evidence(path), "fixture": fixture, "ca_sha256": digest(ca)}


def build_layer(source_revision, ca_file, output, ca_policy, fixture=False, git="git", maven="mvn", java="java"):
    archive = provenance(ROOT, INFRA_REPOSITORY, source_revision, git, fixture=fixture)
    output = Path(output)
    require(not output.exists(), "OUTPUT_EXISTS")
    output.mkdir(parents=True)
    workspace = output / "source"
    workspace.mkdir()
    snapshot(archive, workspace)
    fixture_sources = None
    if fixture:
        bootstrap = Path("phase4/stacks/edge/lambda-bootstrap")
        fixture_sources = {}
        for source_path in sorted((ROOT / bootstrap).rglob("*")):
            relative = source_path.relative_to(ROOT)
            if source_path.is_file() and "target" not in relative.parts:
                require(not source_path.is_symlink(), "UNSAFE_SOURCE_ARCHIVE")
                destination = workspace / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source_path.read_bytes())
                fixture_sources[relative.as_posix()] = digest(source_path.read_bytes())
    environment = local_environment(output)
    java_version = subprocess.run([java, "-version"], env=environment, capture_output=True, timeout=30)
    require(java_version.returncode == 0 and re.search(rb'version "21[.\"]', java_version.stderr + java_version.stdout), "JAVA_21_REQUIRED")
    settings = output / "settings.xml"
    settings.write_text('<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"/>', encoding="utf-8")
    command([maven, "-o", "-B", "-ntp", "-s", str(settings), "-gs", str(settings), "clean", "package"],
            workspace / "phase4/stacks/edge/lambda-bootstrap", environment)
    helper_jar = workspace / "phase4/stacks/edge/lambda-bootstrap/target/auth-bootstrap.jar"
    verify_jar(helper_jar, helper=True)
    wrapper = (ROOT / "phase4/stacks/edge/lambda-bootstrap/bin/auth-bootstrap").read_bytes().replace(b"\r\n", b"\n")
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        approved_wrapper = source.extractfile("phase4/stacks/edge/lambda-bootstrap/bin/auth-bootstrap").read().replace(b"\r\n", b"\n")
    require(wrapper == approved_wrapper, "WRAPPER_SOURCE_MISMATCH")
    with zipfile.ZipFile(output / "layer.zip", "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content, mode in [("bin/auth-bootstrap", wrapper, 0o755),
                                     ("java/lib/auth-bootstrap.jar", Path(helper_jar).read_bytes(), 0o644),
                                     ("auth-home/.postgresql/root.crt", Path(ca_file).read_bytes(), 0o644)]:
            entry = zipfile.ZipInfo(name, (2026, 11, 2, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | mode) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(entry, content)
    evidence = verify_layer(output / "layer.zip", ca_policy, fixture)
    receipt = {"source": {"repository": INFRA_REPOSITORY, "revision": source_revision}, "layer": evidence,
               "source_archive_sha256": digest(archive), "helper_sha256": digest(helper_jar.read_bytes()),
               "uncommitted_fixture_sources": fixture_sources,
               "state": "fixture_only" if fixture else "locally_packaged_not_runtime_verified", "remote": None}
    write_json(output / "receipt.json", receipt)
    return receipt


def validate_manifest(manifest, catalog, files_root=None, expected_sha256=None):
    if expected_sha256 is not None:
        require(digest(canonical(manifest)) == expected_sha256, "MANIFEST_CHECKSUM_MISMATCH")
    scan("manifest.json", canonical(manifest))
    require(set(manifest) == {"schema_version", "created_at", "scope", "infra", "applications", "artifacts"}, "MANIFEST_FIELDS_REJECTED")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
            and manifest["infra"]["repository"] == INFRA_REPOSITORY, "FORMAT_REJECTED")
    timestamp = datetime.fromisoformat(manifest["created_at"])
    require(timestamp.utcoffset() is not None and timestamp.utcoffset().total_seconds() == 0, "UTC_TIMESTAMP_REQUIRED")
    expected = plan({"scope": manifest["scope"], "infra_revision": manifest["infra"]["revision"], "applications": manifest["applications"]}, catalog)
    require(set(manifest["artifacts"]) == set(ARTIFACTS), "ARTIFACT_SET_REJECTED")
    for name, artifact in manifest["artifacts"].items():
        require(set(artifact) == set(expected["artifacts"][name]), "ARTIFACT_FIELDS_REJECTED")
        require(all(artifact[field] == expected["artifacts"][name][field] for field in ["source", "kind", "platform", "java"]), "ARTIFACT_IDENTITY_REJECTED")
        require(artifact["remote"] is None and artifact["state"] in {"planned", "built_tested"}, "REMOTE_STATE_UNAVAILABLE_OFFLINE")
        if artifact["state"] == "planned":
            require(artifact["local"] is None, "PLANNED_ARTIFACT_HAS_EVIDENCE")
        else:
            require(files_root is not None and name != "auth-layer", "RUNTIME_VALIDATION_REQUIRED")
            local = artifact["local"]
            require(set(local) == {"file", "size", "sha256", "receipt", "receipt_sha256"}, "LOCAL_FIELDS_REJECTED")
            root = Path(files_root).resolve()
            artifact_path = (root / local["file"]).resolve()
            receipt_path = (root / local["receipt"]).resolve()
            require(artifact_path.is_relative_to(root) and receipt_path.is_relative_to(root), "PATH_REJECTED")
            require(digest(receipt_path.read_bytes()) == local["receipt_sha256"], "RECEIPT_CHANGED")
            receipt = read_json(receipt_path)
            require(receipt["source"] == artifact["source"] and receipt["remote"] is None, "SOURCE_REJECTED")
            verify_receipt_source(receipt, receipt_path.parent)
            require(file_evidence(artifact_path) == {"file": artifact_path.name, "size": local["size"], "sha256": local["sha256"]}, "CHECKSUM_MISMATCH")
            maven_evidence(receipt_path.parent / "source")
            if name == "auth-jar":
                verify_jar(artifact_path)
            else:
                verify_image_archive(artifact_path, name, artifact["source"]["revision"], receipt["image"]["image_id"])
    return digest(canonical(manifest))


def attach_build(manifest, service, receipt_folder, files_root, catalog):
    receipt_folder, files_root = Path(receipt_folder).resolve(), Path(files_root).resolve()
    require(receipt_folder.is_relative_to(files_root), "RECEIPT_OUTSIDE_RELEASE")
    receipt_path = receipt_folder / "receipt.json"
    if not receipt_path.is_file():
        receipt_path = receipt_folder / "maven-receipt.json"
    receipt = read_json(receipt_path)
    require(receipt["service"] == service and receipt["source"] == manifest["applications"][service], "SOURCE_REJECTED")
    for name, evidence in [(service, receipt["image"]), ("auth-jar", receipt["jar"] if service == "auth" else None)]:
        if evidence is not None:
            manifest["artifacts"][name].update(state="built_tested", local={
                "file": (receipt_folder / evidence["file"]).relative_to(files_root).as_posix(), "size": evidence["size"],
                "sha256": evidence["sha256"], "receipt": receipt_path.relative_to(files_root).as_posix(),
                "receipt_sha256": digest(receipt_path.read_bytes())})
    validate_manifest(manifest, catalog, files_root)
    return manifest


def jwt_contract(platform, edge, prefix):
    require(set(platform) == {"issuer", "audience", "jwks_uri"} and set(edge) == set(platform), "JWT_FIELDS_REJECTED")
    require(platform == edge and platform["audience"] == prefix, "JWT_CONTRACT_MISMATCH")
    require(re.fullmatch(r"https://[a-z0-9-]+\.execute-api\.us-east-1\.amazonaws\.com", platform["issuer"]), "JWT_ISSUER_REJECTED")
    require(platform["jwks_uri"] == platform["issuer"] + "/.well-known/jwks.json", "JWT_JWKS_REJECTED")


class OfflinePublisher:
    def publish(self, *arguments, **keywords):
        raise Rejected("OPERATIONAL_PUBLICATION_NOT_IMPLEMENTED")

    def simulate(self, manifest, catalog, clients, observed, platform_jwt, edge_jwt, files_root=None):
        fingerprint = validate_manifest(manifest, catalog, files_root)
        prefix = scope(catalog, manifest["scope"], operational=True)
        account, region = manifest["scope"]["account"], manifest["scope"]["region"]
        require(observed["account"] == account and observed["region"] == region, "OBSERVED_IDENTITY_MISMATCH")
        require(re.fullmatch(fr"arn:aws:sts::{account}:assumed-role/{prefix}-deploy/[A-Za-z0-9+=,.@_-]+", observed["arn"]), "OIDC_ROLE_REJECTED")
        jwt_contract(platform_jwt, edge_jwt, prefix)
        evidence = {}
        for name in REPOSITORIES:
            artifact = manifest["artifacts"][name]
            require(artifact["state"] == "built_tested", "BUILD_EVIDENCE_REQUIRED")
            repository = prefix + "-" + name
            destination = clients.ecr.describe_repositories(repositoryNames=[repository])["repositories"][0]
            require(destination["registryId"] == account and destination["repositoryUri"] == f"{account}.dkr.ecr.{region}.amazonaws.com/{repository}"
                    and destination["imageTagMutability"] == "IMMUTABLE", "ECR_DESTINATION_REJECTED")
            tag = artifact["source"]["revision"]
            image = clients.ecr.describe_images(repositoryName=repository, imageIds=[{"imageTag": tag}])["imageDetails"][0]
            require(image["registryId"] == account and image["repositoryName"] == repository and tag in image["imageTags"], "ECR_IMAGE_REJECTED")
            remote_digest = image["imageDigest"]
            require(re.fullmatch(r"sha256:[0-9a-f]{64}", remote_digest), "ECR_DIGEST_REJECTED")
            remote = clients.ecr.batch_get_image(repositoryName=repository, imageIds=[{"imageDigest": remote_digest}])
            require(not remote.get("failures") and len(remote["images"]) == 1, "ECR_READBACK_FAILED")
            image_manifest = remote["images"][0]["imageManifest"].encode()
            require("sha256:" + digest(image_manifest) == remote_digest, "ECR_MANIFEST_DIGEST_MISMATCH")
            require(json.loads(image_manifest).get("config", {}).get("digest") == read_json(Path(files_root) / artifact["local"]["receipt"])["image"]["image_id"], "IMMUTABLE_TAG_CONTENT_MISMATCH")
            evidence[name] = {"tag": tag, "observed_registry_digest": remote_digest}
        bucket = prefix + "-artifacts"
        require(clients.s3.get_bucket_location(Bucket=bucket, ExpectedBucketOwner=account)["LocationConstraint"] in {None, "us-east-1"}, "BUCKET_REGION_REJECTED")
        require(clients.s3.get_bucket_versioning(Bucket=bucket, ExpectedBucketOwner=account)["Status"] == "Enabled", "BUCKET_VERSIONING_REQUIRED")
        block = clients.s3.get_public_access_block(Bucket=bucket, ExpectedBucketOwner=account)["PublicAccessBlockConfiguration"]
        require(all(block.get(field) is True for field in ["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"]), "BUCKET_PUBLIC_ACCESS_REJECTED")
        jar = manifest["artifacts"]["auth-jar"]
        require(jar["state"] == "built_tested", "BUILD_EVIDENCE_REQUIRED")
        key = "auth/" + jar["source"]["revision"] + "/auth.jar"
        obj = clients.s3.get_object(Bucket=bucket, Key=key, ExpectedBucketOwner=account)
        require(obj.get("VersionId") and obj["VersionId"] != "null", "S3_VERSION_REQUIRED")
        require(digest(obj["Body"].read()) == jar["local"]["sha256"], "S3_CHECKSUM_MISMATCH")
        confirmed = clients.s3.get_object(Bucket=bucket, Key=key, VersionId=obj["VersionId"], ExpectedBucketOwner=account)
        require(confirmed.get("VersionId") == obj["VersionId"] and digest(confirmed["Body"].read()) == jar["local"]["sha256"], "S3_VERSION_READBACK_FAILED")
        require(type(clients.layer_version) is int and clients.layer_version > 0, "LAYER_VERSION_REJECTED")
        layer = clients.functions.get_layer_version(LayerName=prefix + "-auth-bootstrap", VersionNumber=clients.layer_version)
        require(layer["LayerVersionArn"] == f"arn:aws:lambda:{region}:{account}:layer:{prefix}-auth-bootstrap:{clients.layer_version}", "LAYER_ARN_REJECTED")
        require(layer["CompatibleRuntimes"] == ["java21"] and layer["CompatibleArchitectures"] == ["x86_64"], "LAYER_RUNTIME_REJECTED")
        require(digest(canonical(manifest)) == fingerprint, "MANIFEST_CHANGED_DURING_VERIFICATION")
        return {"mode": "simulation", "publish_allowed": False, "deploy_allowed": False, "manifest_sha256": fingerprint,
                "observations_not_publication_proof": evidence, "blockers": ["LIVE_READBACK_UNAVAILABLE", "LAYER_CA_AND_RUNTIME_PROOF_REQUIRED"]}


def main():
    parser = argparse.ArgumentParser(description="Offline artifact builds and release contracts; publication unavailable")
    subcommands = parser.add_subparsers(dest="operation", required=True)
    planned = subcommands.add_parser("plan")
    planned.add_argument("--request", required=True)
    planned.add_argument("--output", required=True)
    checked = subcommands.add_parser("verify")
    checked.add_argument("--manifest", required=True)
    checked.add_argument("--root", required=True)
    checked.add_argument("--expected-sha256")
    build = subcommands.add_parser("build-app")
    for name in ["service", "repository", "revision", "output"]:
        build.add_argument("--" + name, required=True)
    for name in ["git", "maven", "java", "docker"]:
        build.add_argument("--" + name, default="mvn" if name == "maven" else name)
    layer = subcommands.add_parser("layer")
    for name in ["revision", "ca", "output"]:
        layer.add_argument("--" + name, required=True)
    layer.add_argument("--git", default="git")
    layer.add_argument("--maven", default="mvn")
    layer.add_argument("--java", default="java")
    layer.add_argument("--fixture", action="store_true")
    attach = subcommands.add_parser("attach")
    for name in ["manifest", "service", "receipt", "root", "output"]:
        attach.add_argument("--" + name, required=True)
    subcommands.add_parser("publish")
    options = parser.parse_args()
    catalog = read_json(ROOT / "phase4/environments/catalog.json")
    if options.operation == "plan":
        write_json(options.output, plan(read_json(options.request), catalog))
    elif options.operation == "verify":
        print(validate_manifest(read_json(options.manifest), catalog, options.root, options.expected_sha256))
    elif options.operation == "build-app":
        build_app(options.service, options.repository, options.revision, options.output, options.git, options.maven, options.java, options.docker)
    elif options.operation == "layer":
        build_layer(options.revision, options.ca, options.output,
                    read_json(Path(__file__).with_name("rds-ca-policy.json")), options.fixture, options.git, options.maven, options.java)
    elif options.operation == "attach":
        write_json(options.output, attach_build(read_json(options.manifest), options.service, options.receipt, options.root, catalog))
    else:
        OfflinePublisher().publish()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, Rejected) else "ARTIFACT_OPERATION_FAILED", file=sys.stderr)
        sys.exit(1)
