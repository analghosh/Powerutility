#!/usr/bin/env python3
"""
EPMS Backend Services Runner
Automatically installs dependencies and runs any service
"""

import sys
import subprocess
import argparse
import os
import time
import threading
import logging
from pathlib import Path
from typing import Dict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global variables
processes = []
SERVICES = {
    "auth": {
        "name": "Auth Service",
        "port": 8001,
        "host": "0.0.0.0",
        "module": "services.auth.app.main:app",
        "command": ["python", "-m", "services.auth.app.main"],
        "requirements": "services/auth/requirements.txt"
    },
    "ingestion": {
        "name": "Ingestion Service", 
        "port": 8002,
        "host": "0.0.0.0",
        "module": "services.ingestion.app.main:app",
        "command": ["python", "-m", "services.ingestion.app.main"],
        "requirements": "services/ingestion/requirements.txt"
    },
    "processing": {
        "name": "Processing Service",
        "port": 8003,
        "host": "0.0.0.0", 
        "module": "services.processing.app.main:app",
        "command": ["python", "-m", "services.processing.app.main"],
        "requirements": "services/processing/requirements.txt"
    },
    "scheduler": {
        "name": "Scheduler Service",
        "port": 8004,
        "host": "0.0.0.0",
        "module": "services.scheduler.app.main:app",
        "command": ["python", "-m", "services.scheduler.app.main"],
        "requirements": "services/scheduler/requirements.txt"
    }
}

def error_print(message: str):
    """Print error message in red"""
    print(f"\033[91m{message}\033[0m")

def cleanup_temp_files():
    """Clean up temporary files"""
    try:
        import shutil
        temp_dirs = ["tmp", "uploads", "logs"]
        for temp_dir in temp_dirs:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                logger.info("Cleaned up %s", temp_dir)
    except Exception as e:
        logger.error("Error cleaning up temp files: %s", e)

def check_requirements_installed(requirements_file: str) -> bool:
    """Check if requirements are already installed"""
    try:
        import importlib
        import pkg_resources
        requirements_path = Path(requirements_file)
        if not requirements_path.exists():
            return False
        
        with open(requirements_path, 'r', encoding='utf-8') as f:
            requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        # Check key packages only (not all, as some might be optional)
        key_packages = ['fastapi', 'uvicorn', 'pydantic', 'sqlalchemy']
        missing_key_packages = []
        
        for req in requirements:
            package_name = req.split('==')[0].split('>=')[0].split('<=')[0].split('[')[0].strip()
            
            # Skip empty lines and comments
            if not package_name or package_name.startswith('#'):
                continue
                
            # Only check key packages to avoid false negatives
            if package_name.lower() in key_packages:
                try:
                    # Try using pkg_resources first (more reliable)
                    pkg_resources.get_distribution(package_name)
                except (pkg_resources.DistributionNotFound, pkg_resources.VersionConflict):
                    try:
                        # Fallback to importlib
                        importlib.import_module(package_name.replace('-', '_'))
                    except ImportError:
                        missing_key_packages.append(package_name)
                        logger.info("Missing key package: %s", package_name)
        
        # If any key packages are missing, return False
        if missing_key_packages:
            return False
            
        # Also check if libs package is importable (project-specific check)
        try:
            import libs.core.cron_manager
            return True
        except ImportError:
            logger.info("Missing libs package - needs installation")
            return False
            
    except Exception as e:
        logger.warning("Error checking requirements: %s", e)
        return False

def stream_output(process, service_name):
    """Stream output from a process"""
    try:
        for line in iter(process.stdout.readline, ''):
            if line:
                print(f"[{service_name}] {line.strip()}")
    except Exception as e:
        logger.error("Error streaming output for %s: %s", service_name, e)

def install_dependencies():
    """Install project dependencies if not already installed"""
    try:
        # Check if libs is importable
        import libs.core.cron_manager  # pylint: disable=unused-import
        print("[OK] Dependencies already installed")
        return True
    except ImportError:
        # Dependencies not installed, check if we need to install
        pass
    
    # Check if we've recently installed (avoid repeated installations)
    install_marker = Path('.libs_installed')
    if install_marker.exists():
        # Check if marker is recent (less than 1 hour old)
        import time
        marker_age = time.time() - install_marker.stat().st_mtime
        if marker_age < 3600:  # 1 hour
            print("[OK] Dependencies were recently installed, skipping...")
            return True
    
    # Get requirements file path
    requirements_file = "requirements.txt"
    requirements_path = Path(requirements_file)
    if not requirements_path.exists():
        return False
    
    try:
        print("📦 Installing project dependencies...")
        import subprocess
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-e", "."
        ], timeout=120)
        
        # Create marker file
        install_marker.touch()
        print("✅ Project dependencies installed successfully")
        return True
    except Exception as e:
        logger.error("Failed to install project dependencies: %s", e)
        return False

def install_requirements(service_key: str, service_config: Dict, force: bool = False) -> bool:
    """Install requirements for a specific service"""
    requirements_file = service_config.get("requirements")
    if not requirements_file:
        logger.warning("No requirements file specified for %s", service_key)
        return True
    
    requirements_path = Path(requirements_file)
    if not requirements_path.exists():
        logger.warning("Requirements file not found: %s", requirements_path)
        return True
    
    # Check if requirements are already installed (unless force is True)
    if not force and check_requirements_installed(requirements_file):
        logger.info("Requirements already installed for %s, skipping installation...", service_config['name'])
        return True
    
    try:
        logger.info("Installing requirements for %s...", service_config['name'])
        
        # Read requirements file
        with open(requirements_path, 'r', encoding='utf-8') as f:
            requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        # Install packages one by one to handle failures gracefully
        failed_packages = []
        for req in requirements:
            try:
                logger.info("Installing %s...", req)
                subprocess.run([
                    sys.executable, "-m", "pip", "install", req
                ], capture_output=True, text=True, check=True)
                logger.info("[OK] %s installed successfully", req)
            except subprocess.CalledProcessError as e:
                logger.warning("[WARN] Failed to install %s: %s", req, e.stderr)
                failed_packages.append(req)
                # Continue with other packages
                continue
        
        if failed_packages:
            logger.warning(f"[WARN] Some packages failed to install: {failed_packages}")
            logger.info("This is normal for packages requiring Rust compilation.")
            logger.info("The service should still work with the installed packages.")
        
        logger.info(f"[OK] Requirements installation completed for {service_config['name']}")
        
        # Clean up temporary files
        cleanup_temp_files()
        
        return True
        
    except Exception as e:
        error_print(f"Unexpected error installing requirements for {service_config['name']}: {e}")
        
        # Clean up temporary files even on error
        cleanup_temp_files()
        
        return False

def install_all_requirements(force: bool = False) -> bool:
    """Install requirements for all services"""
    logger.info("[INSTALL] Installing requirements for all services...")
    
    success = True
    for service_key, service_config in SERVICES.items():
        if not install_requirements(service_key, service_config, force):
            success = False
    
    if success:
        logger.info("[OK] All requirements installed successfully!")
    else:
        error_print("Some requirements failed to install!")
    
    return success

def run_service(
    service_key: str,
    service_config: Dict,
    reload_enabled: bool = False,
    attach_console: bool = False,
) -> subprocess.Popen:
    try:
        logger.info("Starting %s on port %s", service_config['name'], service_config['port'])
        
        # Check if service is already running and gracefully shutdown
        if not graceful_shutdown_service(service_key):
            logger.warning("[WARN] Could not gracefully shutdown %s, attempting to start anyway...", service_config['name'])
        
        # Install requirements first
        if not install_requirements(service_key, service_config):
            raise Exception(f"Failed to install requirements for {service_config['name']}")
        
        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd()

        # Standard uvicorn startup for all services
        cmd = [
            sys.executable, "-m", "uvicorn",
            service_config["module"],
            "--host", service_config["host"],
            "--port", str(service_config["port"])
        ]
        if reload_enabled:
            cmd.append("--reload")

        if attach_console:
            process = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL)
        else:
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )

        if not attach_console:
            threading.Thread(
                target=stream_output,
                args=(process.stdout, f"{service_key}-stdout"),
                daemon=True,
            ).start()
            threading.Thread(
                target=stream_output,
                args=(process.stderr, f"{service_key}-stderr"),
                daemon=True,
            ).start()

        processes.append(process)
        logger.info("%s started with PID %s", service_config['name'], process.pid)
        
        # Initialize queue recovery for services that use audit queue
        if service_key in ["auth", "processing", "ingestion"]:  # Services that use audit logging
            try:
                logger.info("🔄 Initializing audit queue recovery for %s...", service_config['name'])
                
                # Add a small delay to ensure service is fully started
                time.sleep(2)
                
                # Import and call queue startup recovery
                try:
                    # Add the libs directory to Python path for importing
                    libs_path = os.path.join(os.getcwd(), 'libs')
                    if libs_path not in sys.path:
                        sys.path.insert(0, libs_path)
                    
                    # Use absolute import from the project root
                    from libs.audit_queue.startup import startup_queue_recovery
                    result = startup_queue_recovery()
                    
                    if "error" in result:
                        logger.warning("[WARN] Queue recovery warning for %s: %s", service_config['name'], result['error'])
                    else:
                        logger.info("[OK] Queue recovery completed for %s: restarted %s jobs, requeued %s jobs", 
                                   service_config['name'], result['restarted'], result['requeued'])
                        
                except ImportError as e:
                    logger.warning("[WARN] Could not import audit queue recovery module: %s", e)
                except Exception as e:
                    logger.warning("[WARN] Queue recovery failed for %s: %s", service_config['name'], e)
                    
            except Exception as e:
                logger.warning("[WARN] Error during queue initialization for %s: %s", service_config['name'], e)
        
        return process
    except Exception as e:
        error_print(f"Failed to start {service_config['name']}: {e}")
        raise

# Add this function to start Celery workers
def start_celery_worker():
    """Start Celery worker for audit queue processing"""
    try:
        logger.info("🔄 Starting Celery worker for audit queue...")
        
        # Start Celery worker as a subprocess
        celery_cmd = [
            sys.executable, "-m", "celery", 
            "-A", "libs.audit_queue.queue_manager.celery_app",
            "worker", "--loglevel=info"
        ]
        
        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd()
        
        celery_process = subprocess.Popen(
            celery_cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )
        
        processes.append(celery_process)
        logger.info(f"[OK] Celery worker started with PID {celery_process.pid}")
        return celery_process
        
    except Exception as e:
        logger.error(f"[ERROR] Failed to start Celery worker: {e}")
        return None

# Modify run_all_services to include Celery worker
def run_all_services(reload_enabled: bool = False):
    logger.info("Starting all EPMS services...")
    
    # Start Celery worker first
    start_celery_worker()
    time.sleep(2)  # Wait for worker to initialize
    
    # Then start all other services
    threads = []
    for service_key, service_config in SERVICES.items():
        thread = threading.Thread(
            target=run_service,
            args=(service_key, service_config, reload_enabled, False),
            daemon=True
        )
        threads.append(thread)
        thread.start()
        time.sleep(1)
    for thread in threads:
        thread.join()

def run_single_service(service_name: str, reload_enabled: bool = False):
    if service_name not in SERVICES:
        error_print(f"Unknown service: {service_name}")
        logger.info(f"Available services: {', '.join(SERVICES.keys())}")
        return
    service_config = SERVICES[service_name]
    logger.info(f"Starting {service_config['name']}...")
    try:
        run_service(service_name, service_config, reload_enabled, True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info(f"Stopping {service_config['name']}...")
        stop_all_services()

def stop_all_services():
    """Stop all running services"""
    logger.info("Stopping all services...")
    for process in processes:
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        except Exception as e:
            logger.error(f"Error stopping process: {e}")
    processes.clear()

def check_service_health(service_name: str) -> bool:
    import urllib.request
    import urllib.error
    if service_name not in SERVICES:
        return False
    service_config = SERVICES[service_name]
    try:
        response = urllib.request.urlopen(f"http://localhost:{service_config['port']}/health", timeout=5)
        return response.getcode() == 200
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError):
        return False

def is_service_running(service_name: str) -> bool:
    """Check if a service is running by checking if port is in use"""
    if service_name not in SERVICES:
        return False
    
    service_config = SERVICES[service_name]
    port = service_config['port']
    
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(('localhost', port))
            return result == 0
    except Exception:
        return False

def graceful_shutdown_service(service_name: str) -> bool:
    """Gracefully shutdown a running service"""
    if service_name not in SERVICES:
        return False
    
    service_config = SERVICES[service_name]
    port = service_config['port']
    
    logger.info("🔄 Checking if %s is running...", service_config['name'])
    
    if not is_service_running(service_name):
        logger.info("[OK] %s is not running", service_config['name'])
        return True
    
    logger.info("🔄 %s is running, attempting graceful shutdown...", service_config['name'])
    
    try:
        # Try to send shutdown signal via HTTP
        import urllib.request
        import urllib.error
        
        # Try to send a shutdown request (if service supports it)
        try:
            urllib.request.urlopen(f"http://localhost:{port}/shutdown", timeout=3)
            logger.info("[OK] Graceful shutdown signal sent to %s", service_config['name'])
            time.sleep(2)  # Wait for graceful shutdown
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError):
            pass  # Service might not have shutdown endpoint
        
        # Check if service stopped
        if not is_service_running(service_name):
            logger.info("[OK] %s stopped gracefully", service_config['name'])
            return True
        
        # If service is still running, return False
        logger.warning("[WARN] %s is still running after shutdown attempt", service_config['name'])
        return False
    except Exception as e:
        logger.error("Error during graceful shutdown of %s: %s", service_config['name'], e)
        return False

def run_service_simple(service_name):
    """Run a specific service"""
    if not install_dependencies():
        sys.exit(1)
    
    service_configs = {
        "auth": {
            "app": "services.auth.app.main:app",
            "port": 8001,
            "host": "0.0.0.0"
        },
        "ingestion": {
            "app": "services.ingestion.app.main:app", 
            "port": 8002,
            "host": "0.0.0.0"
        },
        "processing": {
            "app": "services.processing.app.main:app",
            "port": 8003,
            "host": "0.0.0.0"
        },
        "scheduler": {
            "app": "services.scheduler.app.main:app",
            "port": 8004,
            "host": "0.0.0.0"
        }
    }
    
    if service_name not in service_configs:
        print(f"[ERROR] Unknown service: {service_name}")
        print(f"Available services: {', '.join(service_configs.keys())}")
        sys.exit(1)
    
    config = service_configs[service_name]
    print(f"[START] Starting {service_name} service on {config['host']}:{config['port']}...")
    
    try:
        # Use uvicorn directly to start the service
        subprocess.run([
            sys.executable, "-m", "uvicorn",
            config["app"],
            "--host", config["host"],
            "--port", str(config["port"]),
            "--reload"
        ], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Service {service_name} failed: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Run EPMS Backend Services")
    parser.add_argument("service", nargs='?', help="Service to run (auth, ingestion, processing, scheduler, all)")
    parser.add_argument("--install-only", action="store_true", help="Only install dependencies, don't run service")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    
    args = parser.parse_args()
    
    if args.install_only:
        install_all_requirements()
    elif args.service == "all":
        try:
            run_all_services(reload_enabled=args.reload)
            # Keep the main thread alive
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down all services...")
            stop_all_services()
    elif args.service:
        run_single_service(args.service, reload_enabled=args.reload)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()