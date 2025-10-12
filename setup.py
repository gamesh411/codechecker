#!/usr/bin/env python3

import os
import sys
import setuptools
import sys
import tarfile
import shutil
import glob
import json
import time
import subprocess
import shutil
import os.path
import tempfile

from enum import Enum

REQ_FILE_PATHS = [
    os.path.join("analyzer", "requirements.txt"),
    os.path.join("web", "requirements.txt"),
]

LD_LOGGER_SRC_PATH = os.path.join("analyzer", "tools", "build-logger", "src")

LD_LOGGER_SOURCES = [
    "ldlogger-hooks.c",
    "ldlogger-logger.c",
    "ldlogger-tool.c",
    "ldlogger-tool-gcc.c",
    "ldlogger-tool-javac.c",
    "ldlogger-util.c",
]

LD_LOGGER_INCLUDES = ["ldlogger-hooks.h", "ldlogger-tool.h", "ldlogger-util.h"]

DATA_FILES_DEST = os.path.join("share", "codechecker")
CONFIG_FILES_PATH = os.path.join(DATA_FILES_DEST, "config")
GENERATED_FILES_DEST = os.path.join("build", "__generated__")


def get_long_description():
    with open(
        os.path.join("docs", "README.md"), "r", encoding="utf-8", errors="ignore"
    ) as fh:
        return fh.read()


def get_codechecker_packages():
    package_roots = [
        ".",  # codechecker_common
        "analyzer",  # codechecker_analyzer
        "web",  # codechecker_web
        "web/server",  # codechecker_server
        "web/client",  # codechecker_client
        "tools/tu_collector",  # tu_collector
        "tools/report-converter",  # codechecker_report_converter
        "analyzer/tools/statistics_collector",  # codechecker_statistics_collector
        "analyzer/tools/merge_clang_extdef_mappings",  # codechecker_merge_clang_extdef_mappings
        "web/api/py",  # codechecker_api, codechecker_api_shared
    ]
    return [
        package_name
        for package_list in map(setuptools.find_packages, package_roots)
        for package_name in package_list
    ]


def get_requirements():
    """Get install requirements."""
    requirements = set()
    for req_file_path in REQ_FILE_PATHS:
        with open(req_file_path, "r") as f:
            requirements.update(
                [
                    s
                    for s in [line.split("#", 1)[0].strip(" \t\n") for line in f]
                    if s and "codechecker" not in s
                ]
            )

    return list(requirements)


def discover_config_files(config_dir_path: str):
    """Discover all config files recursively and create data_files entries.

    Args:
        config_dir_path: Path to the config directory

    Returns:
        List of tuples (target_dir, [file_paths]) for data_files
    """
    data_files = []

    for file_path in glob.glob(os.path.join(config_dir_path, "**/*"), recursive=True):
        if os.path.isfile(file_path):
            # Create relative path from config dir
            rel_path = os.path.relpath(file_path, config_dir_path)
            # Determine target directory
            target_dir = os.path.join(CONFIG_FILES_PATH, *os.path.split(rel_path)[:-1])

            # Add file to data_files
            data_files.append((target_dir, [file_path]))

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


def get_static_data_files():
    """
    This function returns the list of static data files that don't require building.
    """
    data_files = []

    # docs
    data_files.extend(
        [
            (
                os.path.join(DATA_FILES_DEST, "docs"),
                [os.path.join("docs", "README.md")],
            ),
            *map(lambda p: (os.path.join(DATA_FILES_DEST, p), [p]), REQ_FILE_PATHS),
        ]
    )

    # config - explicitly include all config files
    data_files.extend(discover_config_files("config"))

    # web/config - include web-specific config files
    data_files.extend(discover_config_files("web/config"))

    # server/config - include server-specific config files
    data_files.extend(discover_config_files("web/server/config"))

    # Version files and commands.json
    # These files are generated during the build process
    # Make sure they're included in the package
    cmds_json_path = os.path.join(
        GENERATED_FILES_DEST, CONFIG_FILES_PATH, "commands.json"
    )

    config_files = [
        os.path.join(GENERATED_FILES_DEST, CONFIG_FILES_PATH, "analyzer_version.json"),
        os.path.join(GENERATED_FILES_DEST, CONFIG_FILES_PATH, "web_version.json"),
    ]

    if os.environ.get("CC_EMBED_SUBCOMMANDS_JSON"):
        config_files.append(cmds_json_path)

    data_files.append(
        (
            CONFIG_FILES_PATH,
            config_files,
        )
    )

    # Web frontend files - these will be added by custom commands after building
    # This function only returns the static data files that don't require building

    # ld logger header
    # TODO: do we need to copy the header files?
    data_files.append(
        (
            os.path.join(DATA_FILES_DEST, "ld_logger", "include"),
            [os.path.join(LD_LOGGER_SRC_PATH, i) for i in LD_LOGGER_INCLUDES],
        )
    )

    # Prebuilt API sdists (optional): include if present so users can
    # install them explicitly with codechecker-install-api
    api_shared_sdist = os.path.join(
        "web",
        "api",
        "py",
        "codechecker_api_shared",
        "dist",
        "codechecker_api_shared.tar.gz",
    )
    api_sdist = os.path.join(
        "web", "api", "py", "codechecker_api", "dist", "codechecker_api.tar.gz"
    )
    for sdist_path in [api_shared_sdist, api_sdist]:
        if os.path.exists(sdist_path):
            target_dir = os.path.join(
                DATA_FILES_DEST,
                "web",
                "api",
                "py",
                os.path.basename(os.path.dirname(os.path.dirname(sdist_path))),
                "dist",
            )
            data_files.append((target_dir, [sdist_path]))

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
    if os.environ.get("BUILD_LOGGER_64_BIT_ONLY", "NO") == "NO":
        build_ldlogger(Arch.X86_32)


def generate_subcommands_json():
    """Generate commands.json file by collecting all CLI commands."""

    # Create config directory if it doesn't exist
    config_dir = os.path.join(GENERATED_FILES_DEST, CONFIG_FILES_PATH)
    os.makedirs(config_dir, exist_ok=True)

    # Define command directories to scan
    cmd_dirs = [
        os.path.join("codechecker_common", "cli_commands"),
        os.path.join("analyzer", "codechecker_analyzer", "cli"),
        os.path.join("web", "codechecker_web", "cli"),
        os.path.join("web", "server", "codechecker_server", "cli"),
        os.path.join("web", "client", "codechecker_client", "cli"),
    ]

    # Collect subcommands
    subcmds = {}
    for cmd_dir in cmd_dirs:
        if not os.path.exists(cmd_dir):
            continue

        for cmd_file in glob.glob(os.path.join(cmd_dir, "*.py")):
            cmd_file_name = os.path.basename(cmd_file)
            # Exclude files like __init__.py or __pycache__
            if "__" not in cmd_file_name:
                # [:-3] removes '.py' extension
                subcmds[cmd_file_name[:-3].replace("_", "-")] = os.path.join(
                    *cmd_file.split(os.sep)[-3:]
                )

    # Write commands.json
    commands_json_path = os.path.join(config_dir, "commands.json")
    with open(commands_json_path, "w", encoding="utf-8", errors="ignore") as f:
        json.dump(subcmds, f, sort_keys=True, indent=2)

    print(f"Generated commands.json at {commands_json_path}")


def extend_version_files():
    """Extend version files with build date and git information."""

    print("Extending version files with build date and git information...")

    # Ensure the config directory exists
    config_dir = os.path.join(GENERATED_FILES_DEST, CONFIG_FILES_PATH)
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


def add_git_info(version_json_data):
    """Add git information to version data if available."""
    import subprocess

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
    import json

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


def include_api_packages():
    """If prebuilt API tarballs exist, extract them into build/lib.

    This function does not invoke Docker/Thrift/pip, only extracts the
    tarballs and ensures the imports of codechecker_api and
    codechecker_api_shared work at runtime.
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

    need_build = False

    if not os.path.exists(api_shared_tarball) or not os.path.exists(api_tarball):
        need_build = True
        print("API packages not found, building them...")

    if need_build:
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
                        os.chdir(os.path.join(api_py_dir, "codechecker_api_shared"))
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
                        os.chdir(os.path.join(api_py_dir, "codechecker_api"))
                        subprocess.check_call([sys.executable, "setup.py", "sdist"])

                        # Rename the tarball
                        for file in os.listdir(api_dist):
                            if file.startswith("codechecker_api-") and file.endswith(
                                ".tar.gz"
                            ):
                                os.rename(os.path.join(api_dist, file), api_tarball)

                        # Return to the original directory
                        os.chdir(os.path.dirname(os.path.abspath(__file__)))

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

            # Ensure the API packages are available for installation
            if os.path.exists(api_shared_tarball) and os.path.exists(api_tarball):
                # Copy the API packages to the build directory
                build_lib_dir = os.path.join("build", "lib")
                if os.path.exists(build_lib_dir):
                    # Create the destination directories
                    os.makedirs(
                        os.path.join(
                            build_lib_dir,
                            "web",
                            "api",
                            "py",
                            "codechecker_api",
                            "dist",
                        ),
                        exist_ok=True,
                    )
                    os.makedirs(
                        os.path.join(
                            build_lib_dir,
                            "web",
                            "api",
                            "py",
                            "codechecker_api_shared",
                            "dist",
                        ),
                        exist_ok=True,
                    )

                    # Copy the API packages
                    shutil.copy(
                        api_tarball,
                        os.path.join(
                            build_lib_dir,
                            "web",
                            "api",
                            "py",
                            "codechecker_api",
                            "dist",
                        ),
                    )
                    shutil.copy(
                        api_shared_tarball,
                        os.path.join(
                            build_lib_dir,
                            "web",
                            "api",
                            "py",
                            "codechecker_api_shared",
                            "dist",
                        ),
                    )

                    print("API packages copied to build directory.")
                else:
                    print(
                        "Warning: build/lib directory not found, API packages not copied."
                    )
            else:
                print("Warning: API packages not found after build attempt.")

        except Exception as e:
            print(f"Error building API packages: {str(e)}")
            print("Continuing with installation, but some features may not work.")
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
    build_ui_dist = os.environ.get("BUILD_UI_DIST", "YES")

    if build_ui_dist.upper() == "YES":
        # Build the Vue.js application
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

            # Save current directory to return to it later
            current_dir = os.getcwd()

            # Check if package.json exists (needed for npm commands)
            package_json_path = os.path.join(vue_cli_dir, "package.json")
            if not os.path.exists(package_json_path):
                print(
                    "Warning: package.json not found in vue-cli directory. Skipping Vue.js build."
                )
                print("This is expected when building from a source distribution.")
                return

            # Change to vue-cli directory
            os.chdir(vue_cli_dir)

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

            # Return to original directory
            os.chdir(current_dir)

            # Copy the built files to the generated files directory
            if os.path.exists(dist_dir):
                print(f"Copying web frontend from {dist_dir} to {web_dest_dir}")
                copy_directory(dist_dir, web_dest_dir)
            else:
                print(f"Warning: Vue.js build directory {dist_dir} does not exist")

        except (subprocess.CalledProcessError, OSError) as e:
            print(f"Warning: Failed to build Vue.js application: {e}")
            print("Continuing with installation without web frontend...")
    else:
        print("Skipping web frontend build as BUILD_UI_DIST is not set to YES")


def build_report_converter():
    """Build and package report-converter."""

    root_dir = os.path.dirname(os.path.abspath(__file__))
    # Build and package report-converter
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


def build_and_generate_all_data_files():
    """
    This function returns all data files including dynamically generated ones.
    It builds the necessary components and then collects all data files.
    """
    if os.environ.get("CC_EMBED_SUBCOMMANDS_JSON"):
        generate_subcommands_json()

    build_ldlogger_shared_libs()
    extend_version_files()

    if os.environ.get("CC_FORCE_BUILD_API_PACKAGES") or not has_prebuilt_api_packages():
        build_api_packages()

    include_api_packages()
    build_web_frontend()
    build_report_converter()

    static_data_files = get_static_data_files()
    ldlogger_data_files = get_ldlogger_data_files()
    web_frontend_data_files = get_web_frontend_data_files()

    return static_data_files + ldlogger_data_files + web_frontend_data_files


setuptools.setup(
    name="codechecker",
    version="6.27.0",
    author="CodeChecker Team (Ericsson)",
    author_email="codechecker-tool@googlegroups.com",
    description="CodeChecker is an analyzer tooling, defect database and "
    "viewer extension",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    url="https://github.com/Ericsson/CodeChecker",
    project_urls={
        "Documentation": "http://codechecker.readthedocs.io",
        "Issue Tracker": "http://github.com/Ericsson/CodeChecker/issues",
    },
    keywords=["codechecker", "plist", "sarif"],
    license="Apache-2.0 WITH LLVM-exception",
    packages=get_codechecker_packages(),
    package_dir={
        "codechecker_analyzer": "analyzer/codechecker_analyzer/",
        "codechecker_web": "web/codechecker_web/",
        "codechecker_client": "web/client/codechecker_client/",
        "codechecker_server": "web/server/codechecker_server/",
        "tu_collector": "tools/tu_collector/tu_collector/",
        "codechecker_report_converter": "tools/report-converter/codechecker_report_converter/",
        "codechecker_statistics_collector": "analyzer/tools/statistics_collector/codechecker_statistics_collector/",
        "codechecker_merge_clang_extdef_mappings": "analyzer/tools/merge_clang_extdef_mappings/codechecker_merge_clang_extdef_mappings/",
        "codechecker_api": "web/api/py/codechecker_api/",
        "codechecker_api_shared": "web/api/py/codechecker_api_shared/",
    },
    data_files=build_and_generate_all_data_files(),
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
    install_requires=get_requirements(),
    python_requires=">=3.9",
    scripts=["scripts/gerrit_changed_files_to_skipfile.py"],
    entry_points={
        "console_scripts": [
            "CodeChecker = codechecker_common.cli:main",
            (
                "merge-clang-extdef-mappings = "
                "codechecker_merge_clang_extdef_mappings.cli:main"
            ),
            "post-process-stats = codechecker_statistics_collector.cli:main",
            "report-converter = codechecker_report_converter.cli:main",
            "tu_collector = tu_collector.tu_collector:main",
            "codechecker-install-api = codechecker_common.cli_commands.install_api:main",
        ]
    },
)
