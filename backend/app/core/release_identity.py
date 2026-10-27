"""Build and runtime code/dependency identities for API and RQ processes."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import importlib.metadata as metadata
import json
import platform
import re
import sysconfig
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import unquote, urlsplit

MANIFEST_VERSION = 1
_BUILD_REVISION_PATTERN = re.compile(r"^(?:unknown|[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FIXED_RUNTIME_FILES = ("pyproject.toml", "alembic.ini", "Dockerfile")
_REQUIRED_SINGLETON_METADATA_HEADERS = frozenset({"metadata-version", "name", "version"})
_OPTIONAL_SINGLETON_METADATA_HEADERS = frozenset({
    "summary", "description-content-type", "requires-python",
})


@dataclass(frozen=True)
class ReleaseIdentity:
    role: Literal["api", "worker"]
    build_revision: str
    manifest_revision: str
    manifest_status: Literal["ok", "unavailable", "invalid"]
    build_source_fingerprint: str | None
    runtime_source_fingerprint: str | None
    build_dependency_fingerprint: str | None
    runtime_dependency_fingerprint: str | None
    build_harness_fingerprint: str | None
    runtime_harness_fingerprint: str | None
    source_matches_build: bool
    dependencies_match_build: bool
    harness_matches_build: bool
    build_revision_matches_manifest: bool

    def to_safe_dict(self) -> dict[str, object]:
        """Return only nonsecret attestation data suitable for private logs."""
        return asdict(self)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalize_distribution_name(value: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", value.strip()).lower()
    if not normalized or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized):
        raise ValueError("invalid installed distribution name")
    return normalized


def _runtime_paths(root: Path) -> list[Path]:
    required = [root / name for name in _FIXED_RUNTIME_FILES]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f"required runtime manifest input missing: {path.name}")

    app_root = root / "app"
    migrations_root = root / "migrations"
    if not app_root.is_dir() or not migrations_root.is_dir():
        raise FileNotFoundError("required app or migrations source directory missing")

    paths = [*app_root.rglob("*.py"), *migrations_root.rglob("*.py"), *required]
    unique: dict[str, Path] = {}
    root_resolved = root.resolve(strict=True)
    for path in paths:
        if path.is_symlink():
            raise ValueError("runtime source manifest does not permit symlinks")
        resolved = path.resolve(strict=True)
        if root_resolved not in resolved.parents:
            raise ValueError("runtime source path escapes the application root")
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("runtime source path is not normalized")
        normalized = relative.as_posix()
        if normalized in unique and unique[normalized] != path:
            raise ValueError("duplicate normalized runtime source path")
        unique[normalized] = path
    return [unique[name] for name in sorted(unique)]


def _harness_paths(root: Path) -> list[Path]:
    harness_root = root / "tests" / "release_rehearsal"
    if not harness_root.exists():
        return []
    if not harness_root.is_dir():
        raise ValueError("release rehearsal harness path must be a directory")
    paths = sorted(harness_root.rglob("*.py"), key=lambda item: item.relative_to(root).as_posix())
    root_resolved = root.resolve(strict=True)
    for path in paths:
        if path.is_symlink() or root_resolved not in path.resolve(strict=True).parents:
            raise ValueError("release rehearsal harness contains an unsafe path")
    return paths


def _records(root: Path, paths: list[Path]) -> list[dict[str, str]]:
    records = []
    for path in paths:
        normalized = PurePosixPath(path.relative_to(root).as_posix()).as_posix()
        records.append({"path": normalized, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return records


def _metadata_signature(message: object) -> str:
    items = getattr(message, "items", None)
    if not callable(items):
        raise ValueError("distribution metadata is unreadable")
    headers = sorted(
        (str(key).strip().lower(), re.sub(r"\s+", " ", str(value)).strip())
        for key, value in items()
    )
    get_payload = getattr(message, "get_payload", None)
    payload = get_payload() if callable(get_payload) else ""
    if not isinstance(payload, str):
        raise ValueError("distribution metadata payload is unreadable")
    payload = payload.replace("\r\n", "\n").replace("\r", "\n").strip()
    return _fingerprint({"headers": headers, "payload": payload})


def _distribution_metadata_path(distribution: metadata.Distribution) -> Path:
    raw_path = getattr(distribution, "_path", None)
    if not isinstance(raw_path, str | Path):
        raise ValueError("distribution metadata location is unavailable")
    path = Path(raw_path)
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_dir() or not info:
            raise ValueError("distribution metadata location is unsafe")
        return path.resolve(strict=True)
    except OSError as error:
        raise ValueError("distribution metadata location is unavailable") from error


def _effective_site_packages() -> tuple[Path, ...]:
    paths = sysconfig.get_paths()
    result = []
    for key in ("purelib", "platlib"):
        value = paths.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError("effective Python site-packages path is unavailable")
        resolved = Path(value).resolve(strict=True)
        if resolved not in result:
            result.append(resolved)
    return tuple(result)


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _metadata_file(path: Path) -> Path:
    if path.name.endswith(".dist-info"):
        return path / "METADATA"
    if path.name.endswith(".egg-info"):
        return path / "PKG-INFO"
    raise ValueError("unexpected distribution metadata location")


def _read_distribution_metadata(path: Path) -> object:
    metadata_file = _metadata_file(path)
    if metadata_file.is_symlink() or not metadata_file.is_file():
        raise ValueError("distribution metadata file is unavailable")
    try:
        return email.parser.Parser().parsestr(metadata_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("distribution metadata file is unreadable") from error


def _validate_metadata_headers(message: object) -> None:
    get_all = getattr(message, "get_all", None)
    if not callable(get_all):
        raise ValueError("distribution metadata headers are unreadable")
    for name in _REQUIRED_SINGLETON_METADATA_HEADERS:
        if len(get_all(name, [])) != 1:
            raise ValueError("ambiguous distribution metadata headers")
    for name in _OPTIONAL_SINGLETON_METADATA_HEADERS:
        if len(get_all(name, [])) > 1:
            raise ValueError("ambiguous distribution metadata headers")


def _metadata_identity(distribution: metadata.Distribution, path: Path) -> tuple[str, str, str]:
    message = distribution.metadata
    raw_name = message.get("Name")
    version = distribution.version
    metadata_version = message.get("Version")
    if (not isinstance(raw_name, str) or not raw_name.strip() or not isinstance(version, str)
            or not version or metadata_version != version):
        raise ValueError("installed distribution metadata is incomplete or contradictory")
    normalized_name = _normalize_distribution_name(raw_name)
    disk_metadata = _read_distribution_metadata(path)
    _validate_metadata_headers(disk_metadata)
    disk_name = disk_metadata.get("Name")
    disk_version = disk_metadata.get("Version")
    if (not isinstance(disk_name, str)
            or _normalize_distribution_name(disk_name) != normalized_name
            or disk_version != version):
        raise ValueError("distribution metadata on disk disagrees with discovered metadata")
    return normalized_name, version, _metadata_signature(disk_metadata)


def _is_editable_source_pair(
    records: list[tuple[metadata.Distribution, Path, str, str, str]],
    *,
    root: Path,
    site_packages: tuple[Path, ...],
) -> bool:
    if len(records) != 2:
        return False
    source_records = [record for record in records if record[1].name.endswith(".egg-info")]
    installed_records = [record for record in records if record[1].name.endswith(".dist-info")]
    if len(source_records) != 1 or len(installed_records) != 1:
        return False
    source, installed = source_records[0], installed_records[0]
    source_path = source[1]
    installed_path = installed[1]
    if (source_path.parent != root
            or _normalize_distribution_name(source_path.name.removesuffix(".egg-info")) != source[2]
            or not any(_is_within(installed_path, site_root) for site_root in site_packages)):
        return False
    if source[2] != installed[2]:
        raise ValueError("normalized name conflict for editable distribution")
    if source[3] != installed[3]:
        raise ValueError(f"version conflict for distribution {source[2]}")
    if source[4] != installed[4]:
        raise ValueError(f"metadata conflict for distribution {source[2]}")

    direct_url_path = installed_path / "direct_url.json"
    if direct_url_path.is_symlink() or not direct_url_path.is_file():
        return False
    try:
        direct_url = json.loads(
            direct_url_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("editable distribution direct_url metadata is invalid") from error
    if (not isinstance(direct_url, dict) or set(direct_url) != {"url", "dir_info"}
            or not isinstance(direct_url.get("url"), str)
            or direct_url.get("dir_info") != {"editable": True}):
        return False
    parsed = urlsplit(direct_url["url"])
    if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
        return False
    try:
        source_url_path = Path(unquote(parsed.path)).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return source_url_path == root


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _installed_distributions(root: Path) -> list[dict[str, str]]:
    root = Path(root).resolve(strict=True)
    site_packages = _effective_site_packages()
    grouped: dict[str, list[tuple[metadata.Distribution, Path, str, str, str]]] = {}
    for distribution in metadata.distributions():
        path = _distribution_metadata_path(distribution)
        name, version, signature = _metadata_identity(distribution, path)
        grouped.setdefault(name, []).append((distribution, path, name, version, signature))

    normalized = []
    for name, records in sorted(grouped.items()):
        if len(records) > 1:
            if not _is_editable_source_pair(records, root=root, site_packages=site_packages):
                raise ValueError(f"unexpected metadata locations for distribution {name}")
            # The sole accepted duplicate is a proven source/editable-install representation pair.
            # Its metadata, version and source identity are identical, so this canonical record is
            # independent of discovery order and does not select either entry as authoritative.
            versions = {record[3] for record in records}
            if len(versions) != 1:
                raise ValueError(f"version conflict for distribution {name}")
            version = next(iter(versions))
        else:
            record = records[0]
            if not any(_is_within(record[1], site_root) for site_root in site_packages):
                raise ValueError(f"unexpected metadata location for distribution {name}")
            version = record[3]
        normalized.append({"name": name, "version": version})
    return normalized


def build_runtime_manifest(root: Path) -> dict[str, object]:
    """Fingerprint selected runtime source and the installed interpreter environment.

    Dependency metadata is read from this Python process via importlib.metadata;
    project declarations or the host's package manager are not used as a proxy.
    """
    root = Path(root).resolve(strict=True)
    source_files = _records(root, _runtime_paths(root))
    harness_files = _records(root, _harness_paths(root))
    dependencies = {
        "python_version": platform.python_version(),
        "distributions": _installed_distributions(root),
    }
    source_payload = {"manifest_version": MANIFEST_VERSION, "files": source_files}
    harness_payload = {"manifest_version": MANIFEST_VERSION, "files": harness_files}
    return {
        "manifest_version": MANIFEST_VERSION,
        "source": {**source_payload, "fingerprint": _fingerprint(source_payload)},
        "dependencies": {**dependencies, "fingerprint": _fingerprint(dependencies)},
        "rehearsal_harness": {
            **harness_payload,
            "fingerprint": _fingerprint(harness_payload),
        },
    }


def _valid_manifest(manifest: object) -> bool:
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != MANIFEST_VERSION:
        return False
    if set(manifest) != {"manifest_version", "build_revision", "source", "dependencies", "rehearsal_harness"}:
        return False
    revision = manifest.get("build_revision")
    if not isinstance(revision, str) or not _BUILD_REVISION_PATTERN.fullmatch(revision):
        return False
    for section_name, allowed_path in (
        ("source", lambda path: (
            (path.startswith("app/") and path.endswith(".py"))
            or (path.startswith("migrations/") and path.endswith(".py"))
            or path in _FIXED_RUNTIME_FILES
        )),
        ("rehearsal_harness", lambda path: (
            path.startswith("tests/release_rehearsal/") and path.endswith(".py")
        )),
    ):
        section = manifest.get(section_name)
        if not isinstance(section, dict) or set(section) != {"manifest_version", "files", "fingerprint"}:
            return False
        if section.get("manifest_version") != MANIFEST_VERSION:
            return False
        files = section.get("files")
        if not isinstance(files, list):
            return False
        paths = []
        for record in files:
            if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
                return False
            path = record.get("path")
            digest = record.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                return False
            normalized = PurePosixPath(path)
            if (normalized.is_absolute() or ".." in normalized.parts
                    or normalized.as_posix() != path or not allowed_path(path)):
                return False
            if not _SHA256_PATTERN.fullmatch(digest):
                return False
            paths.append(path)
        if paths != sorted(set(paths)):
            return False
        expected = _fingerprint({"manifest_version": MANIFEST_VERSION, "files": files})
        if section.get("fingerprint") != expected:
            return False

    dependencies = manifest.get("dependencies")
    if (not isinstance(dependencies, dict)
            or set(dependencies) != {"python_version", "distributions", "fingerprint"}):
        return False
    python_version = dependencies.get("python_version")
    distributions = dependencies.get("distributions")
    if not isinstance(python_version, str) or not isinstance(distributions, list):
        return False
    normalized_distributions = []
    for distribution in distributions:
        if not isinstance(distribution, dict) or set(distribution) != {"name", "version"}:
            return False
        name = distribution.get("name")
        version = distribution.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not version:
            return False
        try:
            if _normalize_distribution_name(name) != name:
                return False
        except ValueError:
            return False
        normalized_distributions.append({"name": name, "version": version})
    names = [item["name"] for item in normalized_distributions]
    if names != sorted(set(names)):
        return False
    expected_dependencies = _fingerprint({
        "python_version": python_version,
        "distributions": normalized_distributions,
    })
    return dependencies.get("fingerprint") == expected_dependencies


def write_build_manifest(root: Path, output: Path, *, build_revision: str) -> dict[str, object]:
    if not isinstance(build_revision, str) or not _BUILD_REVISION_PATTERN.fullmatch(build_revision):
        raise ValueError("build revision must be an exact lowercase commit SHA or unknown")
    runtime = build_runtime_manifest(root)
    manifest = {"manifest_version": MANIFEST_VERSION, "build_revision": build_revision, **runtime}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_canonical_bytes(manifest) + b"\n")
    return manifest


def load_build_manifest(path: Path) -> dict[str, object]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not _valid_manifest(manifest):
        raise ValueError("build manifest is invalid")
    return manifest


def runtime_release_identity(
    root: Path,
    *,
    role: Literal["api", "worker"],
    build_revision: str,
    build_manifest: dict[str, object],
) -> ReleaseIdentity:
    if role not in {"api", "worker"}:
        raise ValueError("release identity role must be api or worker")
    if not _valid_manifest(build_manifest):
        raise ValueError("build manifest is invalid")
    runtime = build_runtime_manifest(root)
    build_source = build_manifest["source"]["fingerprint"]
    build_dependencies = build_manifest["dependencies"]["fingerprint"]
    build_harness = build_manifest["rehearsal_harness"]["fingerprint"]
    return ReleaseIdentity(
        role=role,
        build_revision=build_revision,
        manifest_revision=str(build_manifest["build_revision"]),
        manifest_status="ok",
        build_source_fingerprint=str(build_source),
        runtime_source_fingerprint=str(runtime["source"]["fingerprint"]),
        build_dependency_fingerprint=str(build_dependencies),
        runtime_dependency_fingerprint=str(runtime["dependencies"]["fingerprint"]),
        build_harness_fingerprint=str(build_harness),
        runtime_harness_fingerprint=str(runtime["rehearsal_harness"]["fingerprint"]),
        source_matches_build=runtime["source"]["fingerprint"] == build_source,
        dependencies_match_build=runtime["dependencies"]["fingerprint"] == build_dependencies,
        harness_matches_build=runtime["rehearsal_harness"]["fingerprint"] == build_harness,
        build_revision_matches_manifest=build_revision == build_manifest["build_revision"],
    )


def process_release_identity(
    root: Path,
    *,
    role: Literal["api", "worker"],
    build_revision: str,
    manifest_path: Path,
) -> dict[str, object]:
    """Return a bounded unavailable record if the baked manifest is absent/invalid."""
    try:
        manifest = load_build_manifest(manifest_path)
    except FileNotFoundError:
        return {
            "role": role,
            "build_revision": build_revision,
            "manifest_status": "unavailable",
            "failure_code": "BUILD_MANIFEST_MISSING",
        }
    except (OSError, ValueError):
        return {
            "role": role,
            "build_revision": build_revision,
            "manifest_status": "invalid",
            "failure_code": "BUILD_MANIFEST_INVALID",
        }
    try:
        return runtime_release_identity(
            root, role=role, build_revision=build_revision, build_manifest=manifest,
        ).to_safe_dict()
    except (OSError, ValueError):
        return {
            "role": role,
            "build_revision": build_revision,
            "manifest_status": "invalid",
            "failure_code": "RUNTIME_IDENTITY_INVALID",
        }


def release_identities_match(
    api: Mapping[str, object],
    worker: Mapping[str, object],
    *,
    expected_revision: str,
) -> bool:
    if not _BUILD_REVISION_PATTERN.fullmatch(expected_revision) or expected_revision == "unknown":
        return False
    for identity, role in ((api, "api"), (worker, "worker")):
        if identity.get("role") != role or identity.get("manifest_status") != "ok":
            return False
        if identity.get("build_revision") != expected_revision:
            return False
        if identity.get("manifest_revision") != expected_revision:
            return False
        if identity.get("build_revision_matches_manifest") is not True:
            return False
        if identity.get("source_matches_build") is not True:
            return False
        if identity.get("dependencies_match_build") is not True:
            return False
        if identity.get("harness_matches_build") is not True:
            return False
        for build_field, runtime_field in (
            ("build_source_fingerprint", "runtime_source_fingerprint"),
            ("build_dependency_fingerprint", "runtime_dependency_fingerprint"),
            ("build_harness_fingerprint", "runtime_harness_fingerprint"),
        ):
            if identity.get(build_field) != identity.get(runtime_field):
                return False
    for field in (
        "build_source_fingerprint", "runtime_source_fingerprint",
        "build_dependency_fingerprint", "runtime_dependency_fingerprint",
        "build_harness_fingerprint", "runtime_harness_fingerprint",
    ):
        if not api.get(field) or api.get(field) != worker.get(field):
            return False
    return True


def _write_manifest_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    writer = subparsers.add_parser("write-build-manifest")
    writer.add_argument("--root", type=Path, required=True)
    writer.add_argument("--output", type=Path, required=True)
    writer.add_argument("--build-revision", required=True)
    args = parser.parse_args()
    write_build_manifest(args.root, args.output, build_revision=args.build_revision)


if __name__ == "__main__":
    _write_manifest_cli()
