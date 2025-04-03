# Import Rewriting Guide for CodeChecker

This document describes the strategy for rewriting imports in the CodeChecker project to use repository root-based absolute imports, which provides better IDE integration and clearer dependency structures.

## Current Structure Analysis

CodeChecker currently uses a build process that copies modules from their source locations to a consolidated directory structure during `make package`. This approach works well for deployment but makes static analysis and IDE integration challenging.

### Key Module Locations

| Module | Source Location | Destination in Build |
|--------|-----------------|----------------------|
| `codechecker_common` | `/codechecker_common` | `build/CodeChecker/lib/python3/codechecker_common` |
| `codechecker_analyzer` | `/analyzer/codechecker_analyzer` | `build/CodeChecker/lib/python3/codechecker_analyzer` |
| `codechecker_web` | `/web/codechecker_web` | `build/CodeChecker/lib/python3/codechecker_web` |
| `codechecker_server` | `/web/server/codechecker_server` | `build/CodeChecker/lib/python3/codechecker_server` |
| `codechecker_client` | `/web/client/codechecker_client` | `build/CodeChecker/lib/python3/codechecker_client` |
| `codechecker_report_converter` | `/tools/report-converter/codechecker_report_converter` | `build/CodeChecker/lib/python3/codechecker_report_converter` |
| `tu_collector` | `/tools/tu_collector/tu_collector` | `build/CodeChecker/lib/python3/tu_collector` |
| Various other tools | Tools directories | Copied to lib/python3 |

### Problematic Areas Identified

1. **Non-Python-Compatible Directory Names**: `report-converter` contains a hyphen, which is not valid in Python import statements.

2. **Missing `__init__.py` Files**: Some parent directories lack `__init__.py` files, preventing proper package importing.

3. **Runtime vs. Development Imports**: The current import system works at runtime after the build process but is not IDE-friendly during development.

## Import Rewriting Solution

The provided `import_rewriter.py` script transforms imports to use repository root-based absolute imports, making the codebase more analyzable by IDEs while maintaining compatibility with the build system.

### Import Transformation Examples

| Original Import | Transformed Import |
|-----------------|-------------------|
| `import codechecker_common` | `import codechecker_common` (remains unchanged as it's at the root) |
| `from codechecker_common import logger` | `from codechecker_common import logger` (unchanged) |
| `import codechecker_analyzer` | `import analyzer.codechecker_analyzer` |
| `from codechecker_analyzer import env` | `from analyzer.codechecker_analyzer import env` |
| `import codechecker_web` | `import web.codechecker_web` |
| `from codechecker_report_converter.report import Report` | `from tools.report_converter.codechecker_report_converter.report import Report` |

### Handling Problematic Module Names

For directories with non-Python-compatible names like `report-converter`, the script transforms imports to use underscores instead:

Original directory: `/tools/report-converter/`
Import path: `tools.report_converter.codechecker_report_converter`

## Implementation Strategy

1. **Create Missing `__init__.py` Files**: The script identifies directories that need `__init__.py` files and creates them.

2. **Transform Import Statements**: All Python files are scanned for import statements, which are then transformed according to the mapping rules.

3. **Preserve Runtime Behavior**: The transformation ensures that the code will still work correctly with the build process.

## Usage Instructions

### Running the Script

```bash
# Dry run - shows changes without modifying files
python3 import_rewriter.py --dry-run --verbose

# Apply changes
python3 import_rewriter.py

# Process only a specific directory
python3 import_rewriter.py --path analyzer/codechecker_analyzer

# Skip creating __init__.py files
python3 import_rewriter.py --skip-init
```

### Workflow Integration

For optimal results:

1. Run the script as part of the development setup process
2. Consider adding a pre-commit hook to ensure all new files use the correct import style
3. Update CI checks to verify import patterns

## Potential Issues and Considerations

1. **Import Cycles**: Refactoring may reveal previously hidden circular dependencies
2. **Third-Party Packages**: Consider excluding them from the rewriting process
3. **Tests**: Some tests may rely on specific import mechanisms and might need manual updating

## Next Steps

After implementing the import rewriting:

1. Update documentation to reflect the new import style
2. Review automated tests to ensure they pass with the new import structure
3. Consider transitioning to proper Python packaging as a long-term solution
