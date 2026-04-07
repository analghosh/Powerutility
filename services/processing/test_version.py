#!/usr/bin/env python3
"""
Test script to verify version and environment detection
"""

import os
import sys
from pathlib import Path

# Add the app directory to Python path
app_dir = Path(__file__).parent / "app"
sys.path.insert(0, str(app_dir))

from app.core.version import detect_environment, get_api_paths, get_version_info

def test_environments():
    """Test different environment configurations"""
    
    print("🧪 Testing Environment Detection\n")
    
    # Test 1: Local environment (default)
    print("1️⃣ Local Environment (default):")
    os.environ.pop("DOCKER_CONTAINER", None)
    os.environ.pop("SERVER_DEPLOYMENT", None)
    os.environ.pop("PROCESSING_PORT", None)
    
    env = detect_environment()
    paths = get_api_paths()
    version_info = get_version_info()
    
    print(f"   Environment: {env}")
    print(f"   Base Path: '{paths['base_path']}'")
    print(f"   V1 Prefix: {paths['v1_prefix']}")
    print(f"   V2 Mount: {paths['v2_mount']}")
    print(f"   Health: {paths['health']}")
    print()
    
    # Test 2: Docker environment
    print("2️⃣ Docker Environment:")
    os.environ["DOCKER_CONTAINER"] = "true"
    
    env = detect_environment()
    paths = get_api_paths()
    
    print(f"   Environment: {env}")
    print(f"   Base Path: '{paths['base_path']}'")
    print(f"   V1 Prefix: {paths['v1_prefix']}")
    print(f"   V2 Mount: {paths['v2_mount']}")
    print(f"   Health: {paths['health']}")
    print()
    
    # Test 3: Server environment
    print("3️⃣ Server Environment:")
    os.environ.pop("DOCKER_CONTAINER", None)
    os.environ["SERVER_DEPLOYMENT"] = "true"
    
    env = detect_environment()
    paths = get_api_paths()
    
    print(f"   Environment: {env}")
    print(f"   Base Path: '{paths['base_path']}'")
    print(f"   V1 Prefix: {paths['v1_prefix']}")
    print(f"   V2 Mount: {paths['v2_mount']}")
    print(f"   Health: {paths['health']}")
    print()
    
    # Test 4: Processing port detection
    print("4️⃣ Processing Port Detection (Docker):")
    os.environ.pop("SERVER_DEPLOYMENT", None)
    os.environ["PROCESSING_PORT"] = "8003"
    
    env = detect_environment()
    paths = get_api_paths()
    
    print(f"   Environment: {env}")
    print(f"   Base Path: '{paths['base_path']}'")
    print(f"   V1 Prefix: {paths['v1_prefix']}")
    print(f"   V2 Mount: {paths['v2_mount']}")
    print()
    
    # Clean up
    os.environ.pop("PROCESSING_PORT", None)
    
    print("✅ All tests completed!")
    print("\n📋 Version Info:")
    for key, value in version_info.items():
        if key != "paths":
            print(f"   {key}: {value}")

if __name__ == "__main__":
    test_environments()