#!/usr/bin/env python3

import os
import platform
import setuptools
import subprocess
import sys

from enum import Enum
from setuptools.command.build import build
from setuptools.command.build_ext import build_ext
from setuptools.command.install import install
from setuptools.command.sdist import sdist
from setuptools.extension import Extension

curr_dir = os.path.dirname(os.path.realpath(__file__))
build_dir = os.path.join(curr_dir, "build_dist")
package_dir = os.path.join("build_dist", "CodeChecker")
lib_dir = os.path.join(package_dir, "lib", "python3")
req_file_paths = [
    os.path.join("analyzer", "requirements.txt"),
    os.path.join("web", "requirements.txt")]
data_files_dir_path = os.path.join('share', 'codechecker')
DATA_FILES_DEST = os.path.join('share', 'codechecker')
GENERATED_FILES_DEST = os.path.join('build', '__generated__')

packages = []


def get_requirements():
    """ Get install requirements. """
    requirements = set()
    for req_file_path in req_file_paths:
        with open(req_file_path, 'r') as f:
            requirements.update([s for s in [
                line.split('#', 1)[0].strip(' \t\n') for line in f]
                if s and 'codechecker' not in s])

    return requirements


def init_data_files():
    """
    Initialize data files which will be copied into the package.
    
    Note: This function modifies the distribution's data_files at runtime.
    It adds dynamic files (config, www) that are generated during the build.
    This will be refactored in later commits to use build commands.
    """
    # This function is called from Sdist and Install commands
    # It modifies self.distribution.data_files in those contexts
    # For now, we keep it for compatibility with existing Makefile-based builds
    pass


def init_packages():
    """ Find and initialize the list of packages. """
    global packages
    packages.extend(setuptools.find_packages(where=lib_dir))


ld_logger_src_dir_path = \
    os.path.join("analyzer", "tools", "build-logger", "src")
LD_LOGGER_SRC_PATH = ld_logger_src_dir_path

ld_logger_sources = [
    'ldlogger-hooks.c',
    'ldlogger-logger.c',
    'ldlogger-tool.c',
    'ldlogger-tool-gcc.c',
    'ldlogger-tool-javac.c',
    'ldlogger-util.c'
]
LD_LOGGER_SOURCES = ld_logger_sources

ld_logger_includes = [
    'ldlogger-hooks.h',
    'ldlogger-tool.h',
    'ldlogger-util.h'
]


def get_static_data_files():
    """
    Return a list of static data files that don't require building.
    
    This function only returns files that exist in the source tree and don't
    need to be generated or built. Dynamic files (like generated config files,
    built web frontend, etc.) are handled separately in build commands.
    
    Returns:
        List of tuples (target_directory, [list of source files])
    """
    static_files = []
    
    # Documentation files
    static_files.append((
        os.path.join(data_files_dir_path, "docs"),
        [os.path.join("docs", "README.md")]
    ))
    
    # Requirements files
    for req_file_path in req_file_paths:
        static_files.append((
            os.path.join(data_files_dir_path, os.path.dirname(req_file_path)),
            [req_file_path]
        ))
    
    # ld_logger header files (static source files)
    static_files.append((
        os.path.join(data_files_dir_path, 'ld_logger', 'include'),
        [os.path.join(ld_logger_src_dir_path, i) for i in ld_logger_includes]
    ))
    
    return static_files


def build_ldlogger_shared_libs():
    """
    Build traditional ldlogger.so shared libraries for LD_PRELOAD usage.
    This complements the Python extension module build.
    """
    if sys.platform != "linux":
        return

    lib_dest_dir = os.path.join(
        GENERATED_FILES_DEST, DATA_FILES_DEST, "ld_logger", "lib"
    )

    class Arch(Enum):
        X86_64 = "64bit"
        X86_32 = "32bit"

    def build_ldlogger(arch: Arch):
        os.makedirs(os.path.join(lib_dest_dir, arch.value), exist_ok=True)
        lib_sources = [os.path.join(LD_LOGGER_SRC_PATH, s) for s in LD_LOGGER_SOURCES]
        compile_flags = [
            f"-m{arch.value[:2]}",
            "-D_GNU_SOURCE",
            "-std=c99",
            "-pedantic",
            "-Wall",
            "-Wextra",
            "-O2",
            "-Wno-strict-aliasing",
            "-fno-exceptions",
            "-fPIC",
            "-fomit-frame-pointer",
            "-fvisibility=hidden",
        ]

        link_flags = ["-shared", "-Wl,--no-as-needed", "-ldl"]
        ldlogger_so = os.path.join(lib_dest_dir, arch.value, "ldlogger.so")
        try:
            cmd = (
                ["gcc"] + compile_flags + lib_sources + link_flags + ["-o", ldlogger_so]
            )
            subprocess.check_call(cmd)
            print(f"Built ldlogger shared library for {arch.value}: {ldlogger_so}")
        except subprocess.CalledProcessError as e:
            print(
                f"Warning: Failed to build ldlogger shared library for {arch.value}: {e}"
            )
            print("LD_PRELOAD functionality will not be available")
        except FileNotFoundError:
            print(
                f"Warning: gcc not found, skipping ldlogger shared library build for {arch.value}"
            )
            print("LD_PRELOAD functionality will not be available")

    build_ldlogger(Arch.X86_64)
    # Support old env var name for backward compatibility
    build_64_bit_only = (
        os.environ.get("CC_BUILD_LOGGER_64_BIT_ONLY", "NO").upper() == "YES" or
        os.environ.get("BUILD_LOGGER_64_BIT_ONLY", "NO").upper() == "YES"
    )
    if not build_64_bit_only:
        build_ldlogger(Arch.X86_32)


def build_report_converter():
    """Build and package report-converter."""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        print("Building report-converter...")
        report_converter_dir = os.path.join(root_dir, "tools", "report-converter")

        # Build report-converter using its setup.py
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=report_converter_dir
        )

        # The report-converter package will be included automatically by setuptools
        # since it's listed in get_codechecker_packages()
        print("Report-converter built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        print(f"Warning: Failed to build report-converter: {e}")
        print("Continuing with installation without report-converter...")


module_logger_name = 'codechecker_analyzer.ld_logger.lib.ldlogger'
module_logger = Extension(
    module_logger_name,
    define_macros=[('__LOGGER_MAIN__', None), ('_GNU_SOURCE', None)],
    extra_link_args=[
        '-O2', '-fomit-frame-pointer', '-fvisibility=hidden', '-pedantic',
        '-Wl,--no-as-needed', '-ldl'
    ],
    sources=[
        os.path.join(ld_logger_src_dir_path, s) for s in ld_logger_sources])


class CustomBuild(build):
    """
    Custom build command for CodeChecker.
    
    This class handles all build steps (binary dependencies, API packages,
    web frontend, etc.) that previously happened at import time or via Makefile.
    """
    def run(self):
        # Build binary dependencies first
        build_ldlogger_shared_libs()
        build_report_converter()
        
        # Continue with standard build
        build.run(self)


class BuildExt(build_ext):
    def get_ext_filename(self, ext_name):
        return os.path.join(platform.uname().machine, f"{ext_name}.so")

    def build_extension(self, ext):
        if sys.platform == "linux":
            build_ext.build_extension(self, ext)


class Sdist(sdist):
    def run(self):
        res = subprocess.call(
            ["make", "clean_package", "package", "package_api"],
            env=dict(os.environ,
                     BUILD_DIR=build_dir),
            encoding="utf-8",
            errors="ignore")

        if res:
            sys.exit(1)

        # Add dynamic data files (config, www) generated by Makefile
        for data_dir_name in ['config', 'www']:
            data_dir_path = os.path.join(package_dir, data_dir_name)
            if os.path.exists(data_dir_path):
                for root, _, files in os.walk(data_dir_path):
                    if not files:
                        continue
                    self.distribution.data_files.append((
                        os.path.normpath(
                            os.path.join(data_files_dir_path, data_dir_name,
                                        os.path.relpath(root, data_dir_path))),
                        [os.path.join(root, file_path) for file_path in files]))
        
        init_packages()

        return sdist.run(self)


class Install(install):
    def run(self):
        # Add dynamic data files (config, www) generated by Makefile
        for data_dir_name in ['config', 'www']:
            data_dir_path = os.path.join(package_dir, data_dir_name)
            if os.path.exists(data_dir_path):
                for root, _, files in os.walk(data_dir_path):
                    if not files:
                        continue
                    self.distribution.data_files.append((
                        os.path.normpath(
                            os.path.join(data_files_dir_path, data_dir_name,
                                        os.path.relpath(root, data_dir_path))),
                        [os.path.join(root, file_path) for file_path in files]))
        
        init_packages()

        return install.run(self)

with open(os.path.join("docs", "README.md"), "r",
          encoding="utf-8", errors="ignore") as fh:
    long_description = fh.read()


setuptools.setup(
    name="codechecker",
    version="6.28.0",
    author='CodeChecker Team (Ericsson)',
    author_email='codechecker-tool@googlegroups.com',
    description="CodeChecker is an analyzer tooling, defect database and "
                "viewer extension",
    long_description=long_description,
    long_description_content_type = "text/markdown",
    url="https://github.com/Ericsson/CodeChecker",
    project_urls = {
        "Documentation": "http://codechecker.readthedocs.io",
        "Issue Tracker": "http://github.com/Ericsson/CodeChecker/issues",
    },
    keywords=['codechecker', 'plist', 'sarif'],
    license='Apache-2.0 WITH LLVM-exception',
    packages=packages,
    package_dir={
        "": lib_dir
    },
    data_files=get_static_data_files(),
    include_package_data=True,
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: MacOS",
        "Operating System :: POSIX",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: Software Development :: Bug Tracking",
        "Topic :: Software Development :: Quality Assurance",
    ],
    install_requires=list(get_requirements()),
    ext_modules=[module_logger],
    cmdclass={
        'build': CustomBuild,
        'sdist': Sdist,
        'install': Install,
        'build_ext': BuildExt,
    },
    python_requires='>=3.9',
    scripts=[
        'scripts/gerrit_changed_files_to_skipfile.py'
    ],
    entry_points={
        'console_scripts': [
            'CodeChecker = codechecker_common.cli:main',
            'merge-clang-extdef-mappings = codechecker_merge_clang_extdef_mappings.cli:main',
            'post-process-stats = codechecker_statistics_collector.cli:main',
            'report-converter = codechecker_report_converter.cli:main',
            'tu_collector = tu_collector.tu_collector:main'
        ]
    },
)
