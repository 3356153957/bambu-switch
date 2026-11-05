"""Build and package only explicit project files; never include local Studio data."""
import argparse
import hashlib
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from switcher import VERSION


DOCUMENTS = ("README.md", "README.en.md", "LICENSE", "THIRD_PARTY.md", "VALIDATION.md")
SOURCE_FILES = (
    "app.py", "switcher.py", "local_cache.py", "windows_shortcuts.py",
    "test_switcher.py", "test_app.py", "test_transfer.py", "test_local_cache.py",
    "build.ps1", "package_release.py", "requirements-build.txt", "使用说明.md",
    *DOCUMENTS, ".gitignore", ".github/workflows/build.yml",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    dist = root / "dist" / VERSION
    if not args.package_only:
        subprocess.run([sys.executable, "-m", "unittest", "-v"], cwd=root, check=True)
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                        "--icon", "assets/BambuStudio.ico", "--add-data", "assets;assets",
                        "--distpath", str(dist),
                        "--name", "BambuAccountSwitcher", "app.py"], cwd=root, check=True)
    if not (dist / "BambuAccountSwitcher.exe").is_file():
        raise RuntimeError("Build the executable before packaging")
    # Keep the launch directory small. Only the explicit files in release_files
    # enter the archive, even if someone has placed other data in dist locally.
    release_files = [dist / "BambuAccountSwitcher.exe"]

    def copy_release_file(source, relative):
        target = dist / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        release_files.append(target)

    copy_release_file(root / "使用说明.md", Path("使用说明.md"))
    for filename in DOCUMENTS:
        copy_release_file(root / filename, Path("support") / filename)
    for relative in ("docs/screenshot.png", "assets/bambu-logo.png", "assets/BambuStudio.ico",
                     "assets/BambuStudio-LICENSE.txt"):
        copy_release_file(root / relative, Path("support") / relative)
    licenses = Path("support/licenses")
    base = Path(sys.base_prefix)
    copy_release_file(base / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    # The bundled Tk notice explicitly applies to all associated files of Tcl/Tk.
    copy_release_file(base / "tcl/tk8.6/license.terms", licenses / "Tcl-Tk-license.terms")
    copy_release_file(root / "assets/BambuStudio-LICENSE.txt", licenses / "BambuStudio-LICENSE.txt")
    metadata = importlib.metadata.distribution("pyinstaller")
    for file in metadata.files or []:
        if ".dist-info/licenses/" in str(file).replace("\\", "/"):
            source = Path(metadata.locate_file(file))
            if source.is_file():
                copy_release_file(source, licenses / ("PyInstaller-" + source.name))
    output = root / "output"
    output.mkdir(exist_ok=True)
    binary = output / f"BambuAccountSwitcher-{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(binary, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(release_files):
            archive.write(file, file.relative_to(dist))
    source = output / f"BambuAccountSwitcher-{VERSION}-source.zip"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in SOURCE_FILES:
            archive.write(root / name, name)
        for file in sorted((root / "assets").iterdir()):
            if file.is_file():
                archive.write(file, file.relative_to(root))
        for file in sorted((root / "docs").glob("*.png")):
            archive.write(file, file.relative_to(root))
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text("".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n"
                                 for file in (binary, source, dist / "BambuAccountSwitcher.exe")), encoding="ascii")
    print(f"Packaged {binary}\nPackaged {source}")


if __name__ == "__main__":
    main()
