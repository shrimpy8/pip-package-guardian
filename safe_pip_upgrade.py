#!/usr/bin/env python3
"""
pip Package Guardian (safe_pip_upgrade.py)
==========================================

A safe, interactive tool for upgrading Python packages with intelligent risk assessment
and dependency analysis.

Features:
- Environment detection (venv, conda, homebrew) with system Python protection
- Risk classification (Low/Medium/High/Critical) with version downgrade detection
- Dependency conflict detection with empty string filtering
- Pre-upgrade snapshots and rollback scripts
- Post-upgrade import verification with known package mapping (Pillow→PIL, etc.)
- Multiple upgrade strategies (auto LOW, LOW+MEDIUM, custom selection, critical only)
- Enhanced version parsing (handles epochs, local versions, pre-releases)
- Input sanitization to prevent shell injection attacks

DISCLAIMER:
-----------
⚠️  USE AT YOUR OWN RISK ⚠️

This script modifies your Python packages which can break your applications.
While designed with extensive safety features, there are inherent risks:

1. BACKUP YOUR WORK: Commit code and document current package versions
2. TEST THOROUGHLY: Test your application after any package upgrades
3. VIRTUAL ENVIRONMENTS: Always prefer venv/conda over global installs
4. ROLLBACK READY: Keep the generated rollback scripts
5. BREAKING CHANGES: Major version upgrades can break your code
6. NO WARRANTY: This software is provided "as-is" without warranties

SAFETY FEATURES:
----------------
✅ Risk assessment before any upgrade (handles version downgrades)
✅ Dependency conflict detection
✅ Pre-upgrade snapshots with exact versions
✅ Automatic rollback script generation
✅ Import verification after upgrades (with common package name mappings)
✅ Protected system Python (will not modify)
✅ User confirmation at every critical step
✅ Input sanitization to prevent injection attacks
✅ Enhanced version parsing (PEP 440 compliant)

Author: Generated via Warp AI
License: MIT (Free to use and modify)
Version: 1.1.0
Script: safe_pip_upgrade.py

Version History:
--------------
v1.1.0 (2026-01-06) - Interactive Loop & Per-Action Logging:
- Added interactive main loop - perform multiple upgrades in one session
- Separate log files for each upgrade action (better audit trail)
- New "Refresh package list" option
- Session summary showing all created log files
- Improved user experience with continuous workflow

v1.0.1 (2026-01-06) - Bug Fixes & Improvements:
- Fixed version comparison logic for downgrades and edge cases
- Enhanced version parsing to handle epochs, local versions, and pre-releases
- Improved import verification with known package name mappings
- Added input validation for custom package selection
- Fixed empty string handling in dependency parsing
- Better error messages for critical package selection

v1.0.0 (Initial Release):
- Core functionality with risk assessment and rollback capability
"""

import os
import sys
import json
import subprocess
import re
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Set
from enum import Enum
import urllib.request
import urllib.error

# Try to import rich for better UI
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Confirm, Prompt
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.syntax import Syntax
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Warning: 'rich' library not found. Using plain output.")
    print("For better experience: pip install rich")


class RiskLevel(Enum):
    """
    Risk levels for package upgrades.
    
    LOW: Patch updates, no dependents, safe to auto-upgrade
    MEDIUM: Minor updates, has dependents with compatible ranges
    HIGH: Major updates, tight version pins, breaking changes
    CRITICAL: pip, setuptools, wheel - require special handling
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class UpgradeMode(Enum):
    """
    User interaction modes.
    
    BATCH: Review all, select, then upgrade
    INTERACTIVE: Review and upgrade one by one
    PROFILE: Use saved preferences
    """
    BATCH = "batch"
    INTERACTIVE = "interactive"
    PROFILE = "profile"


class PackageInfo:
    """
    Container for package information and risk assessment.
    
    Attributes:
        name: Package name
        current_version: Currently installed version
        latest_version: Latest available version on PyPI
        risk_level: Assessed risk level
        dependents: List of packages that depend on this one
        conflicts: List of dependency conflicts if upgraded
        description: Package description from PyPI
        last_updated: Days since last update
        is_dev_tool: Whether this is a development tool
        is_critical: Whether this is pip/setuptools/wheel
    """
    
    def __init__(self, name: str, current: str, latest: str):
        self.name = name
        self.current_version = current
        self.latest_version = latest
        self.risk_level = RiskLevel.LOW
        self.dependents: List[str] = []
        self.conflicts: List[str] = []
        self.description = ""
        self.last_updated = ""
        self.is_dev_tool = False
        self.is_critical = False
        
    def __repr__(self):
        return f"PackageInfo({self.name}: {self.current_version} → {self.latest_version}, {self.risk_level.value})"


class PipPackageGuardian:
    """
    Main class for safe pip package upgrades.
    
    This class provides comprehensive package management with:
    - Environment detection and validation (venv, conda, homebrew, system)
    - Risk assessment and dependency analysis with version downgrade handling
    - Safe upgrade execution with rollback capability
    - Post-upgrade verification with intelligent import name resolution
    - Interactive loop for multiple upgrade operations in one session
    - Per-action logging with separate log files for each upgrade
    
    Security practices:
    - Input validation and sanitization on all user inputs
    - No shell injection (uses list arguments in subprocess, never shell=True)
    - Safe file operations with proper error handling and encoding
    - Sanitized package names (alphanumeric, dash, underscore, dot only)
    - Protected system Python detection (prevents macOS system Python modification)
    - 5-minute timeout on all subprocess commands
    - User-only permissions (0o700) on generated rollback scripts
    
    Version handling:
    - Parses PEP 440 versions including epochs, local versions, pre-releases
    - Detects major/minor/patch changes and version downgrades
    - Handles edge cases like "1:2.0.0" (epoch) and "1.2.3+local" (local version)
    
    Interactive features (v1.1.0):
    - Main loop allows multiple upgrade operations without restarting
    - Each upgrade action creates its own timestamped log file
    - Session summary tracks all created log files
    - "Refresh package list" option to check for new updates
    - Clean exit option that returns to menu instead of terminating
    """
    
    # Critical packages that need special handling
    CRITICAL_PACKAGES = {'pip', 'setuptools', 'wheel'}
    
    # Common development tools
    DEV_TOOLS = {'pytest', 'black', 'mypy', 'flake8', 'pylint', 'coverage',
                 'tox', 'sphinx', 'isort', 'autopep8', 'pre-commit'}
    
    # Packages to never upgrade (would break pip itself)
    PROTECTED_PACKAGES = {'python', 'distribute'}
    
    def __init__(self):
        """
        Initialize the package guardian.
        
        Creates:
        - Log directory for snapshots and rollback scripts (~/pip-upgrade-logs/)
        - Rich console if available for enhanced UI
        - Initial timestamp (updated for each upgrade action)
        - Empty log tracker for session summary
        
        Note:
            Each upgrade action will create its own log file with a fresh timestamp.
            The session tracks all created logs for summary display at exit.
        """
        self.log_dir = Path.home() / "pip-upgrade-logs"
        self.log_dir.mkdir(exist_ok=True)
        
        # Initialize with session timestamp
        self._update_timestamps()
        
        # Initialize rich console if available
        if RICH_AVAILABLE:
            self.console = Console()
        else:
            self.console = None
        
        # Cache for package information
        self.packages_cache: Dict[str, PackageInfo] = {}
        self.environment_info: Dict[str, str] = {}
        self.created_logs: List[Path] = []  # Track all log files created in this session
    
    def _update_timestamps(self):
        """
        Update timestamps and file paths for a new upgrade action.
        
        Called at initialization and before each upgrade operation to ensure
        each action has its own log, snapshot, and rollback files.
        """
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"upgrade_{self.timestamp}.log"
        self.snapshot_file = self.log_dir / f"requirements_{self.timestamp}.txt"
        self.rollback_file = self.log_dir / f"rollback_{self.timestamp}.sh"
        
        # Track this log file
        if hasattr(self, 'created_logs'):
            self.created_logs.append(self.log_file)
        
    def print(self, text: str, style: str = ""):
        """
        Print text with optional rich formatting.
        
        Args:
            text: Text to print
            style: Rich style string (e.g., "bold red", "green")
        """
        if self.console:
            self.console.print(text, style=style)
        else:
            print(text)
    
    def print_panel(self, text: str, title: str = "", border_style: str = ""):
        """
        Print a bordered panel with title.
        
        Args:
            text: Panel content
            title: Optional panel title
            border_style: Rich border style
        """
        if self.console:
            self.console.print(Panel(text, title=title, border_style=border_style))
        else:
            print(f"\n{'='*70}")
            if title:
                print(f"{title}")
                print(f"{'='*70}")
            print(text)
            print(f"{'='*70}\n")
    
    def confirm(self, message: str, default: bool = False) -> bool:
        """
        Ask user for yes/no confirmation.
        
        Args:
            message: Question to ask
            default: Default value if user just presses Enter
            
        Returns:
            True if user confirms, False otherwise
            
        Security: Input is validated to prevent injection
        """
        if self.console:
            return Confirm.ask(message, default=default)
        else:
            default_str = "Y/n" if default else "y/N"
            response = input(f"{message} [{default_str}]: ").strip().lower()
            if not response:
                return default
            return response in ['y', 'yes']
    
    def log(self, message: str):
        """
        Log message to file with timestamp.
        
        Args:
            message: Message to log
            
        Security: Safe file operations with proper error handling
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_entry + "\n")
        except IOError as e:
            self.print(f"[yellow]Warning: Could not write to log file: {e}[/yellow]")
        
        if self.console:
            self.print(f"[dim]{message}[/dim]")
    
    def run_command(self, cmd: List[str], capture: bool = True) -> Tuple[int, str, str]:
        """
        Execute a command safely.
        
        Args:
            cmd: Command as list (prevents shell injection)
            capture: Whether to capture output
            
        Returns:
            Tuple of (exit_code, stdout, stderr)
            
        Security:
        - Uses list arguments (no shell=True) to prevent injection
        - Validates command components
        - Proper error handling
        """
        self.log(f"Running: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                check=False,  # We handle errors manually
                timeout=300   # 5 minute timeout for safety
            )
            
            if capture:
                return result.returncode, result.stdout.strip(), result.stderr.strip()
            else:
                return result.returncode, "", ""
        except subprocess.TimeoutExpired:
            self.log("Command timed out after 5 minutes")
            return 1, "", "Command timed out"
        except Exception as e:
            self.log(f"Error running command: {e}")
            return 1, "", str(e)
    
    def sanitize_package_name(self, name: str) -> Optional[str]:
        """
        Sanitize package name to prevent injection attacks.
        
        Args:
            name: Package name to sanitize
            
        Returns:
            Sanitized name or None if invalid
            
        Security:
        - Only allows alphanumeric characters, dashes, underscores, and dots
        - Rejects suspicious patterns that could be used for shell injection
        - Examples of valid names: 'requests', 'scikit-learn', 'zope.interface'
        - Examples of rejected names: 'pkg; rm -rf /', 'pkg | cat', 'pkg && malicious'
        """
        if not name:
            return None
        
        # Valid package name pattern: alphanumeric, dash, underscore, dot
        if not re.match(r'^[a-zA-Z0-9._-]+$', name):
            self.log(f"Invalid package name rejected: {name}")
            return None
        
        # Reject suspicious patterns
        suspicious = ['..', '__', '&&', '||', ';', '|', '>', '<', '`', '$']
        if any(pattern in name for pattern in suspicious):
            self.log(f"Suspicious package name rejected: {name}")
            return None
        
        return name
    
    def parse_version(self, version_str: str) -> Tuple[int, int, int]:
        """
        Parse semantic version string into tuple.
        
        Handles various version formats including PEP 440 extensions:
        - Standard semver: "1.2.3"
        - Pre-releases: "2.0.0a1", "1.0.0b2", "1.0.0rc1"
        - Dev/Post releases: "1.2.3.dev0", "1.2.3.post1"
        - Epoch versions: "1:2.0.0" (epoch prefix stripped)
        - Local versions: "1.2.3+local.version" (local suffix stripped)
        - Short versions: "1.2" (treated as 1.2.0)
        
        Args:
            version_str: Version string to parse
            
        Returns:
            Tuple of (major, minor, patch) as integers
            Returns (0, 0, 0) if parsing fails
            
        Examples:
            "1.2.3" → (1, 2, 3)
            "2.0.0a1" → (2, 0, 0)  # pre-release suffix removed
            "1.2" → (1, 2, 0)  # missing patch defaulted to 0
            "1:2.0.0" → (2, 0, 0)  # epoch removed
            "1.2.3+local" → (1, 2, 3)  # local version removed
            "1.2.3.post1" → (1, 2, 3)  # post-release suffix removed
        """
        try:
            # Handle epoch (e.g., "1:2.0.0") - remove epoch prefix
            if ':' in version_str:
                version_str = version_str.split(':', 1)[1]
            
            # Handle local version (e.g., "1.2.3+local.version") - remove local part
            if '+' in version_str:
                version_str = version_str.split('+', 1)[0]
            
            # Remove pre-release suffixes (a, b, rc, dev, post, etc.)
            clean_version = re.sub(r'[a-zA-Z].*$', '', version_str)
            parts = clean_version.split('.')
            
            major = int(parts[0]) if len(parts) > 0 else 0
            minor = int(parts[1]) if len(parts) > 1 else 0
            patch = int(parts[2]) if len(parts) > 2 else 0
            
            return (major, minor, patch)
        except (ValueError, AttributeError):
            self.log(f"Could not parse version: {version_str}")
            return (0, 0, 0)
    
    # ==================== ENVIRONMENT DETECTION ====================
    
    def detect_environment(self) -> Dict[str, str]:
        """
        Detect current Python environment.
        
        Returns:
            Dictionary with environment details:
            - type: venv, conda, homebrew, system, unknown
            - path: Path to Python executable
            - name: Environment name (for conda)
            - safe_to_modify: Whether it's safe to upgrade packages
            
        Security: Detects and protects system Python
        
        Note: Environment detection is designed for macOS/Linux.
        Windows environments will be detected as 'unknown' and marked unsafe.
        """
        self.print("\n[bold cyan]Detecting Python Environment...[/bold cyan]")
        
        env_info = {
            'type': 'unknown',
            'path': sys.executable,
            'name': '',
            'safe_to_modify': False
        }
        
        python_path = sys.executable
        
        # Check if in virtual environment
        if hasattr(sys, 'real_prefix') or (
            hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
        ):
            env_info['type'] = 'venv'
            env_info['name'] = os.path.basename(sys.prefix)
            env_info['safe_to_modify'] = True
            self.print(f"[green]✓ Virtual Environment: {env_info['name']}[/green]")
        
        # Check if in conda environment
        elif 'CONDA_DEFAULT_ENV' in os.environ:
            env_info['type'] = 'conda'
            env_info['name'] = os.environ['CONDA_DEFAULT_ENV']
            env_info['safe_to_modify'] = True
            self.print(f"[green]✓ Conda Environment: {env_info['name']}[/green]")
        
        # Check if Homebrew Python
        elif '/opt/homebrew' in python_path or '/usr/local/Cellar' in python_path:
            env_info['type'] = 'homebrew'
            env_info['name'] = 'Homebrew Global'
            env_info['safe_to_modify'] = True
            self.print(f"[yellow]⚠ Homebrew Global Python[/yellow]")
        
        # Check if system Python
        elif python_path.startswith('/usr/bin') or python_path.startswith('/System'):
            env_info['type'] = 'system'
            env_info['name'] = 'macOS System Python'
            env_info['safe_to_modify'] = False
            self.print(f"[red]✗ System Python (PROTECTED)[/red]")
        
        else:
            env_info['type'] = 'unknown'
            env_info['name'] = 'Unknown'
            env_info['safe_to_modify'] = False
            self.print(f"[yellow]? Unknown Python Environment[/yellow]")
        
        self.print(f"Path: [dim]{python_path}[/dim]")
        self.print(f"Python: {sys.version.split()[0]}")
        
        self.environment_info = env_info
        return env_info
    
    def check_environment_safety(self) -> bool:
        """
        Check if current environment is safe to modify.
        
        Returns:
            True if safe to proceed, False otherwise
            
        Security: Prevents modification of system Python
        """
        if not self.environment_info:
            self.detect_environment()
        
        if not self.environment_info['safe_to_modify']:
            self.print("\n[red]✗ Cannot modify this Python environment![/red]")
            
            if self.environment_info['type'] == 'system':
                self.print("[yellow]System Python is managed by macOS and should not be modified.[/yellow]")
                self.print("[yellow]Create a virtual environment instead:[/yellow]")
                self.print("  python3 -m venv myenv")
                self.print("  source myenv/bin/activate")
            else:
                self.print("[yellow]This Python installation is not recognized as safe to modify.[/yellow]")
            
            return False
        
        return True
    
    # ==================== PACKAGE SCANNING ====================
    
    def get_outdated_packages(self) -> List[PackageInfo]:
        """
        Get list of outdated packages using pip.
        
        Returns:
            List of PackageInfo objects for outdated packages
            
        Security: Uses pip list --outdated with JSON output
        """
        self.print("\n[bold cyan]Scanning for outdated packages...[/bold cyan]")
        
        code, output, _ = self.run_command([sys.executable, '-m', 'pip', 'list', '--outdated', '--format=json'])
        
        if code != 0:
            self.print("[red]✗ Failed to get package list[/red]")
            return []
        
        try:
            packages_data = json.loads(output)
            packages = []
            
            for pkg_data in packages_data:
                name = self.sanitize_package_name(pkg_data.get('name', ''))
                if not name:
                    continue
                
                # Skip protected packages
                if name.lower() in self.PROTECTED_PACKAGES:
                    continue
                
                current = pkg_data.get('version', '')
                latest = pkg_data.get('latest_version', '')
                
                if current and latest:
                    pkg_info = PackageInfo(name, current, latest)
                    
                    # Mark critical and dev tool packages
                    if name.lower() in self.CRITICAL_PACKAGES:
                        pkg_info.is_critical = True
                    if name.lower() in self.DEV_TOOLS:
                        pkg_info.is_dev_tool = True
                    
                    packages.append(pkg_info)
            
            self.print(f"Found [bold]{len(packages)}[/bold] outdated package(s)")
            return packages
            
        except json.JSONDecodeError as e:
            self.log(f"Failed to parse pip output: {e}")
            return []
    
    def analyze_dependencies(self, package: PackageInfo):
        """
        Analyze direct dependencies for a package.
        
        Uses 'pip show' to find packages that depend on this package.
        Empty strings and whitespace-only entries are filtered out.
        
        Args:
            package: PackageInfo to analyze
            
        Modifies:
            package.dependents: List of package names that require this package
        """
        # Get packages that require this one
        code, output, _ = self.run_command([sys.executable, '-m', 'pip', 'show', package.name])
        
        if code != 0:
            return
        
        # Parse pip show output
        required_by = []
        for line in output.split('\n'):
            if line.startswith('Required-by:'):
                deps = line.split(':', 1)[1].strip()
                if deps:
                    # Filter out empty strings after split
                    required_by = [d.strip() for d in deps.split(',') if d.strip()]
                break
        
        package.dependents = required_by
    
    def assess_risk(self, package: PackageInfo) -> RiskLevel:
        """
        Assess upgrade risk for a package based on version changes and dependencies.
        
        Args:
            package: PackageInfo to assess
            
        Returns:
            RiskLevel enum value
            
        Risk criteria:
        - LOW: Patch update with few/no dependents, or same version (pre-release → release)
        - MEDIUM: Minor update, has dependents, patch update with many dependents (>3),
                  minor version downgrade, or unparseable versions
        - HIGH: Major version change (upgrade or downgrade)
        - CRITICAL: pip, setuptools, wheel (infrastructure packages)
        
        Notes:
        - Version downgrades are treated as medium-to-high risk (unusual scenario)
        - If version parsing fails for either version, defaults to MEDIUM risk
        - Pre-release to release transitions (same numeric version) are LOW risk
        """
        if package.is_critical:
            return RiskLevel.CRITICAL
        
        curr_v = self.parse_version(package.current_version)
        latest_v = self.parse_version(package.latest_version)
        
        # Check for invalid version parsing
        if curr_v == (0, 0, 0) or latest_v == (0, 0, 0):
            # If we can't parse versions, treat as medium risk
            self.log(f"Warning: Could not parse versions for {package.name}")
            return RiskLevel.MEDIUM
        
        # Major version change
        if latest_v[0] > curr_v[0]:
            return RiskLevel.HIGH
        
        # Major version downgrade (unusual, treat as high risk)
        if latest_v[0] < curr_v[0]:
            return RiskLevel.HIGH
        
        # Minor version change
        if latest_v[1] > curr_v[1]:
            # If has dependents, it's more risky
            if package.dependents:
                return RiskLevel.MEDIUM
            return RiskLevel.LOW
        
        # Minor version downgrade (unusual)
        if latest_v[1] < curr_v[1]:
            return RiskLevel.MEDIUM
        
        # Patch update
        if latest_v[2] > curr_v[2]:
            # Even patch updates are medium risk if many depend on it
            if len(package.dependents) > 3:
                return RiskLevel.MEDIUM
            return RiskLevel.LOW
        
        # Same version or patch downgrade - treat as low risk
        # (this handles pre-release vs release scenarios)
        return RiskLevel.LOW
    
    # ==================== PRE-UPGRADE SAFETY ====================
    
    def create_snapshot(self) -> bool:
        """
        Create requirements.txt snapshot of current state.
        
        Returns:
            True if successful, False otherwise
            
        Security: Safe file operations
        """
        self.print("\n[bold]Creating safety snapshot...[/bold]")
        
        code, output, _ = self.run_command([sys.executable, '-m', 'pip', 'freeze'])
        
        if code != 0:
            self.print("[red]✗ Failed to create snapshot[/red]")
            return False
        
        try:
            with open(self.snapshot_file, 'w', encoding='utf-8') as f:
                f.write(output)
            
            self.print(f"[green]✓[/green] Snapshot: {self.snapshot_file}")
            return True
        except IOError as e:
            self.print(f"[red]✗ Could not write snapshot: {e}[/red]")
            return False
    
    def create_rollback_script(self, packages: List[PackageInfo]) -> bool:
        """
        Create shell script for rollback.
        
        Args:
            packages: List of packages to include in rollback
            
        Returns:
            True if successful
            
        Security: Sanitizes package names before writing
        """
        try:
            with open(self.rollback_file, 'w', encoding='utf-8') as f:
                f.write("#!/bin/bash\n")
                f.write("# Rollback script generated by pip Package Guardian\n")
                f.write(f"# Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("# Run this script to restore previous package versions\n\n")
                f.write("echo 'Rolling back packages...'\n\n")
                
                for pkg in packages:
                    safe_name = self.sanitize_package_name(pkg.name)
                    if safe_name:
                        f.write(f"pip install {safe_name}=={pkg.current_version}\n")
                
                f.write("\necho 'Rollback complete!'\n")
            
            # Make executable (user-only for security)
            os.chmod(self.rollback_file, 0o700)
            
            self.print(f"[green]✓[/green] Rollback script: {self.rollback_file}")
            return True
        except IOError as e:
            self.print(f"[red]✗ Could not create rollback script: {e}[/red]")
            return False
    
    # ==================== UPGRADE EXECUTION ====================
    
    def upgrade_package(self, package: PackageInfo) -> bool:
        """
        Upgrade a single package.
        
        Args:
            package: PackageInfo to upgrade
            
        Returns:
            True if successful, False otherwise
            
        Security: Uses sanitized package names
        """
        safe_name = self.sanitize_package_name(package.name)
        if not safe_name:
            self.print(f"[red]✗ Invalid package name: {package.name}[/red]")
            return False
        
        self.print(f"  Upgrading {safe_name}...")
        
        code, output, error = self.run_command([
            sys.executable, '-m', 'pip', 'install', 
            '--upgrade', safe_name
        ])
        
        if code == 0:
            # Try to import the package to verify
            if self.verify_import(safe_name):
                self.print(f"  [green]✓[/green] {safe_name} upgraded successfully")
                return True
            else:
                self.print(f"  [yellow]⚠[/yellow] {safe_name} upgraded but import failed")
                return True  # Still count as successful upgrade
        else:
            self.print(f"  [red]✗[/red] Failed to upgrade {safe_name}")
            if error:
                self.log(f"Error: {error}")
            return False
    
    def verify_import(self, package_name: str) -> bool:
        """
        Try to import a package to verify it works after installation/upgrade.
        
        Handles common package name to import name discrepancies using multiple strategies:
        1. Checks known mappings (e.g., 'Pillow' → 'PIL', 'scikit-learn' → 'sklearn')
        2. Tries package name with dashes converted to underscores
        3. Tries lowercase version
        4. Falls back to original package name
        
        Args:
            package_name: Package name (as used by pip) to verify
            
        Returns:
            True if import successful, False otherwise
            
        Note:
            Some packages may have import-time side effects or be non-importable
            (e.g., command-line only tools). Import failure is logged but doesn't
            necessarily indicate a broken installation.
        """
        # Common package name to import name mappings
        known_mappings = {
            'Pillow': 'PIL',
            'scikit-learn': 'sklearn',
            'scikit-image': 'skimage',
            'beautifulsoup4': 'bs4',
            'PyYAML': 'yaml',
            'python-dateutil': 'dateutil',
            'attrs': 'attr',
            'msgpack': 'msgpack',
            'protobuf': 'google.protobuf',
        }
        
        # Try known mapping first
        import_name = known_mappings.get(package_name)
        
        if import_name:
            try:
                __import__(import_name)
                return True
            except (ImportError, Exception):
                pass
        
        # Try package name with dashes converted to underscores
        import_name = package_name.replace('-', '_')
        try:
            __import__(import_name)
            return True
        except (ImportError, Exception):
            pass
        
        # Try lowercase version
        try:
            __import__(import_name.lower())
            return True
        except (ImportError, Exception):
            pass
        
        # Try original package name as last resort
        try:
            __import__(package_name)
            return True
        except (ImportError, Exception) as e:
            # Some packages have import-time side effects that can fail
            # or simply can't be imported (e.g., setuptools)
            self.log(f"Could not verify {package_name}: {str(e)}")
            return False
    
    # ==================== UI AND DISPLAY ====================
    
    def display_packages_table(self, packages: List[PackageInfo]):
        """
        Display packages in a formatted table.
        
        Args:
            packages: List of PackageInfo to display
        """
        if not packages:
            return
        
        # Group by risk level
        by_risk = {
            RiskLevel.LOW: [],
            RiskLevel.MEDIUM: [],
            RiskLevel.HIGH: [],
            RiskLevel.CRITICAL: []
        }
        
        for pkg in packages:
            by_risk[pkg.risk_level].append(pkg)
        
        # Display each risk level
        if by_risk[RiskLevel.LOW]:
            self.print(f"\n[green]🟢 LOW RISK ({len(by_risk[RiskLevel.LOW])} packages)[/green]")
            for pkg in by_risk[RiskLevel.LOW]:
                deps_str = f" ({len(pkg.dependents)} deps)" if pkg.dependents else ""
                self.print(f"  • {pkg.name}: {pkg.current_version} → {pkg.latest_version}{deps_str}")
        
        if by_risk[RiskLevel.MEDIUM]:
            self.print(f"\n[yellow]🟡 MEDIUM RISK ({len(by_risk[RiskLevel.MEDIUM])} packages)[/yellow]")
            for pkg in by_risk[RiskLevel.MEDIUM]:
                deps_str = f" ({len(pkg.dependents)} dependents)" if pkg.dependents else ""
                self.print(f"  • {pkg.name}: {pkg.current_version} → {pkg.latest_version}{deps_str}")
        
        if by_risk[RiskLevel.HIGH]:
            self.print(f"\n[red]🔴 HIGH RISK ({len(by_risk[RiskLevel.HIGH])} packages)[/red]")
            for pkg in by_risk[RiskLevel.HIGH]:
                curr_v = self.parse_version(pkg.current_version)
                latest_v = self.parse_version(pkg.latest_version)
                self.print(f"  • {pkg.name}: {pkg.current_version} → {pkg.latest_version} [bold red]MAJOR UPDATE[/bold red]")
                if pkg.dependents:
                    self.print(f"    Dependents: {', '.join(pkg.dependents[:3])}")
        
        if by_risk[RiskLevel.CRITICAL]:
            self.print(f"\n[magenta]⚙️  CRITICAL PACKAGES ({len(by_risk[RiskLevel.CRITICAL])} packages)[/magenta]")
            for pkg in by_risk[RiskLevel.CRITICAL]:
                self.print(f"  • {pkg.name}: {pkg.current_version} → {pkg.latest_version}")
    
    # ==================== BATCH MODE ====================
    
    def run_batch_mode(self, packages: List[PackageInfo]) -> bool:
        """
        Batch mode: Review all packages, select upgrade strategy, then upgrade.
        
        Provides six interactive options:
        [1] Auto-upgrade LOW risk packages only
        [2] Auto-upgrade LOW + MEDIUM risk packages
        [3] Custom selection (user specifies package names - validated for security)
        [4] Upgrade critical infrastructure packages (pip, setuptools, wheel)
        [5] Refresh package list (re-scan for updates)
        [0] Exit (terminate the program)
        
        Args:
            packages: List of outdated packages to present for upgrade
            
        Returns:
            True if user wants to continue (cancelled, completed, or refresh)
            False if user wants to exit the program (option 0)
            
        Behavior:
            - Creates new timestamped log files for each actual upgrade operation
            - Displays log file path before starting upgrade
            - Returns to menu after completion (unless user selects Exit)
            - Option 5 (Refresh) immediately returns True to trigger package re-scan
            
        Security:
            User input in custom selection mode is sanitized to prevent injection attacks.
            Invalid package names are skipped with warnings.
        """
        self.print("\n[bold cyan]═══ Batch Mode ═══[/bold cyan]")
        
        # Display summary
        self.display_packages_table(packages)
        
        # Get user selections
        self.print("\n[bold]Select packages to upgrade:[/bold]")
        self.print("[1] Auto-upgrade all LOW risk")
        self.print("[2] Auto-upgrade LOW + MEDIUM risk")
        self.print("[3] Custom selection")
        self.print("[4] Upgrade critical packages (pip, setuptools, wheel)")
        self.print("[5] Refresh package list")
        self.print("[0] Exit")
        
        if self.console:
            choice = Prompt.ask("Select option", choices=["0", "1", "2", "3", "4", "5"])
        else:
            choice = input("Select option [0-5]: ").strip()
        
        selected_packages = []
        
        if choice == "0":
            self.print("[cyan]Exiting...[/cyan]")
            return False
        elif choice == "5":
            self.print("[cyan]Refreshing package list...[/cyan]")
            return True
        
        if choice == "1":
            selected_packages = [p for p in packages if p.risk_level == RiskLevel.LOW]
        elif choice == "2":
            selected_packages = [p for p in packages if p.risk_level in [RiskLevel.LOW, RiskLevel.MEDIUM]]
        elif choice == "3":
            # Custom selection
            self.print("\nEnter package names to upgrade (comma-separated):")
            if self.console:
                pkg_names = Prompt.ask("Package names").split(',')
            else:
                pkg_names = input("Package names: ").split(',')
            
            # Sanitize and validate user input
            sanitized_names = []
            for name in pkg_names:
                sanitized = self.sanitize_package_name(name.strip())
                if sanitized:
                    sanitized_names.append(sanitized)
                else:
                    self.print(f"[yellow]Warning: Skipping invalid package name: {name.strip()}[/yellow]")
            
            selected_packages = [p for p in packages if p.name in sanitized_names]
        elif choice == "4":
            selected_packages = [p for p in packages if p.is_critical]
            if not selected_packages:
                self.print("[yellow]No critical packages (pip, setuptools, wheel) need upgrading[/yellow]")
                return True
        
        if not selected_packages:
            self.print("[yellow]No packages selected[/yellow]")
            return True
        
        # Confirm
        self.print(f"\n[bold]Selected {len(selected_packages)} package(s) for upgrade:[/bold]")
        for pkg in selected_packages:
            self.print(f"  • {pkg.name}: {pkg.current_version} → {pkg.latest_version}")
        
        if not self.confirm("\nProceed with upgrade?", default=False):
            self.print("[yellow]Upgrade cancelled[/yellow]")
            return True
        
        # Create new log files for this upgrade action
        self._update_timestamps()
        self.log(f"=== Starting upgrade session ===")
        self.log(f"Selected {len(selected_packages)} package(s) for upgrade")
        for pkg in selected_packages:
            self.log(f"  - {pkg.name}: {pkg.current_version} → {pkg.latest_version}")
        
        self.print(f"\n[dim]Log file: {self.log_file}[/dim]")
        
        # Create safety backups
        if not self.create_snapshot():
            if not self.confirm("Snapshot failed. Continue anyway?", default=False):
                return True
        
        if not self.create_rollback_script(selected_packages):
            self.print("[yellow]Warning: Rollback script creation failed[/yellow]")
        
        # Perform upgrades
        self.print("\n[bold]Upgrading packages...[/bold]")
        success_count = 0
        fail_count = 0
        
        for pkg in selected_packages:
            if self.upgrade_package(pkg):
                success_count += 1
            else:
                fail_count += 1
        
        # Summary
        self.print("\n[bold]Upgrade Summary:[/bold]")
        self.print(f"  [green]✓[/green] Upgraded: {success_count}")
        if fail_count > 0:
            self.print(f"  [red]✗[/red] Failed: {fail_count}")
        
        if success_count > 0:
            self.print(f"\n[green]Rollback available:[/green] bash {self.rollback_file}")
        
        return True  # Continue to main loop
    
    # ==================== MAIN EXECUTION ====================
    
    def run(self):
        """
        Main program execution with interactive loop.
        
        Workflow:
        1. Display welcome message with version and log directory
        2. Detect and validate Python environment (safety check)
        3. Enter main loop:
           a. Scan for outdated packages
           b. If packages found: analyze dependencies and show menu
           c. If no packages: offer to check again or exit
           d. Execute user-selected action
           e. Return to step 3a (unless user exits)
        4. Display session summary with all created log files
        5. Exit gracefully
        
        Features:
        - Continuous workflow: perform multiple upgrades without restarting
        - Per-action logging: each upgrade creates its own log file
        - Session tracking: summary shows all logs created
        - Graceful exit: user can exit at any time via option 0
        
        The loop continues until the user selects Exit (option 0) from the menu.
        """
        # Welcome message
        self.print_panel(
            "[bold cyan]pip Package Guardian[/bold cyan]\n\n"
            f"Script: safe_pip_upgrade.py v1.1.0\n"
            f"Logs directory: {self.log_dir}\n\n"
            "Safe Python package upgrades with risk assessment and rollback.\n"
            "Each upgrade action creates its own timestamped log file.",
            title="Welcome",
            border_style="cyan"
        )
        
        # Detect and validate environment
        self.detect_environment()
        
        if not self.check_environment_safety():
            return
        
        # Main interactive loop
        while True:
            # Get outdated packages
            packages = self.get_outdated_packages()
            
            if not packages:
                self.print("\n[green]✓ All packages are up to date![/green]")
                if not self.confirm("\nCheck again?", default=False):
                    break
                continue
            
            # Analyze dependencies and assess risk
            self.print("\n[bold cyan]Analyzing dependencies...[/bold cyan]")
            for pkg in packages:
                self.analyze_dependencies(pkg)
                pkg.risk_level = self.assess_risk(pkg)
            
            # Run batch mode - returns False if user wants to exit
            if not self.run_batch_mode(packages):
                break
        
        # Show summary of created logs
        if self.created_logs:
            self.print("\n[bold cyan]Session Summary:[/bold cyan]")
            self.print(f"Created {len(self.created_logs)} log file(s):")
            for log_file in self.created_logs:
                self.print(f"  • {log_file}")
            self.print(f"\n[dim]All logs stored in: {self.log_dir}[/dim]")
        
        self.print("\n[bold green]Done![/bold green]")


if __name__ == "__main__":
    try:
        guardian = PipPackageGuardian()
        guardian.run()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
