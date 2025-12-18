#!/usr/bin/env python3
"""
This setup.py provides a PEP 517-compliant build system for CodeChecker.

BUILD SYSTEM OVERVIEW:
=====================

The build system is organized into custom setuptools command classes:
- CustomBuild: Handles all build steps (binaries, API packages, web frontend)
- CustomBuildPy: Generates version files and configuration
- CustomDevelop: Development mode installation
- CustomBuildExt: Builds Python extension modules
- CleanCommand: Removes build artifacts
- StandalonePackageCommand: Creates standalone packages

BUILD STEPS:
============

1. Binary Dependencies:
   - ldlogger shared libraries (.so files for LD_PRELOAD)
   - report-converter tool
   - tu_collector tool
   - statistics_collector tool
   - merge_clang_extdef_mappings tool

2. API Packages:
   - Thrift API code generation (using Docker)
   - Python API package building

3. Web Frontend:
   - Vue.js application build (using npm)

4. Configuration Files:
   - Version files (web_version.json, analyzer_version.json)
   - Optional git metadata embedding

ENVIRONMENT VARIABLES:
======================

Build Control:
- CC_BUILD_ERROR_MODE: Error handling mode ("strict" or "warn",
  default: "strict")
- CC_FORCE_REBUILD: Force rebuild all components ("YES" or "NO",
  default: "NO")
- CC_FORCE_BUILD_API_PACKAGES: Force rebuild API packages
  ("YES" or "NO")

Component Control:
- CC_BUILD_UI_DIST: Build web frontend ("YES" or "NO",
  default: "YES")
- CC_BUILD_LOGGER_64_BIT_ONLY: Build only 64-bit ldlogger
  ("YES" or "NO", default: "NO")

Version Control:
- CC_SKIP_BUILD_META: Skip embedding git metadata and build date
  ("YES" or "NO", default: "NO")
  (Build metadata is embedded by default; set to "YES" to disable)

USAGE:
======

Standard build:
    python setup.py build

Development installation:
    pip install -e .

Source distribution:
    python setup.py sdist

Clean build artifacts:
    python setup.py clean --all

Standalone package:
    python setup.py standalone_package

CACHING:
========

The build system uses timestamp-based caching to skip rebuilding unchanged
components. To force a full rebuild, set CC_FORCE_REBUILD=YES.

ERROR HANDLING:
===============

By default, the build system uses "strict" mode, which fails immediately
on build errors. Set CC_BUILD_ERROR_MODE=warn to continue with warnings
instead of failing.

DEPENDENCIES:
=============

Required:
- Python 3.x

Optional (for full functionality):
- gcc (Linux only, for ldlogger shared libraries)
- npm (for web frontend build)
- Docker (for API package generation from Thrift files)

Windows Support:
- Platform-specific checks are skipped on Windows
- Some components (e.g., ldlogger) are Linux-only
"""

import os
import platform
import setuptools
import subprocess
import sys
import shutil
import tarfile
import tempfile
import json
import time
import glob
from contextlib import contextmanager
from enum import Enum
from typing import Optional, List, Tuple, Dict, Set, Any, Iterator, Union


@contextmanager
def change_directory(directory: str) -> Iterator[None]:
    """
    Context manager for temporarily changing the working directory.

    Usage:
        with change_directory('/path/to/dir'):
            # code that runs in /path/to/dir
            pass
        # automatically returns to original directory
    """
    original_dir = os.getcwd()
    try:
        os.chdir(directory)
        yield
    finally:
        os.chdir(original_dir)


def should_force_rebuild() -> bool:
    """
    Check if force rebuild is requested via environment variable.

    Returns:
        True if CC_FORCE_REBUILD environment variable is set to "YES",
        False otherwise.
    """
    return os.environ.get("CC_FORCE_REBUILD", "NO").upper() == "YES"


def should_rebuild(output_path: str, source_paths: List[str]) -> bool:
    """
    Check if a build output needs to be rebuilt based on source file
    timestamps.

    Compares modification times of source files against the output
    file/directory. If any source is newer than the output, or if the
    output doesn't exist, a rebuild is needed. For directories, uses
    the most recent file modification time within the directory tree.

    Args:
        output_path: Path to the build output file or directory
        source_paths: List of source file/directory paths to check

    Returns:
        True if rebuild is needed, False if output is up to date
    """
    if should_force_rebuild():
        return True

    if not os.path.exists(output_path):
        return True

    try:
        if os.path.isdir(output_path):
            output_time = 0
            for root, _, files in os.walk(output_path):
                for f in files:
                    file_path = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(file_path)
                        output_time = max(output_time, mtime)
                    except OSError:
                        pass
        else:
            output_time = os.path.getmtime(output_path)
    except OSError:
        return True

    for source_path in source_paths:
        if not os.path.exists(source_path):
            continue

        try:
            if os.path.isdir(source_path):
                source_time = 0
                for root, _, files in os.walk(source_path):
                    for f in files:
                        file_path = os.path.join(root, f)
                        try:
                            mtime = os.path.getmtime(file_path)
                            source_time = max(source_time, mtime)
                        except OSError:
                            pass
                if source_time > output_time:
                    return True
            else:
                source_time = os.path.getmtime(source_path)
                if source_time > output_time:
                    return True
        except OSError:
            return True

    return False


# Setuptools imports must come after utility functions
# (setuptools may not be available during import-time execution checks)
# pycodestyle: disable=E402
from setuptools import Command
from setuptools.command.build import build
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from setuptools.command.develop import develop
from setuptools.command.install import install
from setuptools.command.sdist import sdist
from setuptools.extension import Extension


# Error handling mode: "strict" (default) or "warn"
# In strict mode, build failures raise exceptions
# In warn mode, build failures print warnings and continue
def get_error_mode() -> str:
    """
    Get error handling mode from environment variable.

    Returns:
        "strict" or "warn" based on CC_BUILD_ERROR_MODE environment variable.
        Defaults to "strict" if not set or invalid.
    """
    mode = os.environ.get("CC_BUILD_ERROR_MODE", "strict").lower()
    if mode not in ("strict", "warn"):
        print(f"Warning: Invalid CC_BUILD_ERROR_MODE '{mode}', using 'strict'")
        return "strict"
    return mode


def check_build_dependencies() -> Tuple[bool, bool, List[str]]:
    """
    Check for required and optional build dependencies.

    Verifies presence of Python, gcc (Linux only), npm, and Docker.
    Provides helpful error messages with installation instructions.
    Platform-specific checks are skipped on Windows.

    Returns:
        Tuple of (required_deps_ok, optional_deps_ok, messages) where:
        - required_deps_ok: True if all required dependencies are present
        - optional_deps_ok: True if all optional dependencies are present
        - messages: List of informational/error messages for the user
    """
    required_ok = True
    optional_ok = True
    messages = []

    if sys.executable is None:
        required_ok = False
        messages.append("ERROR: Python interpreter not found")

    if sys.platform == "linux":
        try:
            subprocess.run(
                ["gcc", "--version"], capture_output=True, check=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            optional_ok = False
            messages.append(
                "WARNING: gcc not found. ldlogger shared library build "
                "will be skipped."
            )
            messages.append(
                "  Install with: sudo apt-get install gcc (Debian/Ubuntu)")
            messages.append(
                "  or: sudo yum install gcc (RHEL/CentOS)")

    try:
        subprocess.run(
            ["npm", "--version"], capture_output=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        optional_ok = False
        messages.append(
            "WARNING: npm not found. Web frontend build will be skipped."
        )
        messages.append("  Install from: https://nodejs.org/")
        messages.append("  or: sudo apt-get install npm (Debian/Ubuntu)")

    try:
        subprocess.run(
            ["docker", "--version"], capture_output=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        optional_ok = False
        messages.append(
            "WARNING: Docker not found. API packages will use prebuilt "
            "versions."
        )
        messages.append(
            "  Install from: https://docs.docker.com/get-docker/")
        messages.append(
            "  Note: Prebuilt API packages are included in the "
            "repository.")

    return required_ok, optional_ok, messages


def handle_build_error(
    error: Exception,
    component_name: str,
    error_mode: Optional[str] = None
) -> None:
    """
    Handle build errors according to the error mode.

    In strict mode, raises the exception. In warn mode, prints a warning
    and continues without the failed component.

    Args:
        error: The exception that occurred during build
        component_name: Name of the component that failed to build
        error_mode: Error mode ("strict" or "warn"). If None, uses
                    get_error_mode() to determine the mode.

    Raises:
        Exception: In strict mode, re-raises the original exception
    """
    if error_mode is None:
        error_mode = get_error_mode()

    error_msg = f"Failed to build {component_name}: {error}"

    if error_mode == "strict":
        print(f"ERROR: {error_msg}")
        raise
    else:
        print(f"Warning: {error_msg}")
        print(f"Continuing with installation without {component_name}...")
        return None


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


def get_requirements() -> Set[str]:
    """
    Read and parse Python package requirements from requirements.txt files.

    Reads requirements from analyzer/requirements.txt and web/requirements.txt,
    strips comments and whitespace, and filters out any codechecker packages
    (which are part of this distribution, not external dependencies).

    Returns:
        Set of requirement strings (package names with optional version specs)
    """
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

    This function is kept for backward compatibility but is no longer
    used. Data files are now collected dynamically by
    CustomBuild.collect_data_files().
    """
    pass


def init_packages() -> None:
    """
    Discover and initialize the list of Python packages for distribution.

    Searches for packages in the root directory and various subdirectories
    (analyzer, web, tools). Filters to include only codechecker_* packages
    and specific tool packages, excluding test packages and other
    non-distribution packages.
    """
    global packages
    root_dir = os.path.dirname(os.path.abspath(__file__))

    if os.path.exists(lib_dir):
        lib_packages = setuptools.find_packages(where=lib_dir)
        packages.extend(lib_packages)

    root_packages = setuptools.find_packages(where=root_dir)

    search_dirs = [
        root_dir,
        os.path.join(root_dir, "analyzer"),
        os.path.join(root_dir, "analyzer", "tools", "statistics_collector"),
        os.path.join(root_dir, "analyzer", "tools",
                     "merge_clang_extdef_mappings"),
        os.path.join(root_dir, "web"),
        os.path.join(root_dir, "web", "server"),
        os.path.join(root_dir, "web", "client"),
        os.path.join(root_dir, "tools", "report-converter"),
        os.path.join(root_dir, "tools", "tu_collector"),
    ]

    all_found_packages = set()
    for search_dir in search_dirs:
        if os.path.exists(search_dir):
            found = setuptools.find_packages(where=search_dir)
            all_found_packages.update(found)

    for pkg in all_found_packages:
        if (pkg.startswith('codechecker_') or
                pkg in ['tu_collector', 'codechecker_report_converter',
                        'codechecker_statistics_collector',
                        'codechecker_merge_clang_extdef_mappings']):
            if pkg not in packages:
                packages.append(pkg)


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


def get_static_data_files() -> List[Tuple[str, List[str]]]:
    """
    Return a list of static data files that don't require building.

    Collects documentation files, requirements files, configuration files
    from multiple locations (config/, analyzer/config/, web/config/,
    web/server/config/), and ld_logger header files. All nested config
    folders are flattened into the package's data directory.

    Returns:
        List of tuples (target_directory, [list of source files])
    """
    static_files = []

    static_files.append((
        os.path.join(data_files_dir_path, "docs"),
        [os.path.join("docs", "README.md")]
    ))

    for req_file_path in req_file_paths:
        static_files.append((
            os.path.join(data_files_dir_path, os.path.dirname(req_file_path)),
            [req_file_path]
        ))

    config_dirs = [
        ("config", "config"),
        ("analyzer/config", "config"),
        ("web/config", "config"),
        ("web/server/config", "config"),
    ]

    for source_dir, target_subdir in config_dirs:
        if os.path.exists(source_dir):
            config_file_groups = {}
            for root, dirs, files in os.walk(source_dir):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                files = [f for f in files if not f.startswith('.')]

                if not files:
                    continue

                rel_path = os.path.relpath(root, source_dir)
                if rel_path == ".":
                    target_dir = os.path.join(
                        data_files_dir_path, target_subdir)
                else:
                    target_dir = os.path.join(
                        data_files_dir_path, target_subdir, rel_path)

                file_list = [os.path.join(root, f) for f in files]
                if target_dir not in config_file_groups:
                    config_file_groups[target_dir] = []
                config_file_groups[target_dir].extend(file_list)

            for target_dir, files in config_file_groups.items():
                static_files.append((target_dir, files))

    static_files.append((
        os.path.join(data_files_dir_path, 'ld_logger', 'include'),
        [os.path.join(ld_logger_src_dir_path, i) for i in ld_logger_includes]
    ))

    return static_files


def get_ldlogger_data_files() -> List[Tuple[str, List[str]]]:
    """
    Get ldlogger shared library data files generated during build.

    Searches for ldlogger.so files in the build output directory and groups
    them by their subdirectory structure (e.g., 64bit/, 32bit/) for proper
    installation into the package.

    Returns:
        List of tuples (target_directory, [list of .so files])
    """
    data_files = []

    lib_dir_path = os.path.join(
        GENERATED_FILES_DEST, DATA_FILES_DEST, "ld_logger", "lib"
    )
    if os.path.exists(lib_dir_path):
        ldlogger_files = glob.glob(
            os.path.join(lib_dir_path, "**", "ldlogger.so"), recursive=True
        )
        if ldlogger_files:
            for ldlogger_file in ldlogger_files:
                rel_path = os.path.relpath(ldlogger_file, lib_dir_path)
                subdir = os.path.dirname(rel_path)
                if subdir:
                    target_dir = os.path.join(
                        DATA_FILES_DEST, "ld_logger", "lib", subdir)
                else:
                    target_dir = os.path.join(
                        DATA_FILES_DEST, "ld_logger", "lib")
                data_files.append((target_dir, [ldlogger_file]))

    return data_files


def get_web_frontend_data_files() -> List[Tuple[str, List[str]]]:
    """
    Get data files for web frontend assets.
    This function should only be called AFTER the web frontend has been built.
    """
    data_files = []

    web_generated_www = os.path.join(
        GENERATED_FILES_DEST, DATA_FILES_DEST, "www")

    if os.path.exists(web_generated_www):
        for root, _, files in os.walk(web_generated_www):
            if files:
                existing_files = [
                    f for f in files if os.path.exists(os.path.join(root, f))
                ]
                if existing_files:
                    rel_path = os.path.relpath(root, web_generated_www)
                    target_path = os.path.join(DATA_FILES_DEST, "www")
                    if rel_path != ".":
                        target_path = os.path.join(target_path, rel_path)
                    file_paths = []
                    for f in existing_files:
                        file_path = os.path.join(root, f)
                        if (os.path.exists(file_path) and
                                os.path.isfile(file_path)):
                            file_paths.append(file_path)
                    if file_paths:
                        data_files.append((target_path, file_paths))

    return data_files


def get_version_data_files() -> List[Tuple[str, List[str]]]:
    """
    Get version file data files that are generated during the build process.
    """
    data_files = []
    config_files_path = os.path.join(DATA_FILES_DEST, "config")
    config_dir = os.path.join(GENERATED_FILES_DEST, config_files_path)

    # Version files
    version_files = []
    web_version_file = os.path.join(config_dir, "web_version.json")
    analyzer_version_file = os.path.join(config_dir, "analyzer_version.json")

    if os.path.exists(web_version_file):
        version_files.append(web_version_file)
    if os.path.exists(analyzer_version_file):
        version_files.append(analyzer_version_file)

    if version_files:
        data_files.append((config_files_path, version_files))

    return data_files


def build_ldlogger_shared_libs() -> None:
    """
    Build ldlogger.so shared libraries for LD_PRELOAD usage.

    Compiles 32-bit and/or 64-bit shared libraries from C source files.
    This complements the Python extension module build. Only builds on
    Linux. Uses gcc with architecture-specific flags. Build failures are
    handled gracefully since ldlogger is optional for LD_PRELOAD
    functionality.
    """
    if sys.platform != "linux":
        return

    lib_dest_dir = os.path.join(
        GENERATED_FILES_DEST, DATA_FILES_DEST, "ld_logger", "lib"
    )

    ldlogger_sources = [
        os.path.join(LD_LOGGER_SRC_PATH, s) for s in LD_LOGGER_SOURCES]
    output_64bit = os.path.join(lib_dest_dir, "64bit", "ldlogger.so")
    output_32bit = os.path.join(lib_dest_dir, "32bit", "ldlogger.so")

    build_64_bit_only_value = os.environ.get(
        "CC_BUILD_LOGGER_64_BIT_ONLY", "NO")
    build_64_bit_only = build_64_bit_only_value.upper() == "YES"

    rebuild_64bit = should_rebuild(output_64bit, ldlogger_sources)
    rebuild_32bit = (should_rebuild(output_32bit, ldlogger_sources)
                     if not build_64_bit_only else False)

    if not rebuild_64bit and not rebuild_32bit:
        print("ldlogger shared libraries are up to date, skipping build.")
        return

    class Arch(Enum):
        X86_64 = "64bit"
        X86_32 = "32bit"

    def build_ldlogger(arch: Arch):
        error_mode = get_error_mode()
        os.makedirs(os.path.join(lib_dest_dir, arch.value), exist_ok=True)
        lib_sources = [
            os.path.join(LD_LOGGER_SRC_PATH, s) for s in LD_LOGGER_SOURCES]
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
                ["gcc"] + compile_flags + lib_sources + link_flags +
                ["-o", ldlogger_so]
            )
            subprocess.check_call(cmd)
            print(
                f"Built ldlogger shared library for {arch.value}: "
                f"{ldlogger_so}")
        except subprocess.CalledProcessError as e:
            print(
                f"Warning: Failed to build ldlogger shared library for "
                f"{arch.value}: {e}")
            print("LD_PRELOAD functionality will not be available")
        except FileNotFoundError:
            print(
                f"Warning: gcc not found, skipping ldlogger shared library "
                f"build for {arch.value}")
            print("LD_PRELOAD functionality will not be available")

    if rebuild_64bit:
        build_ldlogger(Arch.X86_64)
    if rebuild_32bit:
        build_ldlogger(Arch.X86_32)


def build_report_converter() -> None:
    """
    Build and package the report-converter tool.

    Invokes the report-converter's setup.py build command. Skips build if
    the directory doesn't exist or if sources haven't changed since last build.
    """
    root_dir = os.path.dirname(os.path.abspath(__file__))
    report_converter_dir = os.path.join(root_dir, "tools", "report-converter")

    if not os.path.exists(report_converter_dir):
        print(f"Warning: report-converter directory not found at "
              f"{report_converter_dir}, skipping build.")
        return

    build_dir = os.path.join(report_converter_dir, "build")
    source_files = []
    for root, _, files in os.walk(report_converter_dir):
        if "build" in root or "__pycache__" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith((".py", ".c", ".h", ".cpp", ".hpp", "setup.py")):
                source_files.append(os.path.join(root, f))

    if not should_rebuild(build_dir, source_files):
        print("report-converter is up to date, skipping build.")
        return

    error_mode = get_error_mode()
    try:
        print("Building report-converter...")
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=report_converter_dir
        )
        print("Report-converter built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        handle_build_error(e, "report-converter", error_mode)


def build_tu_collector() -> None:
    """
    Build and package the tu_collector tool.

    Invokes tu_collector's setup.py build command. Skips build if the directory
    doesn't exist or if sources haven't changed since last build.
    """
    root_dir = os.path.dirname(os.path.abspath(__file__))
    tu_collector_dir = os.path.join(root_dir, "tools", "tu_collector")

    if not os.path.exists(tu_collector_dir):
        print(f"Warning: tu_collector directory not found at "
              f"{tu_collector_dir}, skipping build.")
        return

    build_dir = os.path.join(tu_collector_dir, "build")
    source_files = []
    for root, _, files in os.walk(tu_collector_dir):
        if "build" in root or "__pycache__" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith((".py", ".c", ".h", ".cpp", ".hpp", "setup.py")):
                source_files.append(os.path.join(root, f))

    if not should_rebuild(build_dir, source_files):
        print("tu_collector is up to date, skipping build.")
        return

    error_mode = get_error_mode()
    try:
        print("Building tu_collector...")
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=tu_collector_dir
        )
        print("tu_collector built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        handle_build_error(e, "tu_collector", error_mode)


def build_statistics_collector() -> None:
    """
    Build and package the statistics_collector tool.

    Invokes statistics_collector's setup.py build command. Skips build if
    the directory doesn't exist or if sources haven't changed since last build.
    """
    root_dir = os.path.dirname(os.path.abspath(__file__))
    statistics_collector_dir = os.path.join(
        root_dir, "analyzer", "tools", "statistics_collector")

    if not os.path.exists(statistics_collector_dir):
        print(f"Warning: statistics_collector directory not found at "
              f"{statistics_collector_dir}, skipping build.")
        return

    build_dir = os.path.join(statistics_collector_dir, "build")
    source_files = []
    for root, _, files in os.walk(statistics_collector_dir):
        if "build" in root or "__pycache__" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith((".py", ".c", ".h", ".cpp", ".hpp", "setup.py")):
                source_files.append(os.path.join(root, f))

    if not should_rebuild(build_dir, source_files):
        print("statistics_collector is up to date, skipping build.")
        return

    error_mode = get_error_mode()
    try:
        print("Building statistics_collector...")
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=statistics_collector_dir
        )
        print("statistics_collector built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        handle_build_error(e, "statistics_collector", error_mode)


def build_merge_clang_extdef_mappings() -> None:
    """
    Build and package the merge_clang_extdef_mappings tool.

    Invokes merge_clang_extdef_mappings's setup.py build command.
    Skips build
    if the directory doesn't exist or if sources haven't changed since
    last build.
    """
    root_dir = os.path.dirname(os.path.abspath(__file__))
    merge_clang_dir = os.path.join(
        root_dir, "analyzer", "tools", "merge_clang_extdef_mappings")

    if not os.path.exists(merge_clang_dir):
        print(f"Warning: merge_clang_extdef_mappings directory not found "
              f"at {merge_clang_dir}, skipping build.")
        return

    build_dir = os.path.join(merge_clang_dir, "build")
    source_files = []
    for root, _, files in os.walk(merge_clang_dir):
        if "build" in root or "__pycache__" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith((".py", ".c", ".h", ".cpp", ".hpp", "setup.py")):
                source_files.append(os.path.join(root, f))

    if not should_rebuild(build_dir, source_files):
        print("merge_clang_extdef_mappings is up to date, skipping build.")
        return

    error_mode = get_error_mode()
    try:
        print("Building merge_clang_extdef_mappings...")
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=merge_clang_dir
        )
        print("merge_clang_extdef_mappings built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        handle_build_error(e, "merge_clang_extdef_mappings", error_mode)


def has_prebuilt_api_packages() -> bool:
    """
    Check if prebuilt API package tarballs exist.

    Returns:
        True if both codechecker_api.tar.gz and
        codechecker_api_shared.tar.gz exist in their respective dist
        directories, False otherwise.
    """
    return os.path.exists(
        os.path.join(
            "web",
            "api",
            "py",
            "codechecker_api_shared",
            "dist",
            "codechecker_api_shared.tar.gz",
        )
    ) and os.path.exists(
        os.path.join(
            "web", "api", "py", "codechecker_api", "dist",
            "codechecker_api.tar.gz"
        )
    )


def copy_directory(src: str, dst: str) -> None:
    """
    Recursively copy all contents from src directory to dst directory.

    Creates the destination directory if it doesn't exist. Preserves
    directory structure and file metadata using shutil.copy2.

    Args:
        src: Source directory path
        dst: Destination directory path
    """
    if not os.path.exists(dst):
        os.makedirs(dst)

    for item in os.listdir(src):
        src_item = os.path.join(src, item)
        dst_item = os.path.join(dst, item)

        if os.path.isdir(src_item):
            copy_directory(src_item, dst_item)
        else:
            shutil.copy2(src_item, dst_item)


def include_api_packages() -> None:
    """
    Extract prebuilt API tarballs into build/lib if they exist.

    Extracts codechecker_api and codechecker_api_shared from their
    tarballs into the build/lib directory. This avoids invoking
    Docker/Thrift/pip during install while ensuring the API packages
    are available at runtime.
    """
    base_dir = os.path.abspath(os.path.dirname(__file__))
    api_dir = os.path.join(base_dir, "web", "api", "py")
    api_shared_tarball = os.path.join(
        api_dir, "codechecker_api_shared", "dist",
        "codechecker_api_shared.tar.gz")
    api_tarball = os.path.join(
        api_dir, "codechecker_api", "dist", "codechecker_api.tar.gz"
    )

    build_lib = os.path.join(base_dir, "build", "lib")
    os.makedirs(build_lib, exist_ok=True)

    def extract_package(tar_path: str, package_name: str) -> None:
        if not os.path.exists(tar_path):
            return
        try:
            with tarfile.open(tar_path, "r:gz") as tf:
                tmp_dir = os.path.join(
                    base_dir, "build", "__api_extract__", package_name
                )
                shutil.rmtree(tmp_dir, ignore_errors=True)
                os.makedirs(tmp_dir, exist_ok=True)
                tf.extractall(tmp_dir)

                src_pkg_dir = None
                for root, dirs, files in os.walk(tmp_dir):
                    if (
                        os.path.basename(root) == package_name
                        and "__init__.py" in files
                    ):
                        src_pkg_dir = root
                        break
                if not src_pkg_dir:
                    print(
                        f"Warning: Could not locate {package_name} in "
                        f"{tar_path}")
                    return

                dst_pkg_dir = os.path.join(build_lib, package_name)
                shutil.rmtree(dst_pkg_dir, ignore_errors=True)
                shutil.copytree(src_pkg_dir, dst_pkg_dir)
                print(
                    f"Included prebuilt {package_name} package from "
                    f"{tar_path}")
        except Exception as e:
            print(
                f"Warning: Failed to include {package_name} from "
                f"{tar_path}: {e}")

    extract_package(api_shared_tarball, "codechecker_api_shared")
    extract_package(api_tarball, "codechecker_api")


def build_api_packages() -> None:
    """
    Build API packages from Thrift definitions if needed.

    Generates Python code from Thrift files using Docker and Thrift
    compiler, then builds codechecker_api and codechecker_api_shared
    packages. Only rebuilds if tarballs don't exist or if Thrift files
    are newer than the existing tarballs. Requires Docker to be available.
    """
    print("Checking and building API packages if needed...")

    api_dir = os.path.join("web", "api")
    api_py_dir = os.path.join(api_dir, "py")
    api_shared_dist = os.path.join(
        api_py_dir, "codechecker_api_shared", "dist")
    api_dist = os.path.join(api_py_dir, "codechecker_api", "dist")

    api_shared_tarball = os.path.join(
        api_shared_dist, "codechecker_api_shared.tar.gz")
    api_tarball = os.path.join(api_dist, "codechecker_api.tar.gz")

    thrift_files = [
        os.path.join(api_dir, "authentication.thrift"),
        os.path.join(api_dir, "products.thrift"),
        os.path.join(api_dir, "report_server.thrift"),
        os.path.join(api_dir, "configuration.thrift"),
        os.path.join(api_dir, "server_info.thrift"),
        os.path.join(api_dir, "codechecker_api_shared.thrift"),
    ]

    existing_thrift_files = [f for f in thrift_files if os.path.exists(f)]

    need_build = False
    if (not os.path.exists(api_shared_tarball) or
            not os.path.exists(api_tarball)):
        need_build = True
        print("API packages not found, building them...")
    elif existing_thrift_files:
        if (should_rebuild(api_shared_tarball, existing_thrift_files) or
                should_rebuild(api_tarball, existing_thrift_files)):
            need_build = True
            print("API packages are outdated, rebuilding them...")

    if need_build:
        error_mode = get_error_mode()
        try:
            py_api_dir = os.path.join(
                api_py_dir, "codechecker_api", "codechecker_api")
            py_api_shared_dir = os.path.join(
                api_py_dir, "codechecker_api_shared", "codechecker_api_shared")

            os.makedirs(py_api_dir, exist_ok=True)
            os.makedirs(py_api_shared_dir, exist_ok=True)
            os.makedirs(api_shared_dist, exist_ok=True)
            os.makedirs(api_dist, exist_ok=True)

            try:
                subprocess.check_output(
                    ["docker", "--version"], encoding="utf-8", errors="ignore"
                )
                has_docker = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                has_docker = False

            if has_docker:
                print("Building API packages using Docker...")

                thrift_files = [
                    os.path.join(api_dir, "authentication.thrift"),
                    os.path.join(api_dir, "products.thrift"),
                    os.path.join(api_dir, "report_server.thrift"),
                    os.path.join(api_dir, "configuration.thrift"),
                    os.path.join(api_dir, "server_info.thrift"),
                    os.path.join(api_dir, "codechecker_api_shared.thrift"),
                ]

                with tempfile.TemporaryDirectory() as temp_dir:
                    for thrift_file in thrift_files:
                        if os.path.exists(thrift_file):
                            print(f"Processing {thrift_file}...")
                            uid = (os.getuid() if hasattr(os, "getuid")
                                   else 1000)
                            gid = (os.getgid() if hasattr(os, "getgid")
                                   else 1000)

                            cmd = [
                                "docker",
                                "run",
                                "--rm",
                                "-u",
                                f"{uid}:{gid}",
                                "-v",
                                f"{os.path.abspath(api_dir)}:/data",
                                "thrift:0.11.0",
                                "thrift",
                                "-r",
                                "-o",
                                "/data",
                                "--gen",
                                "py",
                                f"/data/{os.path.basename(thrift_file)}",
                            ]

                            try:
                                subprocess.check_call(cmd)
                                print(
                                    f"Successfully generated Python code "
                                    f"for {thrift_file}")
                            except subprocess.CalledProcessError as e:
                                print(
                                    f"Error generating Python code for "
                                    f"{thrift_file}: {str(e)}")
                        else:
                            print(
                                f"Warning: Thrift file {thrift_file} "
                                "not found")

                    gen_py_dir = os.path.join(api_dir, "gen-py")
                    if os.path.exists(gen_py_dir):
                        if os.path.exists(os.path.join(
                                gen_py_dir, "codechecker_api_shared")):
                            copy_directory(
                                os.path.join(
                                    gen_py_dir, "codechecker_api_shared"),
                                py_api_shared_dir)

                        for item in os.listdir(gen_py_dir):
                            if (item != "codechecker_api_shared" and
                                    os.path.isdir(
                                        os.path.join(gen_py_dir, item))):
                                copy_directory(
                                    os.path.join(gen_py_dir, item), py_api_dir)

                        with change_directory(os.path.join(
                                api_py_dir, "codechecker_api_shared")):
                            subprocess.check_call(
                                [sys.executable, "setup.py", "sdist"])

                            for file in os.listdir(api_shared_dist):
                                if (file.startswith("codechecker_api_shared-")
                                        and file.endswith(".tar.gz")):
                                    os.rename(
                                        os.path.join(api_shared_dist, file),
                                        api_shared_tarball,
                                    )

                        with change_directory(os.path.join(
                                api_py_dir, "codechecker_api")):
                            subprocess.check_call(
                                [sys.executable, "setup.py", "sdist"])

                            for file in os.listdir(api_dist):
                                if (file.startswith("codechecker_api-") and
                                        file.endswith(".tar.gz")):
                                    os.rename(
                                        os.path.join(api_dist, file),
                                        api_tarball)

                        shutil.rmtree(gen_py_dir, ignore_errors=True)
                        print("Successfully built API packages")
                    else:
                        print(
                            f"Warning: Generated Python directory "
                            f"{gen_py_dir} not found")
            else:
                print("Warning: Docker is required to build the API packages.")
                print(
                    "The API packages are pre-built and committed to the "
                    "repository,")
                print(
                    "but they may be outdated if the Thrift files have "
                    "changed.")

        except Exception as e:
            handle_build_error(e, "API packages", error_mode)
    else:
        print("API packages already exist, skipping build.")


def build_web_frontend() -> None:
    """
    Build the Vue.js web frontend application.

    Runs npm install and npm build in the vue-cli directory. Uses git commit
    hash comparison to skip rebuilds when sources haven't changed. Copies
    the built assets to the generated files directory. Can be disabled via
    CC_BUILD_UI_DIST environment variable.
    """
    print("Building web frontend...")

    root_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(root_dir, "web")
    vue_cli_dir = os.path.join(web_dir, "server", "vue-cli")
    dist_dir = os.path.join(vue_cli_dir, "dist")

    web_dest_dir = os.path.join(GENERATED_FILES_DEST, DATA_FILES_DEST, "www")
    os.makedirs(web_dest_dir, exist_ok=True)

    build_ui_dist = os.environ.get(
        "CC_BUILD_UI_DIST", "YES")
    if build_ui_dist.upper() == "YES":
        error_mode = get_error_mode()
        try:
            print("Building Vue.js application...")

            if os.path.exists(dist_dir):
                latest_commit_file = os.path.join(dist_dir, ".build-commit")
                rebuild_needed = True

                if os.path.exists(latest_commit_file):
                    try:
                        latest_commit = subprocess.check_output(
                            [
                                "git",
                                "log",
                                "-n",
                                "1",
                                "--pretty=format:%H",
                                vue_cli_dir,
                            ],
                            stderr=subprocess.PIPE,
                            universal_newlines=True,
                        ).strip()

                        with open(latest_commit_file, "r") as f:
                            latest_build_commit = f.read().strip()

                        if latest_commit == latest_build_commit:
                            rebuild_needed = False
                            print(
                                "Vue.js application is up to date, "
                                "skipping build.")
                    except (subprocess.CalledProcessError, OSError, IOError):
                        pass

                if rebuild_needed:
                    shutil.rmtree(dist_dir)

            os.makedirs(dist_dir, exist_ok=True)

            package_json_path = os.path.join(vue_cli_dir, "package.json")
            if not os.path.exists(package_json_path):
                print(
                    "Warning: package.json not found in vue-cli directory. "
                    "Skipping Vue.js build.")
                print(
                    "This is expected when building from a source "
                    "distribution.")
                return

            with change_directory(vue_cli_dir):
                subprocess.check_call(["npm", "install"])
                subprocess.check_call(["npm", "run-script", "build"])

                try:
                    latest_commit = subprocess.check_output(
                        ["git", "log", "-n", "1", "--pretty=format:%H",
                         vue_cli_dir],
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                    ).strip()

                    with open(os.path.join(dist_dir, ".build-commit"),
                              "w") as f:
                        f.write(latest_commit)
                except (subprocess.CalledProcessError, OSError):
                    pass

            if os.path.exists(dist_dir):
                print(
                    f"Copying web frontend from {dist_dir} to "
                    f"{web_dest_dir}")
                copy_directory(dist_dir, web_dest_dir)
            else:
                print(
                    f"Warning: Vue.js build directory {dist_dir} does not "
                    "exist")

        except (subprocess.CalledProcessError, OSError) as e:
            handle_build_error(e, "web frontend", error_mode)
    else:
        print(
            "Skipping web frontend build as CC_BUILD_UI_DIST is not set "
            "to YES")


def add_git_info(version_json_data: Dict[str, Any]) -> None:
    """
    Add git repository information to version JSON data.

    Extracts git hash and git describe information (tag, dirty status) from
    the repository and adds them to the version JSON dictionary. Silently
    skips if not in a git repository or if git commands fail.

    Args:
        version_json_data: Dictionary to update with git information
    """
    try:
        if not os.path.exists(".git"):
            return

        try:
            git_hash = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], encoding="utf-8", errors="ignore"
            ).strip()
            version_json_data["git_hash"] = git_hash
        except subprocess.CalledProcessError:
            pass

        try:
            git_describe = subprocess.check_output(
                ["git", "describe", "--tags", "--dirty"],
                encoding="utf-8",
                errors="ignore",
            ).strip()

            git_describe_data = {}
            if "-dirty" in git_describe:
                git_describe_data["dirty"] = True
                git_describe = git_describe.replace("-dirty", "")
            else:
                git_describe_data["dirty"] = False

            if "-" in git_describe:
                tag = git_describe.split("-")[0]
            else:
                tag = git_describe

            git_describe_data["tag"] = tag
            version_json_data["git_describe"] = git_describe_data
        except subprocess.CalledProcessError:
            pass
    except Exception as e:
        print(f"Error adding git information: {str(e)}")


def extend_version_file(version_file: str) -> None:
    """
    Extend a version JSON file with build metadata.

    Reads the version file, adds git information and build date, then
    writes it back.

    Args:
        version_file: Path to the version JSON file to extend
    """
    if not os.path.exists(version_file):
        print(f"Warning: Version file not found: {version_file}")
        return

    try:
        with open(version_file, encoding="utf-8", errors="ignore") as v_file:
            version_json_data = json.load(v_file)

        add_git_info(version_json_data)

        time_now = time.strftime("%Y-%m-%dT%H:%M")
        version_json_data["package_build_date"] = time_now

        with open(version_file, "w", encoding="utf-8",
                  errors="ignore") as v_file:
            v_file.write(json.dumps(
                version_json_data, sort_keys=True, indent=4))

        print(f"Extended version file: {version_file}")
    except Exception as e:
        print(f"Error extending version file {version_file}: {str(e)}")


def ensure_version_defaults(version_file: str) -> None:
    """
    Ensure required keys exist in version file to avoid runtime errors.

    Adds default values for missing required fields. Currently ensures
    package_build_date exists with a default value if missing.

    Args:
        version_file: Path to the version JSON file to check/update
    """
    if not os.path.exists(version_file):
        return
    try:
        with open(version_file, encoding="utf-8", errors="ignore") as v_file:
            version_json_data = json.load(v_file)
        if "package_build_date" not in version_json_data:
            version_json_data["package_build_date"] = "1970-01-01T00:00"
            with open(version_file, "w", encoding="utf-8",
                      errors="ignore") as v_file:
                v_file.write(json.dumps(
                    version_json_data, sort_keys=True, indent=4))
    except Exception:
        pass


def extend_version_files() -> None:
    """
    Generate and extend version files for web and analyzer components.

    Copies source version files from web/config/ and analyzer/config/ to
    the generated config directory, ensures required keys exist, and
    extends them with build metadata (git info, build date) by default.
    Can be disabled by setting CC_SKIP_BUILD_META environment variable.
    """
    print("Extending version files with build date and git information...")

    config_files_path = os.path.join(DATA_FILES_DEST, "config")
    config_dir = os.path.join(GENERATED_FILES_DEST, config_files_path)
    os.makedirs(config_dir, exist_ok=True)

    web_version_file = os.path.join(config_dir, "web_version.json")
    src_web_version = os.path.join("web", "config", "web_version.json")
    if os.path.exists(src_web_version):
        shutil.copy(src_web_version, web_version_file)
        print(f"Copied {src_web_version} to {web_version_file}")
    else:
        print(f"Warning: Source file {src_web_version} not found")

    analyzer_version_file = os.path.join(
        config_dir, "analyzer_version.json")
    src_analyzer_version = os.path.join(
        "analyzer", "config", "analyzer_version.json")
    if os.path.exists(src_analyzer_version):
        shutil.copy(src_analyzer_version, analyzer_version_file)
        print(f"Copied {src_analyzer_version} to {analyzer_version_file}")
    else:
        print(f"Warning: Source file {src_analyzer_version} not found")

    ensure_version_defaults(web_version_file)
    ensure_version_defaults(analyzer_version_file)

    skip_build_meta = os.environ.get(
        "CC_SKIP_BUILD_META", "").upper() in ("YES", "1", "TRUE")
    if not skip_build_meta:
        extend_version_file(web_version_file)
        extend_version_file(analyzer_version_file)


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


class CustomBuildPy(build_py):
    """
    Custom build_py command that generates configuration files.

    Generates version files before building Python packages to ensure they
    are available during the build process.
    """
    def run(self) -> None:
        extend_version_files()
        build_py.run(self)


class CustomDevelop(develop):
    """
    Custom develop command for development mode installation.

    Ensures version files are generated and all build steps complete
    before creating development installation symlinks, so editable
    installs have all data files available.
    """
    def run(self) -> None:
        extend_version_files()

        build_cmd = self.get_finalized_command('build')
        if not self.distribution.data_files:
            build_cmd.run()
            build_cmd.collect_data_files()
            self.distribution.data_files = build_cmd.distribution.data_files

        develop.run(self)


class CustomBuild(build):
    """
    Custom build command for CodeChecker.

    Orchestrates all build steps: binary dependencies, API packages,
    web frontend, and data file collection. Replaces import-time
    execution and Makefile-based builds with proper setuptools command
    execution.
    """
    def run(self) -> None:
        extend_version_files()

        required_ok, optional_ok, messages = check_build_dependencies()
        for msg in messages:
            print(msg)

        if not required_ok:
            error_mode = get_error_mode()
            if error_mode == "strict":
                raise RuntimeError(
                    "Required build dependencies are missing. See "
                    "messages above.")
            else:
                print(
                    "WARNING: Continuing despite missing required "
                    "dependencies...")

        build_ldlogger_shared_libs()
        build_report_converter()
        build_tu_collector()
        build_statistics_collector()
        build_merge_clang_extdef_mappings()

        if (os.environ.get("CC_FORCE_BUILD_API_PACKAGES") or
                not has_prebuilt_api_packages()):
            build_api_packages()

        include_api_packages()
        build_web_frontend()
        self.collect_data_files()
        build.run(self)

    def collect_data_files(self) -> List[Tuple[str, List[str]]]:
        """
        Collect all data files and set them on the distribution.

        Combines static data files (docs, requirements, headers) with
        dynamic files generated during build (ldlogger .so files, web
        frontend assets, version files).

        Returns:
            List of tuples (target_directory, [list of source files])
        """
        all_data_files = get_static_data_files()
        all_data_files.extend(get_ldlogger_data_files())
        all_data_files.extend(get_version_data_files())
        all_data_files.extend(get_web_frontend_data_files())
        self.distribution.data_files = all_data_files
        return all_data_files


class BuildExt(build_ext):
    """
    Custom build_ext command for platform-specific extension building.

    Only builds extensions on Linux since ldlogger is Linux-only. Skips
    extension builds gracefully on other platforms.
    """
    def get_ext_filename(self, fullname: str) -> str:
        """
        Get the filename for an extension module.

        Returns a path with the machine architecture as a subdirectory,
        e.g., 'x86_64/codechecker_analyzer.ld_logger.lib.ldlogger.so'.

        Args:
            fullname: Full module name (e.g.,
                'codechecker_analyzer.ld_logger.lib.ldlogger')

        Returns:
            Path to the extension .so file
        """
        return os.path.join(platform.uname().machine, f"{fullname}.so")

    def build_extensions(self) -> None:
        """
        Build all extensions, skipping on non-Linux platforms.

        Clears the extension list on non-Linux platforms to prevent any
        processing attempts.
        """
        if sys.platform != "linux":
            print(
                f"Skipping all extension builds on {sys.platform} "
                "(Linux-only)")
            self.extensions = []
            return
        build_ext.build_extensions(self)

    def build_extension(self, ext: Extension) -> None:
        """
        Build a single extension, only on Linux.

        Args:
            ext: Extension object to build
        """
        if sys.platform == "linux":
            build_ext.build_extension(self, ext)
        else:
            print(f"Skipping extension build for {ext.name} on "
                  f"{sys.platform} (Linux-only)")

    def get_outputs(self) -> List[str]:
        """
        Get list of extension output files.

        Returns empty list on non-Linux platforms since no extensions
        are built there.

        Returns:
            List of output file paths
        """
        if sys.platform != "linux":
            return []
        return build_ext.get_outputs(self)


class CleanCommand(Command):
    """
    Custom clean command to remove build artifacts.

    Supports options:
    - --all: Remove all build artifacts (build/, dist/, *.egg-info/,
      generated files)
    - --build: Remove build/ directory
    - --generated: Remove build/__generated__/ directory
    - --dist: Remove dist/ directory and *.egg-info/ directories
    """
    description = "clean build artifacts"
    user_options = [
        ('all', 'a', 'remove all build artifacts'),
        ('build', 'b', 'remove build directory'),
        ('generated', 'g', 'remove generated files directory'),
        ('dist', 'd', 'remove dist directory'),
    ]

    def initialize_options(self) -> None:
        """Initialize command options to default values."""
        self.all = False
        self.build = False
        self.generated = False
        self.dist = False

    def finalize_options(self) -> None:
        """Finalize command options (no-op for this command)."""
        pass

    def run(self) -> None:
        """Execute the clean command based on selected options."""
        root_dir = os.path.dirname(os.path.abspath(__file__))

        if self.all:
            self.build = True
            self.generated = True
            self.dist = True

        if self.build:
            build_dir = os.path.join(root_dir, "build")
            if os.path.exists(build_dir):
                print(f"Removing build directory: {build_dir}")
                shutil.rmtree(build_dir)
            else:
                print("Build directory does not exist")

        if self.generated:
            generated_dir = os.path.join(root_dir, GENERATED_FILES_DEST)
            if os.path.exists(generated_dir):
                print(
                    f"Removing generated files directory: "
                    f"{generated_dir}")
                shutil.rmtree(generated_dir)
            else:
                print("Generated files directory does not exist")

        if self.dist:
            dist_dir = os.path.join(root_dir, "dist")
            if os.path.exists(dist_dir):
                print(f"Removing dist directory: {dist_dir}")
                shutil.rmtree(dist_dir)
            else:
                print("Dist directory does not exist")

            for item in os.listdir(root_dir):
                if item.endswith(".egg-info"):
                    egg_info_dir = os.path.join(root_dir, item)
                    if os.path.isdir(egg_info_dir):
                        print(f"Removing {item} directory: {egg_info_dir}")
                        shutil.rmtree(egg_info_dir)

        if not (self.build or self.generated or self.dist):
            print(
                "No clean options specified. Use --help to see available "
                "options.")


class StandalonePackageCommand(Command):
    """
    Custom command to create a standalone package with embedded virtual
    environment.

    Builds the package and wraps binaries in a virtual environment using
    wrap_binary_in_venv.py script, similar to the Makefile
    standalone_package target.
    """
    description = "create standalone package with embedded virtual environment"
    user_options = []

    def initialize_options(self) -> None:
        """Initialize command options (no-op for this command)."""
        pass

    def finalize_options(self) -> None:
        """Finalize command options (no-op for this command)."""
        pass

    def run(self) -> None:
        """Build package and wrap binaries in virtual environment."""
        root_dir = os.path.dirname(os.path.abspath(__file__))
        wrap_script = os.path.join(
            root_dir, "scripts", "build", "wrap_binary_in_venv.py")

        if not os.path.exists(wrap_script):
            print(
                f"Warning: wrap_binary_in_venv.py not found at "
                f"{wrap_script}")
            print("Skipping standalone package creation.")
            return

        print("Building package for standalone distribution...")
        self.run_command('build')
        self.run_command('sdist')

        print(
            "Creating standalone package with embedded virtual "
            "environment...")
        venv_dir = os.path.join(root_dir, "venv")
        package_dir = os.path.join(root_dir, "build_dist", "CodeChecker")

        try:
            subprocess.check_call(
                [sys.executable, wrap_script,
                 "-e", venv_dir,
                 "-o", package_dir],
                cwd=root_dir
            )
            print("Standalone package created successfully")
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"Warning: Failed to create standalone package: {e}")
            print("Continuing without standalone package wrapper...")


class Sdist(sdist):
    """
    Custom sdist command that ensures build completes before creating
    source distribution.

    Runs the build command to collect all data files before creating the
    source distribution archive.
    """
    def run(self) -> None:
        build_cmd = self.get_finalized_command('build')
        if not self.distribution.data_files:
            build_cmd.run()
            build_cmd.collect_data_files()
            self.distribution.data_files = build_cmd.distribution.data_files

        return sdist.run(self)


class Install(install):
    """
    Custom install command that ensures build completes before installation.

    Runs the build command to collect all data files before installing
    the package.
    """
    def run(self) -> None:
        build_cmd = self.get_finalized_command('build')
        if not self.distribution.data_files:
            build_cmd.run()
            build_cmd.collect_data_files()
            self.distribution.data_files = build_cmd.distribution.data_files

        return install.run(self)


with open(os.path.join("docs", "README.md"), "r",
          encoding="utf-8", errors="ignore") as fh:
    long_description = fh.read()

init_packages()

if not os.path.exists(lib_dir):
    os.makedirs(lib_dir, exist_ok=True)

package_dir_map = {
    "codechecker_common": "codechecker_common",
    "codechecker_analyzer": "analyzer/codechecker_analyzer",
    "codechecker_web": "web/codechecker_web",
    "codechecker_server": "web/server/codechecker_server",
    "codechecker_client": "web/client/codechecker_client",
    "codechecker_report_converter": (
        "tools/report-converter/codechecker_report_converter"),
    "codechecker_statistics_collector": (
        "analyzer/tools/statistics_collector/"
        "codechecker_statistics_collector"),
    "codechecker_merge_clang_extdef_mappings": (
        "analyzer/tools/merge_clang_extdef_mappings/"
        "codechecker_merge_clang_extdef_mappings"),
    "tu_collector": "tools/tu_collector/tu_collector",
}


# Note: Most metadata is defined in pyproject.toml.
# Only build-specific configuration is here.
setuptools.setup(
    packages=(packages if packages else setuptools.find_packages()),
    package_dir=(package_dir_map if packages else {}),
    data_files=[],
    include_package_data=True,

    install_requires=list(get_requirements()),

    ext_modules=([module_logger] if sys.platform == "linux" else []),
    cmdclass={
        'build': CustomBuild,
        'build_py': CustomBuildPy,
        'develop': CustomDevelop,
        'sdist': Sdist,
        'install': Install,
        'build_ext': BuildExt,
        'clean': CleanCommand,
        'standalone_package': StandalonePackageCommand,
    },

    scripts=[
        'scripts/gerrit_changed_files_to_skipfile.py'
    ],
    entry_points={
        'console_scripts': [
            'CodeChecker = codechecker_common.cli:main',
            ('merge-clang-extdef-mappings = '
             'codechecker_merge_clang_extdef_mappings.cli:main'),
            'post-process-stats = codechecker_statistics_collector.cli:main',
            'report-converter = codechecker_report_converter.cli:main',
            'tu_collector = tu_collector.tu_collector:main'
        ]
    },
)
