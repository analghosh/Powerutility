#!/usr/bin/env python3
"""
One-time project installation script
Run this once to install the project in editable mode and avoid repeated installations
"""

import sys
import subprocess
from pathlib import Path

def main():
    """Install the project in editable mode"""
    project_root = Path(__file__).parent
    
    print("📦 Installing EPMS Backend project in editable mode...")
    print(f"Project root: {project_root}")
    
    try:
        # Install the project in editable mode
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-e", "."
        ])
        
        # Create marker file to indicate successful installation
        marker_file = project_root / '.libs_installed'
        marker_file.touch()
        
        print("✅ Project installed successfully!")
        print("Now you can run services without repeated dependency installations.")
        
        # Test the installation
        print("\n🧪 Testing installation...")
        try:
            import libs.core.cron_manager
            print("✅ libs package is importable")
        except ImportError as e:
            print(f"⚠️ Warning: libs package still not importable: {e}")
            
    except subprocess.CalledProcessError as e:
        print(f"❌ Installation failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()