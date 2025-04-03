#!/usr/bin/env python3
"""
Directory Rename Script for CodeChecker

This script handles the renaming of the 'report-converter' directory to 
'report_converter' and updates all references throughout the codebase.

It performs the following tasks:
1. Uses git mv to rename the directory
2. Updates references in Makefiles
3. Updates references in documentation
4. Updates references in Python imports
5. Ensures entry points and commands remain compatible

Usage:
    python3 rename_report_converter.py [--dry-run]
"""

import os
import re
import sys
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional


def run_command(cmd: List[str], cwd: str = None, dry_run: bool = False) -> Tuple[int, str, str]:
    """Run a command and return the return code, stdout, and stderr."""
    if dry_run:
        print(f"[DRY RUN] Would run: {' '.join(cmd)}")
        return 0, "", ""
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        text=True
    )
    stdout, stderr = process.communicate()
    return process.returncode, stdout, stderr


def git_rename(repo_root: Path, old_dir: str, new_dir: str, dry_run: bool = False) -> bool:
    """Use git mv to rename the directory."""
    old_path = repo_root / old_dir
    new_path = repo_root / new_dir
    
    if not old_path.exists():
        print(f"Error: Source directory {old_path} does not exist")
        return False
    
    if new_path.exists():
        print(f"Error: Target directory {new_path} already exists")
        return False
    
    print(f"Renaming directory: {old_path} -> {new_path}")
    code, stdout, stderr = run_command(
        ["git", "mv", str(old_path), str(new_path)],
        cwd=str(repo_root),
        dry_run=dry_run
    )
    
    if code != 0:
        print(f"Error renaming directory:\n{stderr}")
        return False
    
    return True


def find_files(repo_root: Path, extensions: Set[str] = None, exclude_dirs: Set[str] = None) -> List[Path]:
    """Find all files with given extensions, excluding specified directories."""
    if extensions is None:
        extensions = {".py", ".md", ".yml", ".yaml", ".c", ".cpp", ".h", ".hpp", ".sh", ".txt", "Makefile"}
    
    if exclude_dirs is None:
        exclude_dirs = {".git", "build", "__pycache__", "venv"}
    
    result = []
    
    for root, dirs, files in os.walk(str(repo_root)):
        # Skip excluded directories
        for exclude in exclude_dirs:
            if exclude in dirs:
                dirs.remove(exclude)
        
        for file in files:
            file_path = Path(root) / file
            # Check if file has an extension we're interested in or is a Makefile
            if (file_path.suffix in extensions or
                file_path.name in {"Makefile", "makefile"} or
                "Makefile" in file_path.name):
                result.append(file_path)
    
    return result


def update_file_references(file_path: Path, old_dir: str, new_dir: str, dry_run: bool = False) -> int:
    """
    Update references in a file.
    Returns the number of occurrences replaced.
    """
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # Handle different types of references based on file type
    if file_path.suffix in {'.py'}:
        # Handle Python imports and string literals
        pattern = re.compile(r'([\'\"])tools/{}([\'\"])'.format(re.escape(old_dir)))
        new_content, count1 = re.subn(pattern, r'\1tools/{}\2'.format(new_dir), content)
        
        # Handle more general references
        pattern = re.compile(r'([^\w-]){}([^\w-])'.format(re.escape(old_dir)))
        new_content, count2 = re.subn(pattern, r'\1{}\2'.format(new_dir), new_content)
        
        count = count1 + count2
    elif file_path.name == "Makefile" or "Makefile" in file_path.name:
        # Handle paths in Makefiles
        pattern = re.compile(r'(report-converter)([:/)])')
        new_content, count = re.subn(pattern, f'report_converter\\2', content)
    elif file_path.suffix in {'.md', '.txt'}:
        # Handle documentation references
        # For URLs and directory paths
        pattern = re.compile(r'(/|\\){}(/|\\)'.format(re.escape(old_dir)))
        new_content, count1 = re.subn(pattern, r'\1{}\2'.format(new_dir), content)
        
        # For command references that should keep the hyphen
        # These are not changed if they represent the command name
        new_content = new_content
        count2 = 0
        
        count = count1 + count2
    else:
        # Generic replacement for other files
        pattern = re.compile(re.escape(old_dir))
        new_content, count = re.subn(pattern, new_dir, content)
    
    if count > 0:
        print(f"Updated {count} references in {file_path}")
        if not dry_run:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
    
    return count


def update_command_references(repo_root: Path, dry_run: bool = False) -> int:
    """
    Specially handle command references that should keep using hyphens.
    This preserves backward compatibility of command names while allowing
    proper Python imports.
    """
    changes = 0
    
    # Update setup.py to keep the command name as report-converter
    setup_py_path = repo_root / "tools" / "report_converter" / "setup.py"
    if setup_py_path.exists():
        with open(setup_py_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Ensure the entry point still uses the hyphenated name
        if "report-converter = " not in content:
            pattern = re.compile(r"report_converter = ")
            new_content, count = re.subn(pattern, "report-converter = ", content)
            
            if count > 0:
                print(f"Updated entry point in {setup_py_path} to preserve command name")
                if not dry_run:
                    with open(setup_py_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                changes += count
    
    # Check for the documentation specifically about the command
    doc_path = repo_root / "docs" / "tools" / "report-converter.md"
    if doc_path.exists():
        # Let's rename the doc file but keep content references to the command
        new_doc_path = repo_root / "docs" / "tools" / "report_converter.md"
        if not dry_run:
            code, stdout, stderr = run_command(
                ["git", "mv", str(doc_path), str(new_doc_path)],
                cwd=str(repo_root)
            )
            if code != 0:
                print(f"Error renaming documentation file:\n{stderr}")
            else:
                changes += 1
                print(f"Renamed documentation file: {doc_path} -> {new_doc_path}")
        else:
            print(f"[DRY RUN] Would rename: {doc_path} -> {new_doc_path}")
            changes += 1
    
    return changes


def update_makefile_symlinks(repo_root: Path, dry_run: bool = False) -> int:
    """
    Update symbolic link creation in Makefiles to ensure the command
    is still available as 'report-converter'.
    """
    changes = 0
    
    makefile_paths = [
        repo_root / "analyzer" / "Makefile",
        repo_root / "Makefile"
    ]
    
    for makefile_path in makefile_paths:
        if not makefile_path.exists():
            continue
        
        with open(makefile_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find and modify symlink creation commands to preserve the command name
        pattern = re.compile(r'(ln\s+-s[f]*)\s+([^\s]+)\s+report_converter')
        new_content, count = re.subn(pattern, r'\1 \2 report-converter', content)
        
        if count > 0:
            print(f"Updated {count} symbolic links in {makefile_path}")
            if not dry_run:
                with open(makefile_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
            changes += count
    
    return changes


def create_compatibility_symlink(repo_root: Path, dry_run: bool = False) -> int:
    """
    Create a compatibility symlink from report-converter to report_converter
    in the tools directory for scripts that might still use the old path.
    """
    old_path = repo_root / "tools" / "report-converter"
    if old_path.exists():
        print(f"Warning: Cannot create compatibility symlink as {old_path} already exists")
        return 0
    
    if not dry_run:
        os.symlink("report_converter", str(old_path), target_is_directory=True)
        print(f"Created compatibility symlink: {old_path} -> report_converter")
        return 1
    else:
        print(f"[DRY RUN] Would create symlink: {old_path} -> report_converter")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Rename report-converter directory and update references")
    parser.add_argument('--dry-run', action='store_true', help="Don't actually make changes, just print what would be done")
    args = parser.parse_args()
    
    repo_root = Path(__file__).parent.absolute()
    old_dir = "tools/report-converter"
    new_dir = "tools/report_converter"
    
    print(f"{'[DRY RUN] ' if args.dry_run else ''}Renaming directory and updating references")
    print(f"Repository root: {repo_root}")
    print(f"Old directory: {old_dir}")
    print(f"New directory: {new_dir}")
    
    # Step 1: Rename the directory using git mv
    if not git_rename(repo_root, old_dir, new_dir, args.dry_run):
        print("Directory rename failed. Aborting.")
        return 1
    
    # Step 2: Find files that might contain references
    files = find_files(repo_root)
    print(f"Found {len(files)} files to check for references")
    
    # Step 3: Update references in files
    total_references = 0
    for file_path in files:
        # Skip the renamed directory itself
        if new_dir in str(file_path):
            continue
        
        references = update_file_references(file_path, "report-converter", "report_converter", args.dry_run)
        total_references += references
    
    print(f"Updated {total_references} references in {len(files)} files")
    
    # Step 4: Ensure command names and entry points remain compatible
    command_changes = update_command_references(repo_root, args.dry_run)
    print(f"Made {command_changes} changes to preserve command name compatibility")
    
    # Step 5: Update Makefile symlinks to preserve the command name
    symlink_changes = update_makefile_symlinks(repo_root, args.dry_run)
    print(f"Updated {symlink_changes} symlink references in Makefiles")
    
    # Step 6: Create compatibility symlink for scripts that might use the old path
    compat_symlink = create_compatibility_symlink(repo_root, args.dry_run)
    
    if args.dry_run:
        print("\nThis was a dry run. To apply changes, run without --dry-run")
    else:
        print("\nDirectory renamed and references updated successfully")
        print("Remember to commit these changes with a clear message like:")
        print('  git commit -m "Rename tools/report-converter to tools/report_converter for standard Python packaging"')
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
