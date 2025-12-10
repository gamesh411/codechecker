#!/usr/bin/env python3

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


@contextmanager
def change_directory(directory):
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


def should_force_rebuild():
    """Check if force rebuild is requested via environment variable."""
    return os.environ.get("CC_FORCE_REBUILD", "NO").upper() == "YES"


def should_rebuild(output_path, source_paths):
    """
    Check if a build output needs to be rebuilt based on source file timestamps.
    
    Args:
        output_path: Path to the build output file or directory
        source_paths: List of source file/directory paths to check
    
    Returns:
        True if rebuild is needed, False if output is up to date
    """
    # If force rebuild is requested, always rebuild
    if should_force_rebuild():
        return True
    
    # If output doesn't exist, need to build
    if not os.path.exists(output_path):
        return True
    
    # Get output modification time
    try:
        if os.path.isdir(output_path):
            # For directories, check the most recent file modification time
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
        # If we can't get output time, rebuild to be safe
        return True
    
    # Check if any source file is newer than output
    for source_path in source_paths:
        if not os.path.exists(source_path):
            continue
        
        try:
            if os.path.isdir(source_path):
                # For directories, check the most recent file modification time
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
            # If we can't check a source file, rebuild to be safe
            return True
    
    # All sources are older than output, no rebuild needed
    return False
from setuptools import Command
from setuptools.command.build import build
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from setuptools.command.install import install
from setuptools.command.sdist import sdist
from setuptools.extension import Extension


# Error handling mode: "strict" (default) or "warn"
# In strict mode, build failures raise exceptions
# In warn mode, build failures print warnings and continue
def get_error_mode():
    """Get error handling mode from environment variable."""
    mode = os.environ.get("CC_BUILD_ERROR_MODE", "strict").lower()
    if mode not in ("strict", "warn"):
        print(f"Warning: Invalid CC_BUILD_ERROR_MODE '{mode}', using 'strict'")
        return "strict"
    return mode


def handle_build_error(error, component_name, error_mode=None):
    """
    Handle build errors according to the error mode.
    
    Args:
        error: The exception that occurred
        component_name: Name of the component that failed to build
        error_mode: Error mode ("strict" or "warn"), defaults to get_error_mode()
    
    Returns:
        None (if warn mode) or raises the error (if strict mode)
    """
    if error_mode is None:
        error_mode = get_error_mode()
    
    error_msg = f"Failed to build {component_name}: {error}"
    
    if error_mode == "strict":
        print(f"ERROR: {error_msg}")
        raise
    else:  # warn mode
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


def get_ldlogger_data_files():
    """
    Get ldlogger shared library data files that are generated during the build process.
    """
    data_files = []

    # ldlogger shared library files
    lib_dir_path = os.path.join(
        GENERATED_FILES_DEST, DATA_FILES_DEST, "ld_logger", "lib"
    )
    if os.path.exists(lib_dir_path):
        # Find all ldlogger.so files
        ldlogger_files = glob.glob(
            os.path.join(lib_dir_path, "**", "ldlogger.so"), recursive=True
        )
        if ldlogger_files:
            # Group files by their subdirectory structure
            for ldlogger_file in ldlogger_files:
                # Get the relative path from lib_dir_path
                rel_path = os.path.relpath(ldlogger_file, lib_dir_path)
                # Get the directory part (e.g., "64bit" or "")
                subdir = os.path.dirname(rel_path)
                if subdir:
                    # If there's a subdirectory, include it in the target path
                    target_dir = os.path.join(
                        DATA_FILES_DEST, "ld_logger", "lib", subdir
                    )
                else:
                    # If no subdirectory, place directly in lib
                    target_dir = os.path.join(DATA_FILES_DEST, "ld_logger", "lib")
                data_files.append((target_dir, [ldlogger_file]))

    return data_files


def get_web_frontend_data_files():
    """
    Get data files for web frontend assets.
    This function should only be called AFTER the web frontend has been built.
    """
    data_files = []

    # Web frontend files from the generated directory
    web_generated_www = os.path.join(GENERATED_FILES_DEST, DATA_FILES_DEST, "www")

    if os.path.exists(web_generated_www):
        for root, _, files in os.walk(web_generated_www):
            if files:
                # Filter out files that don't actually exist
                existing_files = [
                    f for f in files if os.path.exists(os.path.join(root, f))
                ]
                if existing_files:
                    rel_path = os.path.relpath(root, web_generated_www)
                    target_path = os.path.join(DATA_FILES_DEST, "www")
                    if rel_path != ".":
                        target_path = os.path.join(target_path, rel_path)
                    # Only include files that actually exist
                    file_paths = []
                    for f in existing_files:
                        file_path = os.path.join(root, f)
                        if os.path.exists(file_path) and os.path.isfile(file_path):
                            file_paths.append(file_path)
                    if file_paths:
                        data_files.append((target_path, file_paths))

    return data_files


def get_version_data_files():
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

    # Check if rebuild is needed (check all architectures at once)
    ldlogger_sources = [os.path.join(LD_LOGGER_SRC_PATH, s) for s in LD_LOGGER_SOURCES]
    output_64bit = os.path.join(lib_dest_dir, "64bit", "ldlogger.so")
    output_32bit = os.path.join(lib_dest_dir, "32bit", "ldlogger.so")
    
    # Support old env var name for backward compatibility
    build_64_bit_only = (
        os.environ.get("CC_BUILD_LOGGER_64_BIT_ONLY", "NO").upper() == "YES" or
        os.environ.get("BUILD_LOGGER_64_BIT_ONLY", "NO").upper() == "YES"
    )
    
    rebuild_64bit = should_rebuild(output_64bit, ldlogger_sources)
    rebuild_32bit = should_rebuild(output_32bit, ldlogger_sources) if not build_64_bit_only else False
    
    if not rebuild_64bit and not rebuild_32bit:
        print("ldlogger shared libraries are up to date, skipping build.")
        return

    class Arch(Enum):
        X86_64 = "64bit"
        X86_32 = "32bit"

    def build_ldlogger(arch: Arch):
        error_mode = get_error_mode()
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
            # For ldlogger, we always warn (it's optional for LD_PRELOAD)
            # but respect strict mode for other errors
            print(
                f"Warning: Failed to build ldlogger shared library for {arch.value}: {e}"
            )
            print("LD_PRELOAD functionality will not be available")
            if error_mode == "strict":
                # In strict mode, we still want to know about failures
                # but ldlogger is optional, so we don't fail the build
                pass
        except FileNotFoundError:
            # gcc not found is always a warning (optional component)
            print(
                f"Warning: gcc not found, skipping ldlogger shared library build for {arch.value}"
            )
            print("LD_PRELOAD functionality will not be available")

    if rebuild_64bit:
        build_ldlogger(Arch.X86_64)
    if rebuild_32bit:
        build_ldlogger(Arch.X86_32)


def build_report_converter():
    """Build and package report-converter."""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    report_converter_dir = os.path.join(root_dir, "tools", "report-converter")
    
    # Check if rebuild is needed
    build_dir = os.path.join(report_converter_dir, "build")
    source_files = []
    for root, _, files in os.walk(report_converter_dir):
        # Skip build directories and hidden files
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

        # Build report-converter using its setup.py
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=report_converter_dir
        )

        # The report-converter package will be included automatically by setuptools
        # since it's listed in get_codechecker_packages()
        print("Report-converter built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        handle_build_error(e, "report-converter", error_mode)


def build_tu_collector():
    """Build and package tu_collector."""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    tu_collector_dir = os.path.join(root_dir, "tools", "tu_collector")
    
    # Check if rebuild is needed
    build_dir = os.path.join(tu_collector_dir, "build")
    source_files = []
    for root, _, files in os.walk(tu_collector_dir):
        # Skip build directories and hidden files
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

        # Build tu_collector using its setup.py
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=tu_collector_dir
        )

        # The tu_collector package will be included automatically by setuptools
        # since it's listed in get_codechecker_packages()
        print("tu_collector built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        handle_build_error(e, "tu_collector", error_mode)


def build_statistics_collector():
    """Build and package statistics_collector."""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    statistics_collector_dir = os.path.join(
        root_dir, "analyzer", "tools", "statistics_collector"
    )
    
    # Check if rebuild is needed
    build_dir = os.path.join(statistics_collector_dir, "build")
    source_files = []
    for root, _, files in os.walk(statistics_collector_dir):
        # Skip build directories and hidden files
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

        # Build statistics_collector using its setup.py
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=statistics_collector_dir
        )

        # The statistics_collector package will be included automatically by setuptools
        # since it's listed in get_codechecker_packages()
        print("statistics_collector built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        handle_build_error(e, "statistics_collector", error_mode)


def build_merge_clang_extdef_mappings():
    """Build and package merge_clang_extdef_mappings."""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    merge_clang_dir = os.path.join(
        root_dir, "analyzer", "tools", "merge_clang_extdef_mappings"
    )
    
    # Check if rebuild is needed
    build_dir = os.path.join(merge_clang_dir, "build")
    source_files = []
    for root, _, files in os.walk(merge_clang_dir):
        # Skip build directories and hidden files
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

        # Build merge_clang_extdef_mappings using its setup.py
        subprocess.check_call(
            [sys.executable, "setup.py", "build"], cwd=merge_clang_dir
        )

        # The merge_clang_extdef_mappings package will be included automatically by setuptools
        # since it's listed in get_codechecker_packages()
        print("merge_clang_extdef_mappings built successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        handle_build_error(e, "merge_clang_extdef_mappings", error_mode)


def has_prebuilt_api_packages():
    """Check if prebuilt API tarballs exist."""
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
            "web", "api", "py", "codechecker_api", "dist", "codechecker_api.tar.gz"
        )
    )


def copy_directory(src, dst):
    """Copy all contents from src directory to dst directory."""
    if not os.path.exists(dst):
        os.makedirs(dst)

    for item in os.listdir(src):
        src_item = os.path.join(src, item)
        dst_item = os.path.join(dst, item)

        if os.path.isdir(src_item):
            copy_directory(src_item, dst_item)
        else:
            shutil.copy2(src_item, dst_item)


def include_api_packages():
    """If prebuilt API tarballs exist, extract them into build/lib.

    This avoids invoking Docker/Thrift/pip during install while ensuring
    codechecker_api and codechecker_api_shared imports work at runtime.
    """
    base_dir = os.path.abspath(os.path.dirname(__file__))
    api_dir = os.path.join(base_dir, "web", "api", "py")
    api_shared_tarball = os.path.join(
        api_dir, "codechecker_api_shared", "dist", "codechecker_api_shared.tar.gz"
    )
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
                # Extract to a temp dir first
                tmp_dir = os.path.join(
                    base_dir, "build", "__api_extract__", package_name
                )
                shutil.rmtree(tmp_dir, ignore_errors=True)
                os.makedirs(tmp_dir, exist_ok=True)
                tf.extractall(tmp_dir)

                # Find top-level package dir in the extracted sdist
                src_pkg_dir = None
                for root, dirs, files in os.walk(tmp_dir):
                    if (
                        os.path.basename(root) == package_name
                        and "__init__.py" in files
                    ):
                        src_pkg_dir = root
                        break
                if not src_pkg_dir:
                    print(f"Warning: Could not locate {package_name} in {tar_path}")
                    return

                dst_pkg_dir = os.path.join(build_lib, package_name)
                shutil.rmtree(dst_pkg_dir, ignore_errors=True)
                shutil.copytree(src_pkg_dir, dst_pkg_dir)
                print(f"Included prebuilt {package_name} package from {tar_path}")
        except Exception as e:
            print(f"Warning: Failed to include {package_name} from {tar_path}: {e}")

    extract_package(api_shared_tarball, "codechecker_api_shared")
    extract_package(api_tarball, "codechecker_api")


def build_api_packages():
    """Build the API packages if they don't exist."""
    print("Checking and building API packages if needed...")

    # Define paths
    api_dir = os.path.join("web", "api")
    api_py_dir = os.path.join(api_dir, "py")
    api_shared_dist = os.path.join(api_py_dir, "codechecker_api_shared", "dist")
    api_dist = os.path.join(api_py_dir, "codechecker_api", "dist")

    # Check if the API packages already exist
    api_shared_tarball = os.path.join(api_shared_dist, "codechecker_api_shared.tar.gz")
    api_tarball = os.path.join(api_dist, "codechecker_api.tar.gz")

    # Check if rebuild is needed based on Thrift file timestamps
    thrift_files = [
        os.path.join(api_dir, "authentication.thrift"),
        os.path.join(api_dir, "products.thrift"),
        os.path.join(api_dir, "report_server.thrift"),
        os.path.join(api_dir, "configuration.thrift"),
        os.path.join(api_dir, "server_info.thrift"),
        os.path.join(api_dir, "codechecker_api_shared.thrift"),
    ]
    
    # Filter to only existing thrift files
    existing_thrift_files = [f for f in thrift_files if os.path.exists(f)]
    
    need_build = False
    if not os.path.exists(api_shared_tarball) or not os.path.exists(api_tarball):
        need_build = True
        print("API packages not found, building them...")
    elif existing_thrift_files:
        # Check if any Thrift file is newer than the tarballs
        if should_rebuild(api_shared_tarball, existing_thrift_files) or \
           should_rebuild(api_tarball, existing_thrift_files):
            need_build = True
            print("API packages are outdated, rebuilding them...")

    if need_build:
        error_mode = get_error_mode()
        try:
            # Create directories for generated files if they don't exist
            py_api_dir = os.path.join(api_py_dir, "codechecker_api", "codechecker_api")
            py_api_shared_dir = os.path.join(
                api_py_dir, "codechecker_api_shared", "codechecker_api_shared"
            )

            os.makedirs(py_api_dir, exist_ok=True)
            os.makedirs(py_api_shared_dir, exist_ok=True)
            os.makedirs(api_shared_dist, exist_ok=True)
            os.makedirs(api_dist, exist_ok=True)

            # Check if we have Docker for building the API packages
            try:
                subprocess.check_output(
                    ["docker", "--version"], encoding="utf-8", errors="ignore"
                )
                has_docker = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                has_docker = False

            if has_docker:
                print("Building API packages using Docker...")

                # These are the Thrift files that need to be processed
                thrift_files = [
                    os.path.join(api_dir, "authentication.thrift"),
                    os.path.join(api_dir, "products.thrift"),
                    os.path.join(api_dir, "report_server.thrift"),
                    os.path.join(api_dir, "configuration.thrift"),
                    os.path.join(api_dir, "server_info.thrift"),
                    os.path.join(api_dir, "codechecker_api_shared.thrift"),
                ]

                # Create a temporary directory for the generated files
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Run Docker to generate the Thrift files
                    for thrift_file in thrift_files:
                        if os.path.exists(thrift_file):
                            print(f"Processing {thrift_file}...")
                            # Get the current user ID and group ID
                            uid = os.getuid() if hasattr(os, "getuid") else 1000
                            gid = os.getgid() if hasattr(os, "getgid") else 1000

                            # Run Thrift in Docker to generate Python code
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
                                subprocess.check_call(
                                    cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                )
                                print(
                                    f"Successfully generated Python code for {thrift_file}"
                                )
                            except subprocess.CalledProcessError as e:
                                print(
                                    f"Error generating Python code for {thrift_file}: {str(e)}"
                                )
                        else:
                            print(f"Warning: Thrift file {thrift_file} not found")

                    # Copy the generated files to their destinations
                    gen_py_dir = os.path.join(api_dir, "gen-py")
                    if os.path.exists(gen_py_dir):
                        # Copy codechecker_api_shared files
                        if os.path.exists(
                            os.path.join(gen_py_dir, "codechecker_api_shared")
                        ):
                            copy_directory(
                                os.path.join(gen_py_dir, "codechecker_api_shared"),
                                py_api_shared_dir,
                            )

                        # Copy all other API files
                        for item in os.listdir(gen_py_dir):
                            if item != "codechecker_api_shared" and os.path.isdir(
                                os.path.join(gen_py_dir, item)
                            ):
                                copy_directory(
                                    os.path.join(gen_py_dir, item), py_api_dir
                                )

                        # Build the packages
                        # Build codechecker_api_shared
                        with change_directory(os.path.join(api_py_dir, "codechecker_api_shared")):
                            subprocess.check_call([sys.executable, "setup.py", "sdist"])

                            # Rename the tarball
                            for file in os.listdir(api_shared_dist):
                                if file.startswith(
                                    "codechecker_api_shared-"
                                ) and file.endswith(".tar.gz"):
                                    os.rename(
                                        os.path.join(api_shared_dist, file),
                                        api_shared_tarball,
                                    )

                        # Build codechecker_api
                        with change_directory(os.path.join(api_py_dir, "codechecker_api")):
                            subprocess.check_call([sys.executable, "setup.py", "sdist"])

                            # Rename the tarball
                            for file in os.listdir(api_dist):
                                if file.startswith("codechecker_api-") and file.endswith(
                                    ".tar.gz"
                                ):
                                    os.rename(os.path.join(api_dist, file), api_tarball)

                        # Clean up generated files
                        shutil.rmtree(gen_py_dir, ignore_errors=True)

                        print("Successfully built API packages")
                    else:
                        print(
                            f"Warning: Generated Python directory {gen_py_dir} not found"
                        )
            else:
                # Inform the user that Docker is required
                print("Warning: Docker is required to build the API packages.")
                print("The API packages are pre-built and committed to the repository,")
                print("but they may be outdated if the Thrift files have changed.")

        except Exception as e:
            handle_build_error(e, "API packages", error_mode)
    else:
        print("API packages already exist, skipping build.")


def build_web_frontend():
    """Build the web frontend."""
    print("Building web frontend...")

    # Define paths
    root_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(root_dir, "web")
    vue_cli_dir = os.path.join(web_dir, "server", "vue-cli")
    dist_dir = os.path.join(vue_cli_dir, "dist")

    # Define destination path in the generated files directory
    web_dest_dir = os.path.join(GENERATED_FILES_DEST, DATA_FILES_DEST, "www")
    # Create even if we don't build the web frontend
    os.makedirs(web_dest_dir, exist_ok=True)

    # Check if we should build the UI
    # Support both old and new env var names for backward compatibility
    build_ui_dist = (
        os.environ.get("CC_BUILD_UI_DIST", os.environ.get("BUILD_UI_DIST", "YES"))
    )

    if build_ui_dist.upper() == "YES":
        # Build the Vue.js application
        error_mode = get_error_mode()
        try:
            print("Building Vue.js application...")

            # Check if the dist directory already exists and is up to date
            if os.path.exists(dist_dir):
                # Check if we need to rebuild based on latest commit
                latest_commit_file = os.path.join(dist_dir, ".build-commit")
                rebuild_needed = True

                if os.path.exists(latest_commit_file):
                    try:
                        # Try to get the latest commit in which vue-cli directory was changed
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

                        # Get the latest build commit from the file
                        with open(latest_commit_file, "r") as f:
                            latest_build_commit = f.read().strip()

                        # If they match, no need to rebuild
                        if latest_commit == latest_build_commit:
                            rebuild_needed = False
                            print("Vue.js application is up to date, skipping build.")
                    except (subprocess.CalledProcessError, OSError, IOError):
                        # If any error occurs, we'll rebuild to be safe
                        pass

                if rebuild_needed:
                    # Remove existing dist directory to ensure clean build
                    shutil.rmtree(dist_dir)

            # Create dist directory if it doesn't exist
            os.makedirs(dist_dir, exist_ok=True)

            # Check if package.json exists (needed for npm commands)
            package_json_path = os.path.join(vue_cli_dir, "package.json")
            if not os.path.exists(package_json_path):
                print(
                    "Warning: package.json not found in vue-cli directory. Skipping Vue.js build."
                )
                print("This is expected when building from a source distribution.")
                return

            # Change to vue-cli directory using context manager
            with change_directory(vue_cli_dir):
                # Run npm install and build
                subprocess.check_call(["npm", "install"])
                subprocess.check_call(["npm", "run-script", "build"])

                # Save the latest commit hash to the build-commit file
                try:
                    latest_commit = subprocess.check_output(
                        ["git", "log", "-n", "1", "--pretty=format:%H", vue_cli_dir],
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                    ).strip()

                    with open(os.path.join(dist_dir, ".build-commit"), "w") as f:
                        f.write(latest_commit)
                except (subprocess.CalledProcessError, OSError):
                    pass

            # Copy the built files to the generated files directory
            if os.path.exists(dist_dir):
                print(f"Copying web frontend from {dist_dir} to {web_dest_dir}")
                copy_directory(dist_dir, web_dest_dir)
            else:
                print(f"Warning: Vue.js build directory {dist_dir} does not exist")

        except (subprocess.CalledProcessError, OSError) as e:
            handle_build_error(e, "web frontend", error_mode)
    else:
        print("Skipping web frontend build as BUILD_UI_DIST is not set to YES")


def add_git_info(version_json_data):
    """Add git information to version data if available."""
    try:
        if not os.path.exists(".git"):
            return

        # Get git hash
        try:
            git_hash = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], encoding="utf-8", errors="ignore"
            ).strip()
            version_json_data["git_hash"] = git_hash
        except subprocess.CalledProcessError:
            pass

        # Get git describe information
        try:
            git_describe = subprocess.check_output(
                ["git", "describe", "--tags", "--dirty"],
                encoding="utf-8",
                errors="ignore",
            ).strip()

            # Parse git describe output
            git_describe_data = {}
            if "-dirty" in git_describe:
                git_describe_data["dirty"] = True
                git_describe = git_describe.replace("-dirty", "")
            else:
                git_describe_data["dirty"] = False

            # Extract tag information
            if "-" in git_describe:
                tag = git_describe.split("-")[0]
            else:
                tag = git_describe

            git_describe_data["tag"] = tag
            version_json_data["git_describe"] = git_describe_data
        except subprocess.CalledProcessError:
            # No tags available
            pass
    except Exception as e:
        print(f"Error adding git information: {str(e)}")


def extend_version_file(version_file):
    """Extend a version file with build date and git information."""
    if not os.path.exists(version_file):
        print(f"Warning: Version file not found: {version_file}")
        return

    try:
        with open(version_file, encoding="utf-8", errors="ignore") as v_file:
            version_json_data = json.load(v_file)

        # Add git information if available
        add_git_info(version_json_data)

        # Add build date
        time_now = time.strftime("%Y-%m-%dT%H:%M")
        version_json_data["package_build_date"] = time_now

        # Rewrite version config file with the extended data
        with open(version_file, "w", encoding="utf-8", errors="ignore") as v_file:
            v_file.write(json.dumps(version_json_data, sort_keys=True, indent=4))

        print(f"Extended version file: {version_file}")
    except Exception as e:
        print(f"Error extending version file {version_file}: {str(e)}")


def ensure_version_defaults(version_file):
    """Ensure required keys exist to avoid runtime errors."""
    if not os.path.exists(version_file):
        return
    try:
        with open(version_file, encoding="utf-8", errors="ignore") as v_file:
            version_json_data = json.load(v_file)
        if "package_build_date" not in version_json_data:
            version_json_data["package_build_date"] = "1970-01-01T00:00"
            with open(version_file, "w", encoding="utf-8", errors="ignore") as v_file:
                v_file.write(json.dumps(version_json_data, sort_keys=True, indent=4))
    except Exception:
        pass


def extend_version_files():
    """Extend version files with build date and git information."""
    print("Extending version files with build date and git information...")

    # Ensure the config directory exists
    config_files_path = os.path.join(DATA_FILES_DEST, "config")
    config_dir = os.path.join(GENERATED_FILES_DEST, config_files_path)
    os.makedirs(config_dir, exist_ok=True)

    # Process web_version.json
    web_version_file = os.path.join(config_dir, "web_version.json")

    # Always copy the source version file to ensure we have the latest version
    src_web_version = os.path.join("web", "config", "web_version.json")
    if os.path.exists(src_web_version):
        shutil.copy(src_web_version, web_version_file)
        print(f"Copied {src_web_version} to {web_version_file}")
    else:
        print(f"Warning: Source file {src_web_version} not found")

    # Process analyzer_version.json
    analyzer_version_file = os.path.join(config_dir, "analyzer_version.json")

    # Always copy the source version file to ensure we have the latest version
    src_analyzer_version = os.path.join("analyzer", "config", "analyzer_version.json")
    if os.path.exists(src_analyzer_version):
        shutil.copy(src_analyzer_version, analyzer_version_file)
        print(f"Copied {src_analyzer_version} to {analyzer_version_file}")
    else:
        print(f"Warning: Source file {src_analyzer_version} not found")

    # Ensure required keys exist for runtime even if build metadata is off
    ensure_version_defaults(web_version_file)
    ensure_version_defaults(analyzer_version_file)

    # Optionally extend both version files with build metadata if enabled
    if os.environ.get("CC_EMBED_BUILD_META"):
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
    
    This class handles generation of version files and other configuration
    files that need to be created before packaging.
    """
    def run(self):
        # Generate version files before building Python packages
        extend_version_files()
        
        # Continue with standard build_py
        build_py.run(self)


class CustomBuild(build):
    """
    Custom build command for CodeChecker.
    
    This class handles all build steps (binary dependencies, API packages,
    web frontend, etc.) that previously happened at import time or via Makefile.
    """
    def collect_data_files(self):
        """
        Collect all data files (static + dynamic) and set them on the distribution.
        
        This method collects:
        - Static data files (docs, requirements, headers)
        - Dynamic data files (ldlogger .so, web frontend, version files)
        """
        # Start with static data files
        all_data_files = get_static_data_files()
        
        # Add ldlogger shared library files (if built)
        all_data_files.extend(get_ldlogger_data_files())
        
        # Add version files (generated by CustomBuildPy)
        all_data_files.extend(get_version_data_files())
        
        # Add web frontend files (if built)
        all_data_files.extend(get_web_frontend_data_files())
        
        # Set data files on the distribution
        self.distribution.data_files = all_data_files
        
        return all_data_files
    
    def run(self):
        # Build binary dependencies first
        build_ldlogger_shared_libs()
        build_report_converter()
        build_tu_collector()
        build_statistics_collector()
        build_merge_clang_extdef_mappings()
        
        # Build API packages if needed
        if os.environ.get("CC_FORCE_BUILD_API_PACKAGES") or not has_prebuilt_api_packages():
            build_api_packages()
        
        # Include API packages (extract prebuilt tarballs to build/lib)
        include_api_packages()
        
        # Build web frontend
        build_web_frontend()
        
        # Collect all data files (static + dynamic) after building
        self.collect_data_files()
        
        # Continue with standard build
        build.run(self)


class BuildExt(build_ext):
    def get_ext_filename(self, ext_name):
        return os.path.join(platform.uname().machine, f"{ext_name}.so")

    def build_extension(self, ext):
        if sys.platform == "linux":
            build_ext.build_extension(self, ext)


class CleanCommand(Command):
    """
    Custom clean command to remove build artifacts.
    
    Supports options:
    - --all: Remove all build artifacts (build/, dist/, *.egg-info/, generated files)
    - --build: Remove build/ directory
    - --generated: Remove build/__generated__/ directory
    - --dist: Remove dist/ directory
    """
    description = "clean build artifacts"
    user_options = [
        ('all', 'a', 'remove all build artifacts'),
        ('build', 'b', 'remove build directory'),
        ('generated', 'g', 'remove generated files directory'),
        ('dist', 'd', 'remove dist directory'),
    ]

    def initialize_options(self):
        self.all = False
        self.build = False
        self.generated = False
        self.dist = False

    def finalize_options(self):
        pass

    def run(self):
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
                print(f"Removing generated files directory: {generated_dir}")
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
            
            # Also remove egg-info directories
            for item in os.listdir(root_dir):
                if item.endswith(".egg-info"):
                    egg_info_dir = os.path.join(root_dir, item)
                    if os.path.isdir(egg_info_dir):
                        print(f"Removing {item} directory: {egg_info_dir}")
                        shutil.rmtree(egg_info_dir)
        
        if not (self.build or self.generated or self.dist):
            print("No clean options specified. Use --help to see available options.")


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
    data_files=[],  # Will be populated by CustomBuild.collect_data_files()
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
        'build_py': CustomBuildPy,
        'sdist': Sdist,
        'install': Install,
        'build_ext': BuildExt,
        'clean': CleanCommand,
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
