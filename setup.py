#!/usr/bin/env python3

import os
from pathlib import Path
import platform
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
        ".",                                # codechecker_common
        "analyzer",                         # codechecker_analyzer
        "web",                             # codechecker_web
        "web/server",                      # codechecker_server
        "web/client",                      # codechecker_client
        "tools/tu_collector",              # tu_collector
        "tools/report-converter",          # codechecker_report_converter
        "analyzer/tools/statistics_collector", # codechecker_statistics_collector
        "web/api/py"                       # codechecker_api, codechecker_api_shared
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


def discover_data_files(dir_name):
    data_files = []
    dir_path = Path(dir_name)
    for root, _, files in os.walk(dir_path):
        if not files:
            continue
        entry = (
            str(DATA_FILES_DEST / dir_path),
            map(lambda p: str(dir_path / p), files),
        )
        data_files.append(entry)

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

    # config
    data_files.extend(discover_data_files("config"))

    # commands.json
    # The actual file will be generated during the build process
    # This entry ensures the package includes the config directory structure
    data_files.append(
        (
            str(CONFIG_FILES_PATH),
            [str(GENERATED_FILES_DEST / CONFIG_FILES_PATH / "commands.json")],
        )
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


class BuildExt(build_ext):
    def get_ext_filename(self, ext_name):
        return Path(platform.architecture()[0], f"{ext_name}.so")

    def build_extension(self, ext):
        if sys.platform == "linux":
            build_ext.build_extension(self, ext)


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
