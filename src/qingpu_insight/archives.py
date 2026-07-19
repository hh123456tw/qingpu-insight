from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

TARGET_NAMES = {"h_lvr_land_a.csv", "h_lvr_land_b.csv"}


def validate_taoyuan_archive(archive: Path) -> bool:
    if not archive.is_file():
        return False
    try:
        with ZipFile(archive) as bundle:
            names = {PurePosixPath(name).name.lower() for name in bundle.namelist()}
            return TARGET_NAMES.issubset(names) and bundle.testzip() is None
    except (BadZipFile, OSError):
        return False


def extract_taoyuan_tables(archive: Path, destination: Path) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with ZipFile(archive) as bundle:
        for member in bundle.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe archive member: {member.filename}")
            name = relative.name.lower()
            if name not in TARGET_NAMES:
                continue
            output = destination / name
            output.write_bytes(bundle.read(member))
            extracted.append(output)
    return tuple(sorted(extracted, key=lambda path: path.name))
