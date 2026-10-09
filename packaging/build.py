#!/usr/bin/env python3
"""
build.py — build standalone executables for the current OS with PyInstaller,
smoke-test them, and pack them into one archive.

    pip install -r packaging/requirements.txt
    python packaging/build.py --version v1.0.0

Output: dist/terminus-<version>-<os>-<arch>.<zip|tar.gz> holding
    Terminus(.exe|.app)    the GUI (room shell + quiz)
    terminus(.exe)         the terminal quiz
    LICENSE

PyInstaller cannot cross-compile, so this runs once per target OS (the release
workflow does that). The GUI is a single-file executable on Windows and Linux
and a .app bundle on macOS, where PyInstaller recommends against one-file app
bundles.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile

import PyInstaller.__main__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build")
ICON = os.path.join(ROOT, "assets", "icon.png")
GUI_NAME = "Terminus"
CLI_NAME = "terminus"
EXE = ".exe" if sys.platform == "win32" else ""


def platform_tag():
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)
    return "%s-%s" % (system, arch)


def pyinstaller(script, name, extra):
    # --specpath puts the generated .spec in build/, which makes relative
    # --add-data paths resolve from there, so every path here is absolute.
    args = [os.path.join(ROOT, script), "--name", name, "--noconfirm", "--clean",
            "--distpath", DIST, "--workpath", WORK, "--specpath", WORK]
    # The bundle must land at <base>/data/bundle.json inside the frozen app,
    # matching terminus.DEFAULT_BUNDLE's sys._MEIPASS fallback.
    args += ["--add-data", "%s:data" % os.path.join(ROOT, "data", "bundle.json")]
    # The bank contains non-cp1252 text (e.g. "→"). Without UTF-8 mode a
    # Windows build crashes printing it whenever stdout is redirected.
    args += ["--python-option", "X utf8"]
    PyInstaller.__main__.run(args + extra)


def build():
    pyinstaller("terminus_cli.py", CLI_NAME, ["--onefile", "--console"])
    gui = ["--windowed", "--icon", ICON, "--add-data", "%s:assets" % ICON]
    if sys.platform == "darwin":
        gui += ["--onedir", "--osx-bundle-identifier", "io.github.ojinguh.terminus"]
    else:
        gui += ["--onefile"]
    pyinstaller("terminus_gui.py", GUI_NAME, gui)


def gui_artifact():
    if sys.platform == "darwin":
        return os.path.join(DIST, GUI_NAME + ".app")
    return os.path.join(DIST, GUI_NAME + EXE)


def smoke_test():
    """Run the frozen builds' self-tests so a missing data file or Qt plugin
    fails the build instead of the user's first launch."""
    cli = os.path.join(DIST, CLI_NAME + EXE)
    gui = gui_artifact()
    if sys.platform == "darwin":
        gui = os.path.join(gui, "Contents", "MacOS", GUI_NAME)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", NO_COLOR="1")
    for cmd in ([cli, "--selftest"], [cli, "--selftest", "--ports"],
                [cli, "--selftest", "--cipher"], [cli, "--selftest", "--crypto"],
                [cli, "--selftest", "--breach", "net"],
                [gui, "--selftest"]):
        print("smoke test:", " ".join(os.path.basename(c) for c in cmd), flush=True)
        subprocess.run(cmd, env=env, check=True, stdout=subprocess.DEVNULL)


def package(version):
    name = "terminus-%s-%s" % (version, platform_tag())
    stage = os.path.join(DIST, name)
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    shutil.move(gui_artifact(), stage)
    shutil.move(os.path.join(DIST, CLI_NAME + EXE), stage)
    shutil.copy(os.path.join(ROOT, "LICENSE"), stage)

    if sys.platform == "darwin":
        # ditto keeps the symlinks and signatures inside the .app intact;
        # shutil's zip would flatten them and break the bundle.
        out = stage + ".zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", stage, out], check=True)
    elif sys.platform == "win32":
        out = shutil.make_archive(stage, "zip", DIST, name)
    else:
        out = stage + ".tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            tar.add(stage, arcname=name)
    shutil.rmtree(stage)
    print("built", out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--version", default="dev",
                    help="label for the archive name, e.g. the release tag")
    args = ap.parse_args(argv)
    shutil.rmtree(DIST, ignore_errors=True)
    build()
    smoke_test()
    package(args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
