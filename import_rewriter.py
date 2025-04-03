#!/usr/bin/env python3
"""
Import Rewriter Tool for CodeChecker

This script rewrites Python imports in the CodeChecker codebase to use 
repository root-based absolute imports, improving IDE analysis and standardizing
the import structure.

Usage:
    python3 import_rewriter.py [--dry-run] [--verbose]
"""

import os
import re
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

# Define the repository root
REPO_ROOT = Path(__file__).parent.absolute()

# Mapping of module sources and their absolute import paths
MODULE_MAPPING = {
    # Main modules
    "codechecker_common": "codechecker_common",
    "codechecker_analyzer": "analyzer.codechecker_analyzer",
    "codechecker_web": "web.codechecker_web",
    "codechecker_server": "web.server.codechecker_server",
    "codechecker_client": "web.client.codechecker_client",
    
    # Tool modules
    "codechecker_report_converter": "tools.report_converter.codechecker_report_converter",
    "codechecker_merge_clang_extdef_mappings": "analyzer.tools.merge_clang_extdef_mappings.codechecker_merge_clang_extdef_mappings",
    "codechecker_statistics_collector": "analyzer.tools.statistics_collector.codechecker_statistics_collector",
    "tu_collector": "tools.tu_collector.tu_collector",
    "bazel_compile_commands": "tools.bazel.bazel_compile_commands",
}

# Modules with problematic names that need special handling
PROBLEMATIC_MODULES = {
    "report-converter": "report_converter"  # Replace hyphen with underscore
}

# Modules import patterns to detect
IMPORT_PATTERNS = [
    # Regular imports
    re.compile(r'^(\s*)import\s+([\w\.]+)(.*)$'),
    # From imports
    re.compile(r'^(\s*)from\s+([\w\.]+)\s+import\s+(.*)$'),
]


def find_python_files(root_dir: Path) -> List[Path]:
    """Find all Python files in the given directory and its subdirectories."""
    python_files = []
    for path in root_dir.rglob("*.py"):
        # Skip venv, build directories and other generated content
        if any(part.startswith(("venv", "build", "__pycache__", ".git")) 
               for part in path.parts):
            continue
        python_files.append(path)
    return python_files


def create_init_files(root_dir: Path) -> List[Path]:
    """Create missing __init__.py files in package directories."""
    created_files = []
    
    # Get all directories containing Python files
    python_dirs = set()
    for py_file in find_python_files(root_dir):
        python_dirs.add(py_file.parent)
    
    # For each directory with Python files, ensure all parent directories
    # up to the repo root have __init__.py files
    for py_dir in python_dirs:
        current = py_dir
        while current != root_dir and current.parent != current:
            init_file = current / "__init__.py"
            if not init_file.exists():
                print(f"Creating __init__.py in {current}")
                if not args.dry_run:
                    init_file.touch()
                created_files.append(init_file)
            current = current.parent
    
    return created_files


def transform_import_path(original_import: str) -> Optional[str]:
    """
    Transform an import path to use repository root-based absolute imports.
    Returns None if no transformation is needed.
    """
    # Check if this import uses any of our modules
    for module, new_path in MODULE_MAPPING.items():
        # Full module import (e.g., "import codechecker_common")
        if original_import == module:
            return new_path
        
        # Module as prefix (e.g., "from codechecker_common.logger import get_logger")
        if original_import.startswith(f"{module}."):
            suffix = original_import[len(module):]
            return f"{new_path}{suffix}"
    
    # No transformation needed
    return None


def rewrite_file_imports(file_path: Path, verbose: bool = False, dry_run: bool = False) -> int:
    """
    Rewrite imports in a file to use repository root-based absolute imports.
    Returns the number of lines changed.
    """
    if verbose:
        print(f"Processing {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    changes = 0
    new_lines = []
    
    for line in lines:
        new_line = line
        
        # Check all import patterns
        for pattern in IMPORT_PATTERNS:
            match = pattern.match(line)
            if match:
                if "import" in line:
                    if "from" in line:
                        # Handle "from X import Y" pattern
                        prefix, module_path, imports = match.groups()
                        new_module_path = transform_import_path(module_path)
                        if new_module_path:
                            new_line = f"{prefix}from {new_module_path} import {imports}"
                            changes += 1
                            if verbose:
                                print(f"  {line.strip()} -> {new_line.strip()}")
                    else:
                        # Handle "import X" pattern
                        prefix, module_path, suffix = match.groups()
                        new_module_path = transform_import_path(module_path)
                        if new_module_path:
                            new_line = f"{prefix}import {new_module_path}{suffix}"
                            changes += 1
                            if verbose:
                                print(f"  {line.strip()} -> {new_line.strip()}")
        
        new_lines.append(new_line)
    
    if changes > 0 and not dry_run:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
    
    return changes


def main():
    global args
    parser = argparse.ArgumentParser(description="Rewrite imports in CodeChecker to use repository root-based absolute imports")
    parser.add_argument('--dry-run', action='store_true', help="Don't actually modify files, just print what would be done")
    parser.add_argument('--verbose', action='store_true', help="Show detailed information about changes")
    parser.add_argument('--skip-init', action='store_true', help="Skip creating __init__.py files")
    parser.add_argument('--path', type=str, help="Process only files in this path")
    
    args = parser.parse_args()
    
    print(f"CodeChecker Import Rewriter")
    print(f"Repository root: {REPO_ROOT}")
    
    if args.dry_run:
        print("Running in dry-run mode - no files will be modified")
    
    # Create missing __init__.py files if needed
    if not args.skip_init:
        created_files = create_init_files(REPO_ROOT)
        print(f"Created {len(created_files)} __init__.py files")
    
    # Fix problematic module directories
    for prob_dir, fixed_name in PROBLEMATIC_MODULES.items():
        # We don't actually rename directories, just use the mapping
        print(f"Note: '{prob_dir}' will be imported as '{fixed_name}'")
    
    # Process Python files
    process_path = Path(args.path) if args.path else REPO_ROOT
    if not process_path.exists():
        print(f"Error: Path {process_path} does not exist")
        return 1
    
    python_files = find_python_files(process_path)
    print(f"Found {len(python_files)} Python files to process")
    
    total_changes = 0
    modified_files = 0
    
    for file_path in python_files:
        changes = rewrite_file_imports(file_path, verbose=args.verbose, dry_run=args.dry_run)
        total_changes += changes
        if changes > 0:
            modified_files += 1
    
    print(f"Modified {modified_files} files with {total_changes} import changes")
    
    # Generate report
    print("\nImport Rewriting Guide:")
    print("======================")
    print("Here's how imports have been transformed:")
    for original, new_path in MODULE_MAPPING.items():
        print(f"  - '{original}' → '{new_path}'")
    
    if args.dry_run:
        print("\nThis was a dry run. To apply changes, run without --dry-run")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
