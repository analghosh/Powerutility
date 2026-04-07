#!/usr/bin/env python3
"""
Setup Environment Files
Copy .env.example files to service directories
"""

import os
import shutil
from pathlib import Path


def copy_env_files():
    """Copy environment example files to service directories"""
    
    # Define source and destination paths
    env_mappings = {
        "env_examples/auth.env.example": "services/auth/.env.example",
        "env_examples/ingestion.env.example": "services/ingestion/.env.example", 
        "env_examples/processing.env.example": "services/processing/.env.example",
        "env_examples/scheduler.env.example": "services/scheduler/.env.example"
    }
    
    print("Setting up environment files...")
    
    for source, destination in env_mappings.items():
        try:
            # Create destination directory if it doesn't exist
            dest_path = Path(destination)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Copy the file
            shutil.copy2(source, destination)
            print(f"✅ Copied {source} to {destination}")
            
        except FileNotFoundError:
            print(f"❌ Source file not found: {source}")
        except Exception as e:
            print(f"❌ Error copying {source}: {e}")
    
    print("\nEnvironment files setup complete!")
    print("\nNext steps:")
    print("1. Copy .env.example to .env in each service directory")
    print("2. Update the configuration values in each .env file")
    print("3. Install dependencies: pip install -r requirements-dev.txt")
    print("4. Run services: python run_services.py <service_name>")


if __name__ == "__main__":
    copy_env_files() 