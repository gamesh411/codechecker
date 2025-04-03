# CodeChecker Build Process Investigation

## Project Structure Overview

The CodeChecker project follows a modular structure with several main components:

- **analyzer**: Contains the static analysis tools and code
- **web**: Contains the web server, client, and UI components
- **codechecker_common**: Contains shared utilities used by other modules
- **tools**: Contains various helper tools
- **bin**: Main entry point scripts
- **config**: Configuration files

## Build Process

The build process is managed through a series of Makefiles. The main `make package` command:

1. Creates a `build/CodeChecker` directory structure
2. Copies Python modules and packages from their source locations to `build/CodeChecker/lib/python3`
3. Builds tools like the ld_logger, tu_collector, report-converter, and other utilities
4. Creates symbolic links in the `bin` directory to Python entry points
5. Copies configuration files

## Why Files Are Moved During Build

After investigating the codebase and build process, here are the likely reasons why the original creators decided to move Python modules to a new location during build:

### 1. Package Isolation and Installation Flexibility

- **Consistent Runtime Environment**: Moving modules to a predefined structure ensures that all components can reliably find each other regardless of where the package is installed.
- **Path Independence**: The build structure is self-contained, making it possible to run CodeChecker from any location without requiring Python package installation.
- **Deployment Consistency**: Ensures that the deployed package has the same structure across different environments.

### 2. Runtime Module Resolution

- **Custom Import Resolution**: The project uses environment variables like `CC_LIB_DIR` to resolve imports at runtime.
- **Controlled Dependencies**: The build process ensures only the needed modules are included, avoiding accidental dependencies on development-only code.
- **Version Consistency**: Guarantees that components use the correct versions of modules they depend on.

### 3. Tool Integration

- **Binary Wrappers**: The project creates symbolic links and wrappers in the `bin` directory that rely on the predictable location of the Python modules.
- **Mixed Language Components**: The project includes both Python modules and compiled C/C++ components that need to interact.

### 4. Historical Development Pattern

- This approach follows a traditional compiled-software distribution model rather than Python-native packaging (pip/setuptools).
- It enables the distribution of a self-contained package that doesn't interfere with system Python packages.

## Possible Ways to Circumvent Moving Files

There are a few potential approaches to avoid moving files while maintaining the project's functionality:

### 1. Development Mode Symlinks

A possible solution would be to modify the build process to create symbolic links to the original module locations instead of copying files. This is partially implemented in the `dev_package` target:

```makefile
dev_package: package
	rm -rf $(CC_BUILD_LIB_DIR)/codechecker_common && \
	rm -rf $(CC_BUILD_LIB_DIR)/codechecker_analyzer && \
	rm -rf $(CC_BUILD_LIB_DIR)/codechecker_web && \
	rm -rf $(CC_BUILD_LIB_DIR)/codechecker_server && \
	rm -rf $(CC_BUILD_LIB_DIR)/codechecker_report_converter && \
	rm -rf $(CC_BUILD_LIB_DIR)/codechecker_client

	ln -fsv $(ROOT)/codechecker_common $(CC_BUILD_LIB_DIR) && \
	ln -fsv $(CC_ANALYZER)/codechecker_analyzer $(CC_BUILD_LIB_DIR) && \
	ln -fsv $(CC_WEB)/codechecker_web $(CC_BUILD_LIB_DIR) && \
	ln -fsv $(CC_SERVER)/codechecker_server $(CC_BUILD_LIB_DIR) && \
	ln -fsv $(CC_TOOLS)/report_converter/codechecker_report_converter $(CC_BUILD_LIB_DIR) && \
	ln -fsv $(CC_CLIENT)/codechecker_client $(CC_BUILD_LIB_DIR)
```

This could be extended to be the default behavior in development environments.

### 2. Modern Python Package Structure

Restructure the project to follow modern Python packaging practices:
- Use proper namespace packages
- Create setup.py files with development mode support (`pip install -e .`)
- Use environment variables or configuration to locate resources

### 3. IDE Integration

Create IDE-specific configuration files that help development tools understand the project structure:
- For VSCode: Create a `.env` file with PYTHONPATH settings
- For PyCharm: Configure source roots in the project settings
- Generate compile_commands.json for language servers

## Trade-offs

While static file locations would improve IDE integration and development experience, there are trade-offs:

1. **Backward Compatibility**: Changing the module structure could break existing installations and scripts
2. **Runtime Performance**: The current approach optimizes for runtime over development experience
3. **Package Management**: The current approach gives precise control over the deployed structure

## Implementation Plan: Migration to Modern Python Package Structure

### Evaluation of Current and Proposed Package Structure

After analyzing the codebase, I've verified that `codechecker_common` indeed serves as a shared library package used by both analyzer and web components. It provides common utilities including:
- Logging functionality
- Singleton pattern implementation
- Utility functions like JSON loading
- Command-line argument processing
- Source code comment and review status handling
- Compatibility layers

Your proposed simplified package structure (with 2-3 user-facing packages) aligns well with the actual usage patterns in the codebase, and would streamline the installation and maintenance process compared to exposing all internal packages separately.

### Recommended Package Structure

1. **CodeChecker Common Library** (`codechecker-common`)
   - Pure library package with no CLI commands
   - Contains shared functionality used by both analyzer and web components
   - Not typically installed directly by end-users

2. **CodeChecker Analyzer** (`codechecker-analyzer`)
   - Includes all analyzer functionality and tools (report-converter, etc.)
   - Primary package for users who only need analysis capabilities
   - Depends on `codechecker-common`

3. **CodeChecker Web** (`codechecker-web`)
   - Includes web server, client and UI components
   - Used by those who need the web interface and result storage
   - Depends on `codechecker-common`

4. **CodeChecker Complete** (`codechecker`)
   - Meta-package that depends on both analyzer and web packages
   - Provides the complete functionality
   - Single installation target for users who want everything

This structure offers several advantages:
- Clear separation of concerns
- Allows lightweight installations for specific use cases
- Better reflects the actual component usage
- More maintainable than many small packages

### Detailed Implementation Plan

#### Phase 1: Analysis and Preparation (1-2 weeks)

1. **Dependency Mapping**
   - Create detailed dependency graphs between current modules
   - Document resource loading patterns and environment variables
   - Identify any dynamic imports or path manipulations

2. **Package Boundary Definition**
   - Precisely define what belongs in each package
   - Catalog which modules should be internal vs. exposed APIs
   - Define version synchronization strategy across packages

#### Phase 2: Common Library Package (2-3 weeks)

1. **Refactor Common Library**
   - Create proper package structure for `codechecker-common`
   - Move all shared functionality into this package
   - Implement `setup.py` with proper metadata:

```python
# codechecker_common/setup.py
from setuptools import setup, find_packages

setup(
    name="codechecker-common",  # Note: using hyphen for PyPI convention
    version="6.26.0",  # Match current version
    packages=find_packages(),
    package_data={
        "codechecker_common": ["*.json", "config/*"],
    },
    include_package_data=True,
    install_requires=[
        # Dependencies extracted from requirements.txt
    ],
    # No entry_points as this is a library package
    description="Common utilities for CodeChecker",
    author='CodeChecker Team (Ericsson)',
    author_email='codechecker-tool@googlegroups.com',
    url="https://github.com/Ericsson/CodeChecker",
    license='Apache-2.0 WITH LLVM-exception',
)
```

2. **Implement Resource Discovery**
   - Replace direct file path references with package-based resource loading:

```python
# Example replacement for direct path references
from importlib import resources

def get_config(filename):
    with resources.open_text("codechecker_common.config", filename) as f:
        return json.load(f)
```

#### Phase 3: Analyzer Package (3-4 weeks)

1. **Build Analyzer Package**
   - Consolidate analyzer modules and tools into a single package
   - Create a clean separation between internal and public APIs
   - Implement `setup.py` with entry points for CLI tools:

```python
# analyzer/setup.py
setup(
    name="codechecker-analyzer",
    version="6.26.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "codechecker-common==6.26.0",
        # Additional dependencies
    ],
    entry_points={
        "console_scripts": [
            "CodeChecker-analyze=codechecker_analyzer.cli.check:main",
            "report-converter=codechecker_report_converter.cli:main",
            # Additional tool entry points
        ],
    },
    description="Static analysis tooling for various analyzers",
    # Other metadata
)
```

2. **Bundle Tools with Analyzer**
   - Include all analysis-related tools like report-converter
   - Maintain backward compatibility for existing command names
   - Ensure shared build artifacts are properly packaged

#### Phase 4: Web Package (3-4 weeks)

1. **Build Web Package**
   - Organize web server, client components, and UI into a cohesive package
   - Ensure proper asset bundling for the web interface
   - Implement setup.py with web-specific entry points:

```python
# web/setup.py
setup(
    name="codechecker-web",
    version="6.26.0",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "codechecker_web": ["www/**/*"],  # Include web assets
    },
    install_requires=[
        "codechecker-common==6.26.0",
        # Web-specific dependencies
    ],
    entry_points={
        "console_scripts": [
            "CodeChecker-web=codechecker_web.cli.server:main",
            "CodeChecker-store=codechecker_server.cli.store:main",
            # Additional web-related commands
        ],
    },
    description="Web viewer and storage for CodeChecker analysis results",
    # Other metadata
)
```

#### Phase 5: Unified Meta-Package (1-2 weeks)

1. **Create Meta-Package**
   - Implement a thin wrapper that depends on both analyzer and web packages
   - Provide a unified entry point for full functionality:

```python
# setup.py for meta-package
setup(
    name="codechecker",
    version="6.26.0",
    packages=find_packages(),
    install_requires=[
        "codechecker-analyzer==6.26.0",
        "codechecker-web==6.26.0",
    ],
    entry_points={
        "console_scripts": [
            "CodeChecker=codechecker.cli:main",
        ],
    },
    description="Static analysis tooling and result viewer platform",
    # Other metadata
)
```

2. **Command Dispatcher**
   - Create a unified CLI that dispatches to appropriate subcommands
   - Maintain backward compatibility with existing command structure

#### Phase 6: Build System Integration (2-3 weeks)

1. **Development Mode Support**
   - Create Makefile targets that install components in development mode:

```makefile
# Development mode installation
dev_install:
	cd $(ROOT)/codechecker_common && pip install -e .
	cd $(ROOT)/analyzer && pip install -e .
	cd $(ROOT)/web && pip install -e .
	cd $(ROOT) && pip install -e .
```

2. **Hybrid Build Process**
   - Support both traditional build and modern packaging:

```makefile
# Build packages for distribution
build_packages:
	cd $(ROOT)/codechecker_common && python -m build
	cd $(ROOT)/analyzer && python -m build
	cd $(ROOT)/web && python -m build
	cd $(ROOT) && python -m build
```

3. **Distribution Pipeline**
   - Set up package building and publication workflow
   - Create containerized build environment for consistent packages

#### Phase 7: Testing and Documentation (Ongoing)

1. **Test Strategy**
   - Create comprehensive tests for package installation
   - Verify all functionality works with the new structure
   - Test on multiple platforms and Python versions

2. **Documentation Updates**
   - Update installation guides for different use cases
   - Create developer documentation for the new structure
   - Provide migration guides for existing users

3. **Gradual Rollout**
   - Release alpha/beta versions for early adopters
   - Collect feedback and refine the implementation
   - Plan for a staged official release

### Benefits of the Simplified Package Structure

1. **User-Focused Packages**: Users only need to install what they need, without being exposed to implementation details.

2. **Clearer Documentation**: Documentation can focus on practical use cases rather than explaining internal component relationships.

3. **Simplified Dependency Management**: Fewer packages means fewer version constraints to manage and synchronize.

4. **Reduced Installation Complexity**: Users don't need to understand the internal architecture to install and use the software.

5. **Better IDE Integration**: Cleaner package boundaries make it easier for tooling to understand the project structure.

### Risk Management

#### Potential Risks and Mitigation Strategies

1. **Internal Module Visibility**
   - Risk: Internal modules may be accidentally exposed as public APIs
   - Mitigation: Clearly mark internal modules with underscores, use `__all__` declarations

2. **Resource Location Changes**
   - Risk: Resources cannot be found at runtime due to changed package structure
   - Mitigation: Use package resource APIs consistently, thorough testing

3. **Backward Compatibility**
   - Risk: Existing scripts and integrations may break with new structure
   - Mitigation: Create compatibility layer, maintain CLI command names

4. **Migration Complexity**
   - Risk: Migrating monolithic structure to packages may be complex
   - Mitigation: Phased approach, continuous testing

5. **Build Process Changes**
   - Risk: Existing build automation may need significant changes
   - Mitigation: Create hybrid approach that supports both old and new methods

### Timeline Summary

1. **Analysis and Preparation**: 1-2 weeks
2. **Common Library Package**: 2-3 weeks
3. **Analyzer Package**: 3-4 weeks
4. **Web Package**: 3-4 weeks
5. **Unified Meta-Package**: 1-2 weeks
6. **Build System Integration**: 2-3 weeks
7. **Testing and Documentation**: Ongoing (2-4 weeks)

**Total Estimated Time**: 14-22 weeks (3.5-5.5 months)

### Metrics for Success

1. **Simplified Installation**: Users can install components with standard `pip install codechecker-analyzer` 

2. **Development Workflow**: Developers can use `pip install -e .` for a streamlined development experience

3. **Test Coverage**: All existing functionality continues to work with new package structure

4. **Performance**: No regression in startup time or runtime performance

5. **Compatibility**: Existing command-line tools and integrations continue to work
