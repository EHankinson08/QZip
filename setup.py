"""Binary-wheel hook for QZip (metadata lives in pyproject.toml).

`pip wheel .` / cibuildwheel: build_py runs `make native ARCH=portable` (src/native: qzmf default engine, qze everyday
tiers, libbsc helpers) and `src/qzcore/build.sh` with QZ_ARCH=portable (x18 qzc2, qzc, qzb, qtyp) unless the programs
are already built or QZIP_SKIP_NATIVE_BUILD=1, then ships them as package data. The wheel is tagged
py3-none-<platform>: the programs are not Python extensions, so one wheel serves every Python 3 on that platform.
"""
import os, subprocess, sys
from setuptools import setup
from setuptools.command.build_py import build_py

try:
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # wheel >= 0.44 moved it into setuptools
    try:
        from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
    except ImportError:
        _bdist_wheel = None

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = ".exe" if os.name == "nt" else ""

class BuildNative(build_py):
    def run(self):
        need = [os.path.join(HERE, "src", "native", "qzmf" + EXE), os.path.join(HERE, "src", "native", "qze" + EXE),
                os.path.join(HERE, "src", "qzcore", "qzc2" + EXE)]
        if os.environ.get("QZIP_SKIP_NATIVE_BUILD") != "1" and not all(os.path.exists(p) for p in need):
            env = dict(os.environ, QZ_ARCH="portable")
            subprocess.check_call(["make", "native", "ARCH=portable"], cwd=HERE, env=env)
        super().run()

cmd = {"build_py": BuildNative}
if _bdist_wheel is not None:
    class PlatWheel(_bdist_wheel):
        def finalize_options(self):
            super().finalize_options()
            self.root_is_pure = False
        def get_tag(self):
            _, _, plat = super().get_tag()
            return "py3", "none", plat
    cmd["bdist_wheel"] = PlatWheel

setup(cmdclass=cmd)
