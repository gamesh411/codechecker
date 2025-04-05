#!/usr/bin/env python3

import os
from pathlib import Path
import platform
import sys
import setuptools
import sys

from setuptools.command.build import build
from setuptools.command.build_ext import build_ext
from setuptools.extension import Extension

REQ_FILE_PATHS = [Path("analyzer", "requirements.txt"), Path("web", "requirements.txt")]

LD_LOGGER_SRC_PATH = Path("analyzer", "tools", "build-logger", "src")

LD_LOGGER_SOURCES = [
    "ldlogger-hooks.c",
    "ldlogger-logger.c",
    "ldlogger-tool.c",
    "ldlogger-tool-gcc.c",
    "ldlogger-tool-javac.c",
    "ldlogger-util.c",
]

LD_LOGGER_INCLUDES = ["ldlogger-hooks.h", "ldlogger-tool.h", "ldlogger-util.h"]

DATA_FILES_DEST = Path("share", "codechecker")
CONFIG_FILES_PATH = DATA_FILES_DEST / "config"
GENERATED_FILES_DEST = Path("build") / "__generated__"


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


def discover_config_files(config_dir_path):
    """Discover all config files recursively and create data_files entries.

    Args:
        config_dir_path: Path to the config directory

    Returns:
        List of tuples (target_dir, [file_paths]) for data_files
    """
    data_files = []
    config_dir = Path(config_dir_path)

    for file_path in config_dir.glob("**/*"):
        if file_path.is_file():
            # Create relative path from config dir
            rel_path = file_path.relative_to(config_dir)
            # Determine target directory
            if len(rel_path.parts) > 1:
                # File is in a subdirectory
                target_dir = CONFIG_FILES_PATH / Path(*rel_path.parts[:-1])
            else:
                # File is directly in config directory
                target_dir = CONFIG_FILES_PATH

            # Add file to data_files
            data_files.append((str(target_dir), [str(file_path)]))

    return data_files


def get_data_files():
    """
    This functions returns the list of descriptors that define which files
    will be copied into the distribution.
    """
    data_files = []

    # docs
    data_files.extend(
        [
            (str(DATA_FILES_DEST / "docs"), [str(Path("docs", "README.md"))]),
            *map(lambda p: (str(DATA_FILES_DEST / p), [str(p)]), REQ_FILE_PATHS),
        ]
    )

    # config - explicitly include all config files
    data_files.extend(discover_config_files("config"))

    # web/config - include web-specific config files
    data_files.extend(discover_config_files("web/config"))

    # Version files and commands.json
    # These files are generated during the build process
    # Make sure they're included in the package
    data_files.append(
        (
            str(CONFIG_FILES_PATH),
            [
                str(GENERATED_FILES_DEST / CONFIG_FILES_PATH / "commands.json"),
                str(GENERATED_FILES_DEST / CONFIG_FILES_PATH / "web_version.json"),
                str(GENERATED_FILES_DEST / CONFIG_FILES_PATH / "analyzer_version.json"),
            ],
        )
    )

    # Web frontend files
    # These are generated during the build process by build_web_frontend
    web_dist_path = GENERATED_FILES_DEST / DATA_FILES_DEST / "www"
    if os.path.exists(web_dist_path):
        for root, _, files in os.walk(web_dist_path):
            if files:
                rel_path = os.path.relpath(root, web_dist_path)
                target_path = DATA_FILES_DEST / "www"
                if rel_path != ".":
                    target_path = target_path / rel_path
                data_files.append(
                    (str(target_path), [str(Path(root) / f) for f in files])
                )

    # ld logger header
    # TODO: do we need to copy the header files?
    data_files.append(
        (
            str(DATA_FILES_DEST / "ld_logger" / "include"),
            [str(LD_LOGGER_SRC_PATH / i) for i in LD_LOGGER_INCLUDES],
        )
    )

    return data_files


def get_ext_modules():
    return [
        Extension(
            "codechecker_analyzer.ld_logger.lib.ldlogger",
            define_macros=[("__LOGGER_MAIN__", None), ("_GNU_SOURCE", None)],
            extra_link_args=[
                "-O2",
                "-fomit-frame-pointer",
                "-fvisibility=hidden",
                "-pedantic",
                "-Wl,--no-as-needed",
                "-ldl",
            ],
            sources=[os.path.join(LD_LOGGER_SRC_PATH, s) for s in LD_LOGGER_SOURCES],
        )
    ]


class Build(build):
    def run(self):
        # First run the standard build
        build.run(self)

        # Create commands.json
        self.generate_commands_json()

        # Extend version files with build date and git information
        self.extend_version_files()

        # Build API packages if they don't exist
        self.build_api_packages()

        # Build web frontend
        self.build_web_frontend()

    def generate_commands_json(self):
        """Generate commands.json file by collecting all CLI commands."""
        import glob
        import json

        # Create config directory if it doesn't exist
        config_dir = GENERATED_FILES_DEST / CONFIG_FILES_PATH
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

    def extend_version_files(self):
        """Extend version files with build date and git information."""
        import json
        import time
        import subprocess
        import shutil

        print("Extending version files with build date and git information...")

        # Ensure the config directory exists
        config_dir = GENERATED_FILES_DEST / CONFIG_FILES_PATH
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
        src_analyzer_version = os.path.join(
            "analyzer", "config", "analyzer_version.json"
        )
        if os.path.exists(src_analyzer_version):
            shutil.copy(src_analyzer_version, analyzer_version_file)
            print(f"Copied {src_analyzer_version} to {analyzer_version_file}")
        else:
            print(f"Warning: Source file {src_analyzer_version} not found")

        # Extend both version files with build date and git information
        self._extend_version_file(web_version_file)
        self._extend_version_file(analyzer_version_file)

    def _extend_version_file(self, version_file):
        """Extend a version file with build date and git information."""
        import json
        import time
        import subprocess

        if not os.path.exists(version_file):
            print(f"Warning: Version file not found: {version_file}")
            return

        try:
            with open(version_file, encoding="utf-8", errors="ignore") as v_file:
                version_json_data = json.load(v_file)

            # Add git information if available
            self._add_git_info(version_json_data)

            # Add build date
            time_now = time.strftime("%Y-%m-%dT%H:%M")
            version_json_data["package_build_date"] = time_now

            # Rewrite version config file with the extended data
            with open(version_file, "w", encoding="utf-8", errors="ignore") as v_file:
                v_file.write(json.dumps(version_json_data, sort_keys=True, indent=4))

            print(f"Extended version file: {version_file}")
        except Exception as e:
            print(f"Error extending version file {version_file}: {str(e)}")

    def _add_git_info(self, version_json_data):
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

    def build_api_packages(self):
        """Build the API packages if they don't exist."""
        import subprocess
        import shutil
        import os.path
        import tempfile

        print("Checking and building API packages if needed...")

        # Define paths
        api_dir = os.path.join("web", "api")
        api_py_dir = os.path.join(api_dir, "py")
        api_shared_dist = os.path.join(api_py_dir, "codechecker_api_shared", "dist")
        api_dist = os.path.join(api_py_dir, "codechecker_api", "dist")

        # Check if the API packages already exist
        api_shared_tarball = os.path.join(
            api_shared_dist, "codechecker_api_shared.tar.gz"
        )
        api_tarball = os.path.join(api_dist, "codechecker_api.tar.gz")

        need_build = False

        if not os.path.exists(api_shared_tarball) or not os.path.exists(api_tarball):
            need_build = True
            print("API packages not found, building them...")

        if need_build:
            try:
                # Create directories for generated files if they don't exist
                py_api_dir = os.path.join(
                    api_py_dir, "codechecker_api", "codechecker_api"
                )
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
                                self._copy_directory(
                                    os.path.join(gen_py_dir, "codechecker_api_shared"),
                                    py_api_shared_dir,
                                )

                            # Copy all other API files
                            for item in os.listdir(gen_py_dir):
                                if item != "codechecker_api_shared" and os.path.isdir(
                                    os.path.join(gen_py_dir, item)
                                ):
                                    self._copy_directory(
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
                                if file.startswith(
                                    "codechecker_api-"
                                ) and file.endswith(".tar.gz"):
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
                    print(
                        "The API packages are pre-built and committed to the repository,"
                    )
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

    def _copy_directory(self, src, dst):
        """Copy all contents from src directory to dst directory."""
        import os
        import shutil

        if not os.path.exists(dst):
            os.makedirs(dst)

        for item in os.listdir(src):
            src_item = os.path.join(src, item)
            dst_item = os.path.join(dst, item)

            if os.path.isdir(src_item):
                self._copy_directory(src_item, dst_item)
            else:
                shutil.copy2(src_item, dst_item)

    def build_web_frontend(self):
        """Build the web frontend."""
        import os
        import subprocess
        import sys
        import shutil

        print("Building web frontend...")

        # Define paths
        root_dir = os.path.dirname(os.path.abspath(__file__))
        web_dir = os.path.join(root_dir, "web")
        vue_cli_dir = os.path.join(web_dir, "server", "vue-cli")
        dist_dir = os.path.join(vue_cli_dir, "dist")

        # Define destination path in the generated files directory
        web_dest_dir = os.path.join(GENERATED_FILES_DEST, DATA_FILES_DEST, "www")
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
                                print(
                                    "Vue.js application is up to date, skipping build."
                                )
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
                    self._copy_directory(dist_dir, web_dest_dir)
                else:
                    print(f"Warning: Vue.js build directory {dist_dir} does not exist")

            except (subprocess.CalledProcessError, OSError) as e:
                print(f"Warning: Failed to build Vue.js application: {e}")
                print("Continuing with installation without web frontend...")
        else:
            print("Skipping web frontend build as BUILD_UI_DIST is not set to YES")

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


class BuildExt(build_ext):
    def get_ext_filename(self, ext_name):
        return Path(platform.architecture()[0], f"{ext_name}.so")

    def build_extension(self, ext):
        if sys.platform == "linux":
            build_ext.build_extension(self, ext)


from setuptools.command.install import install


class CustomInstall(install):
    """Custom install command that installs API packages after main installation."""

    def run(self):
        # Run the standard installation
        install.run(self)

        # Install API packages if they exist
        self.install_api_packages()

    def install_api_packages(self):
        """Install API packages after main installation."""
        import subprocess
        import os.path

        print("Installing API packages...")

        # Define paths to API packages
        api_dir = os.path.join("web", "api", "py")
        api_shared_path = os.path.join(
            api_dir, "codechecker_api_shared", "dist", "codechecker_api_shared.tar.gz"
        )
        api_path = os.path.join(
            api_dir, "codechecker_api", "dist", "codechecker_api.tar.gz"
        )

        # Install API packages if they exist
        if os.path.exists(api_shared_path):
            print(f"Installing {api_shared_path}")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", api_shared_path]
                )
                print(f"Successfully installed {api_shared_path}")
            except subprocess.CalledProcessError as e:
                print(f"Error installing {api_shared_path}: {str(e)}")
        else:
            print(f"Warning: API shared package not found at {api_shared_path}")

        if os.path.exists(api_path):
            print(f"Installing {api_path}")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", api_path]
                )
                print(f"Successfully installed {api_path}")
            except subprocess.CalledProcessError as e:
                print(f"Error installing {api_path}: {str(e)}")
        else:
            print(f"Warning: API package not found at {api_path}")


setuptools.setup(
    name="codechecker",
    version="6.26.0",
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
    data_files=get_data_files(),
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
    ext_modules=get_ext_modules(),
    cmdclass={
        "build": Build,
        "build_ext": BuildExt,
        "install": CustomInstall,
    },
    python_requires=">=3.8",
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
        ]
    },
)
