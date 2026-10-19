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
    for filename in ("README.md", "README.en.md", "LICENSE", "THIRD_PARTY.md", "VALIDATION.md"):
        shutil.copy2(root / filename, dist / filename)
    (dist / "docs").mkdir(exist_ok=True)
    shutil.copy2(root / "docs/screenshot.png", dist / "docs/screenshot.png")
    (dist / "assets").mkdir(exist_ok=True)
    shutil.copy2(root / "assets/bambu-logo.png", dist / "assets/bambu-logo.png")
    licenses = dist / "licenses"
    licenses.mkdir(exist_ok=True)
    base = Path(sys.base_prefix)
    shutil.copy2(base / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    # The bundled Tk notice explicitly applies to all associated files of Tcl/Tk.
    shutil.copy2(base / "tcl/tk8.6/license.terms", licenses / "Tcl-Tk-license.terms")
    shutil.copy2(root / "assets/BambuStudio-LICENSE.txt", licenses / "BambuStudio-LICENSE.txt")
    metadata = importlib.metadata.distribution("pyinstaller")
    for file in metadata.files or []:
        if ".dist-info/licenses/" in str(file).replace("\\", "/"):
            source = Path(metadata.locate_file(file))
            if source.is_file():
                shutil.copy2(source, licenses / ("PyInstaller-" + source.name))
    output = root / "output"
    output.mkdir(exist_ok=True)
    binary = output / f"BambuAccountSwitcher-{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(binary, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(dist.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(dist))
    source = output / f"BambuAccountSwitcher-{VERSION}-source.zip"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("app.py", "switcher.py", "test_switcher.py", "test_app.py", "build.ps1", "package_release.py",
                     "requirements-build.txt", "README.md", "README.en.md", "THIRD_PARTY.md", "VALIDATION.md", "LICENSE", ".gitignore", ".github/workflows/build.yml"):
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
