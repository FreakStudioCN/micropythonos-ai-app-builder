#!/usr/bin/env python3
"""Import a trusted MPK showcase bundle into Vite public assets."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import uuid
import zipfile


FEATURED_IDS = {
    "com.blockless.demo001",
    "com.blockless.demo015",
    "com.blockless.demo018",
    "com.blockless.demo030",
    "com.blockless.demo033",
    "com.blockless.demo051",
    "com.blockless.demo053",
    "com.blockless.demo061",
    "com.blockless.demo062",
    "com.blockless.demo067",
    "com.blockless.demo088",
    "com.blockless.demo096",
}
MPK_NAME_RE = re.compile(r"^(?P<fullname>com\.blockless\.demo\d{3})_r(?P<release>\d+)\.mpk$")
SCREENSHOT_NAME_RE = re.compile(r"^(?P<fullname>com\.blockless\.demo\d{3})\.png$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_OUTER_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_INNER_UNCOMPRESSED_BYTES = 8 * 1024 * 1024


class BundleError(ValueError):
    """Raised when an archive violates the showcase import contract."""


def _validate_member(info: zipfile.ZipInfo, archive_label: str) -> PurePosixPath:
    if "\\" in info.filename:
        raise BundleError(f"{archive_label}: backslashes are not allowed: {info.filename}")
    path = PurePosixPath(info.filename)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise BundleError(f"{archive_label}: unsafe path: {info.filename}")
    if info.flag_bits & 0x1:
        raise BundleError(f"{archive_label}: encrypted entries are not allowed: {info.filename}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        raise BundleError(f"{archive_label}: symbolic links are not allowed: {info.filename}")
    if info.file_size > MAX_MEMBER_BYTES:
        raise BundleError(f"{archive_label}: entry is too large: {info.filename}")
    return path


def _file_entries(archive: zipfile.ZipFile, archive_label: str) -> dict[str, zipfile.ZipInfo]:
    entries: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for info in archive.infolist():
        _validate_member(info, archive_label)
        if info.is_dir():
            continue
        if info.filename in entries:
            raise BundleError(f"{archive_label}: duplicate entry: {info.filename}")
        total_size += info.file_size
        entries[info.filename] = info
    if total_size > MAX_OUTER_UNCOMPRESSED_BYTES:
        raise BundleError(f"{archive_label}: archive expands beyond the allowed size")
    return entries


def _required_text(manifest: dict[str, object], key: str, package_name: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, (str, int, float)) or not str(value).strip():
        raise BundleError(f"{package_name}: MANIFEST.JSON requires {key}")
    return str(value).strip()


def _png_dimensions(data: bytes, package_name: str) -> tuple[int, int]:
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise BundleError(f"{package_name}: screenshot is not a valid PNG")
    return struct.unpack(">II", data[16:24])


def _read_mpk(mpk_data: bytes, expected_fullname: str) -> dict[str, object]:
    try:
        with zipfile.ZipFile(io.BytesIO(mpk_data)) as mpk:
            entries = _file_entries(mpk, expected_fullname)
            if sum(info.file_size for info in entries.values()) > MAX_INNER_UNCOMPRESSED_BYTES:
                raise BundleError(f"{expected_fullname}: MPK expands beyond the allowed size")

            required = {
                f"{expected_fullname}/MANIFEST.JSON",
                f"{expected_fullname}/assets/main.py",
                f"{expected_fullname}/icon_64x64.png",
            }
            if set(entries) != required:
                missing = sorted(required - set(entries))
                extra = sorted(set(entries) - required)
                raise BundleError(
                    f"{expected_fullname}: unexpected MPK structure; missing={missing}, extra={extra}"
                )

            manifest_bytes = mpk.read(entries[f"{expected_fullname}/MANIFEST.JSON"])
            app_code_bytes = mpk.read(entries[f"{expected_fullname}/assets/main.py"])
    except zipfile.BadZipFile as exc:
        raise BundleError(f"{expected_fullname}: invalid MPK ZIP") from exc

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"{expected_fullname}: invalid MANIFEST.JSON") from exc
    if not isinstance(manifest, dict):
        raise BundleError(f"{expected_fullname}: MANIFEST.JSON must be an object")
    if manifest.get("fullname") != expected_fullname:
        raise BundleError(f"{expected_fullname}: Manifest fullname does not match the MPK filename")
    try:
        app_code = app_code_bytes.decode("utf-8")
        ast.parse(app_code, filename=f"{expected_fullname}/assets/main.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise BundleError(f"{expected_fullname}: assets/main.py is not valid UTF-8 Python") from exc
    return manifest


def _catalog_entry(
    manifest: dict[str, object],
    fullname: str,
    release: int,
    sha256: str,
    source_description: object,
) -> dict[str, object]:
    short_description = source_description or manifest.get("short_description") or manifest.get("description")
    long_description = manifest.get("long_description") or short_description
    if not isinstance(short_description, str) or not short_description.strip():
        raise BundleError(f"{fullname}: MANIFEST.JSON requires short_description")
    if not isinstance(long_description, str) or not long_description.strip():
        raise BundleError(f"{fullname}: MANIFEST.JSON requires long_description")
    activities = manifest.get("activities")
    if not isinstance(activities, list) or not activities or not isinstance(activities[0], dict):
        raise BundleError(f"{fullname}: MANIFEST.JSON requires activities")
    entrypoint = activities[0].get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise BundleError(f"{fullname}: first activity requires entrypoint")

    return {
        "fullname": fullname,
        "name": _required_text(manifest, "name", fullname),
        "category": _required_text(manifest, "category", fullname),
        "version": _required_text(manifest, "version", fullname),
        "shortDescription": short_description.strip(),
        "longDescription": long_description.strip(),
        "entrypoint": entrypoint.strip(),
        "screenshotUrl": f"/showcase/screenshots/{fullname}.png",
        "mpkUrl": f"/showcase/mpks/{fullname}_r{release}.mpk",
        "featured": fullname in FEATURED_IDS,
        "sha256": sha256,
    }


def import_bundle(
    source: Path,
    output: Path,
    expected_count: int,
    replace_existing: bool = False,
) -> None:
    if not source.is_file():
        raise BundleError(f"Bundle not found: {source}")

    try:
        with zipfile.ZipFile(source) as outer:
            entries = _file_entries(outer, source.name)
            mpks: dict[str, tuple[int, zipfile.ZipInfo]] = {}
            screenshots: dict[str, zipfile.ZipInfo] = {}
            upload_manifest_info: zipfile.ZipInfo | None = None

            for name, info in entries.items():
                path = PurePosixPath(name)
                if len(path.parts) == 1 and path.name.startswith("upystore_upload_manifest_"):
                    if upload_manifest_info is not None:
                        raise BundleError(f"{source.name}: multiple upload manifests found")
                    upload_manifest_info = info
                    continue
                if len(path.parts) != 2:
                    raise BundleError(f"{source.name}: unexpected nested path: {name}")
                folder, filename = path.parts
                if folder in {"mpk", "mpks"}:
                    match = MPK_NAME_RE.fullmatch(filename)
                    if not match:
                        raise BundleError(f"{source.name}: invalid MPK filename: {filename}")
                    fullname = match.group("fullname")
                    if fullname in mpks:
                        raise BundleError(f"{source.name}: duplicate MPK package: {fullname}")
                    mpks[fullname] = (int(match.group("release")), info)
                elif folder == "screenshots":
                    match = SCREENSHOT_NAME_RE.fullmatch(filename)
                    if not match:
                        raise BundleError(f"{source.name}: invalid screenshot filename: {filename}")
                    fullname = match.group("fullname")
                    if fullname in screenshots:
                        raise BundleError(f"{source.name}: duplicate screenshot package: {fullname}")
                    screenshots[fullname] = info
                else:
                    raise BundleError(f"{source.name}: unexpected top-level directory: {folder}")

            if set(mpks) != set(screenshots):
                missing_screenshots = sorted(set(mpks) - set(screenshots))
                missing_mpks = sorted(set(screenshots) - set(mpks))
                raise BundleError(
                    "MPK/screenshot pairs do not match; "
                    f"missing_screenshots={missing_screenshots}, missing_mpks={missing_mpks}"
                )
            if len(mpks) != expected_count:
                raise BundleError(f"Expected {expected_count} packages, found {len(mpks)}")
            if upload_manifest_info is None:
                raise BundleError(f"{source.name}: upload manifest is required")

            try:
                upload_manifest = json.loads(outer.read(upload_manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BundleError(f"{source.name}: invalid upload manifest") from exc
            if not isinstance(upload_manifest, dict) or not isinstance(upload_manifest.get("apps"), list):
                raise BundleError(f"{source.name}: upload manifest requires an apps list")
            if upload_manifest.get("total") != expected_count:
                raise BundleError(
                    f"{source.name}: upload manifest total does not equal {expected_count}"
                )
            manifest_apps: dict[str, dict[str, object]] = {}
            for item in upload_manifest["apps"]:
                if not isinstance(item, dict) or not isinstance(item.get("fullname"), str):
                    raise BundleError(f"{source.name}: invalid upload manifest app entry")
                fullname = item["fullname"]
                if fullname in manifest_apps:
                    raise BundleError(f"{source.name}: duplicate upload manifest app: {fullname}")
                manifest_apps[fullname] = item
            if set(manifest_apps) != set(mpks):
                raise BundleError(f"{source.name}: upload manifest packages do not match MPKs")

            existing_catalog: list[dict[str, object]] = []
            existing_catalog_path = output / "catalog.json"
            if existing_catalog_path.is_file():
                try:
                    existing_catalog = json.loads(
                        existing_catalog_path.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BundleError(f"Cannot read existing catalog: {existing_catalog_path}") from exc
                if not isinstance(existing_catalog, list):
                    raise BundleError("Existing catalog must be a list")
            existing_fullnames = {
                item.get("fullname")
                for item in existing_catalog
                if isinstance(item, dict)
            }
            overlap = sorted(set(mpks) & existing_fullnames)
            if overlap and not replace_existing:
                raise BundleError(f"Imported packages already exist: {overlap[:5]}")
            if overlap:
                existing_catalog = [
                    item
                    for item in existing_catalog
                    if not isinstance(item, dict) or item.get("fullname") not in set(mpks)
                ]

            output_parent = output.parent.resolve()
            output_parent.mkdir(parents=True, exist_ok=True)
            # tempfile.mkdtemp can create an inaccessible ACL when invoked through
            # the Microsoft Store Python shim on Windows.  Creating the directory
            # normally preserves the repository parent's inherited permissions.
            temporary = output_parent / f".{output.name}.import-{uuid.uuid4().hex}"
            temporary.mkdir()
            try:
                if output.exists():
                    shutil.copytree(output, temporary, dirs_exist_ok=True)
                (temporary / "mpks").mkdir(exist_ok=True)
                (temporary / "screenshots").mkdir(exist_ok=True)
                catalog: list[dict[str, object]] = list(existing_catalog)

                for fullname in sorted(mpks):
                    release, mpk_info = mpks[fullname]
                    mpk_data = outer.read(mpk_info)
                    mpk_sha256 = hashlib.sha256(mpk_data).hexdigest()
                    manifest = _read_mpk(mpk_data, fullname)
                    screenshot_data = outer.read(screenshots[fullname])
                    screenshot_sha256 = hashlib.sha256(screenshot_data).hexdigest()
                    width, height = _png_dimensions(screenshot_data, fullname)
                    if (width, height) != (320, 240):
                        raise BundleError(
                            f"{fullname}: expected a 320x240 screenshot, found {width}x{height}"
                        )
                    upload_entry = manifest_apps[fullname]
                    expected_mpk_name = f"{fullname}_r{release}.mpk"
                    expected_screenshot_name = f"{fullname}.png"
                    validations = {
                        "mpk_filename": expected_mpk_name,
                        "mpk_sha256": mpk_sha256,
                        "mpk_size_bytes": len(mpk_data),
                        "screenshot_filename": expected_screenshot_name,
                        "screenshot_sha256": screenshot_sha256,
                        "screenshot_size_bytes": len(screenshot_data),
                    }
                    for key, actual in validations.items():
                        if upload_entry.get(key) != actual:
                            raise BundleError(
                                f"{fullname}: upload manifest {key} does not match the file"
                            )
                    for key in ("name", "version", "category"):
                        if str(upload_entry.get(key, "")).strip() != _required_text(
                            manifest, key, fullname
                        ):
                            raise BundleError(
                                f"{fullname}: upload manifest {key} does not match MANIFEST.JSON"
                            )

                    mpk_name = expected_mpk_name
                    (temporary / "mpks" / mpk_name).write_bytes(mpk_data)
                    (temporary / "screenshots" / f"{fullname}.png").write_bytes(
                        screenshot_data
                    )
                    catalog.append(
                        _catalog_entry(
                            manifest,
                            fullname,
                            release,
                            mpk_sha256,
                            upload_entry.get("description"),
                        )
                    )

                catalog.sort(key=lambda item: str(item["fullname"]))

                (temporary / "catalog.json").write_text(
                    json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                backup: Path | None = None
                if output.exists():
                    backup = output_parent / f".{output.name}.backup-{uuid.uuid4().hex}"
                    os.replace(output, backup)
                try:
                    os.replace(temporary, output)
                except Exception:
                    if backup is not None and backup.exists() and not output.exists():
                        os.replace(backup, output)
                    raise
                if backup is not None:
                    shutil.rmtree(backup)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
    except zipfile.BadZipFile as exc:
        raise BundleError(f"Invalid showcase ZIP: {source}") from exc

    print(
        f"Imported {expected_count} public apps into {output}; "
        f"catalog now contains {len(existing_catalog) + expected_count} apps"
    )


def _asset_path(asset_root: Path, url: str) -> Path:
    relative = url.removeprefix("/")
    if asset_root.name == "showcase" and relative.startswith("showcase/"):
        relative = relative.removeprefix("showcase/")
    return asset_root / PurePosixPath(relative)


def check_catalog(catalog_path: Path, asset_root: Path, expected_count: int) -> None:
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"Cannot read catalog: {catalog_path}") from exc
    if not isinstance(catalog, list) or len(catalog) != expected_count:
        found = len(catalog) if isinstance(catalog, list) else "non-list"
        raise BundleError(f"Expected {expected_count} catalog entries, found {found}")

    required_strings = {
        "fullname",
        "name",
        "category",
        "version",
        "shortDescription",
        "longDescription",
        "entrypoint",
        "screenshotUrl",
        "mpkUrl",
        "sha256",
    }
    fullnames: set[str] = set()
    featured_count = 0
    for index, raw_entry in enumerate(catalog, start=1):
        if not isinstance(raw_entry, dict):
            raise BundleError(f"Catalog entry {index} must be an object")
        for key in required_strings:
            if not isinstance(raw_entry.get(key), str) or not raw_entry[key].strip():
                raise BundleError(f"Catalog entry {index} requires string field {key}")
        if not isinstance(raw_entry.get("featured"), bool):
            raise BundleError(f"Catalog entry {index} requires boolean field featured")

        fullname = raw_entry["fullname"]
        if fullname in fullnames:
            raise BundleError(f"Duplicate catalog package: {fullname}")
        fullnames.add(fullname)
        featured_count += int(raw_entry["featured"])

        for key in ("screenshotUrl", "mpkUrl"):
            url = raw_entry[key]
            if not url.startswith("/showcase/") or ".." in PurePosixPath(url).parts:
                raise BundleError(f"{fullname}: unsafe {key}: {url}")
            if not _asset_path(asset_root, url).is_file():
                raise BundleError(f"{fullname}: missing asset for {key}: {url}")
        mpk_path = _asset_path(asset_root, raw_entry["mpkUrl"])
        if not re.fullmatch(r"[0-9a-f]{64}", raw_entry["sha256"]):
            raise BundleError(f"{fullname}: invalid sha256")
        if hashlib.sha256(mpk_path.read_bytes()).hexdigest() != raw_entry["sha256"]:
            raise BundleError(f"{fullname}: MPK sha256 does not match the catalog")

    if featured_count != len(FEATURED_IDS):
        raise BundleError(
            f"Expected {len(FEATURED_IDS)} featured entries, found {featured_count}"
        )
    print(f"Catalog check passed for {expected_count} public apps")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Bundle ZIP, or catalog JSON with --check")
    parser.add_argument(
        "asset_root",
        type=Path,
        nargs="?",
        help="Asset root for --check; defaults to the catalog directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/public/showcase"),
        help="Destination directory for imported public assets",
    )
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--check", action="store_true", help="Check an existing catalog")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace matching packages that are already present in the destination",
    )
    args = parser.parse_args()

    try:
        if args.check:
            check_catalog(
                args.source,
                args.asset_root or args.source.parent,
                args.expected_count,
            )
        else:
            if args.asset_root is not None:
                parser.error("asset_root is only valid with --check")
            import_bundle(
                args.source,
                args.output,
                args.expected_count,
                replace_existing=args.replace_existing,
            )
    except BundleError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
