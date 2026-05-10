"""
File Storage Service
Handles temporary file storage and management for upload processing
"""

import os
import uuid
import shutil
import zipfile
import logging
import magic
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from fastapi import UploadFile, HTTPException

# Allowed MIME types and their expected file extensions
_ALLOWED_MIME_TYPES = {
    "text/csv",
    "text/plain",
    "application/csv",
    "application/vnd.ms-excel",
}
_MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB

logger = logging.getLogger(__name__)


class FileStorageService:
    """
    Service for managing temporary file storage during upload processing
    """
    
    def __init__(self, upload_dir: str = "uploads"):
        """
        Initialize file storage service
        
        Args:
            upload_dir: Directory to store uploaded files
        """
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        logger.info("File storage service initialized with directory: %s", self.upload_dir)
    
    async def save_file(self, file_content: bytes, file_name: str, upload_id: str) -> str:
        """
        Save raw file content to disk under the upload directory.
        Returns the absolute file path.
        """
        try:
            upload_path = self.upload_dir / upload_id
            upload_path.mkdir(exist_ok=True)
            # Strip any directory components from the caller-supplied name to prevent path traversal
            safe_name = Path(file_name).name if file_name else f"{upload_id}.bin"
            if not safe_name:
                safe_name = f"{upload_id}.bin"
            file_path = (upload_path / safe_name).resolve()
            # Verify the resolved path stays inside the intended directory
            if not file_path.is_relative_to(upload_path.resolve()):
                raise ValueError(f"Path traversal detected for upload_id={upload_id}")
            with open(file_path, "wb") as f:
                f.write(file_content)
            logger.info("Saved file for upload_id=%s at %s", upload_id, file_path)
            return str(file_path)
        except Exception as e:
            logger.error("Error saving file for upload_id=%s: %s", upload_id, e, exc_info=True)
            raise

    async def store_file(self, file: UploadFile, upload_id: str) -> Dict[str, Any]:
        """
        Store uploaded file temporarily
        
        Args:
            file: Uploaded file
            upload_id: Unique upload identifier
            
        Returns:
            Dict[str, Any]: File storage information
        """
        try:
            # Create upload-specific directory
            upload_path = self.upload_dir / upload_id
            upload_path.mkdir(exist_ok=True)
            
            # Generate unique filename — use only the suffix to avoid injecting directory separators
            file_extension = Path(file.filename or "unknown").suffix
            stored_filename = f"{upload_id}{file_extension}"
            file_path = (upload_path / stored_filename).resolve()
            if not file_path.is_relative_to(upload_path.resolve()):
                raise ValueError(f"Path traversal detected for upload_id={upload_id}")

            # Read content and validate before writing
            content = await file.read()

            # Size guard
            if len(content) > _MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds maximum allowed size of {_MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB"
                )

            # MIME magic-byte validation
            detected_mime = magic.from_buffer(content, mime=True)
            if detected_mime not in _ALLOWED_MIME_TYPES:
                raise HTTPException(
                    status_code=415,
                    detail=f"Unsupported file type. Allowed types: CSV"
                )

            with open(file_path, "wb") as buffer:
                buffer.write(content)

            file_info = {
                "upload_id": upload_id,
                "original_filename": file.filename,
                "stored_filename": stored_filename,
                "file_path": str(file_path),
                "file_size": len(content),
                "file_extension": file_extension,
                "stored_at": datetime.now().isoformat(),
                "status": "STORED"
            }
            
            logger.info("File stored successfully: %s -> %s", file.filename, file_path)
            return file_info
            
        except Exception as e:
            logger.error("Error storing file %s: %s", file.filename, e, exc_info=True)
            raise
    
    async def get_file_path(self, upload_id: str) -> Optional[str]:
        """
        Get file path for upload ID
        
        Args:
            upload_id: Upload identifier
            
        Returns:
            Optional[str]: File path if exists, None otherwise
        """
        upload_path = self.upload_dir / upload_id
        if upload_path.exists():
            # Find the file in the upload directory
            for file_path in upload_path.iterdir():
                if file_path.is_file():
                    return str(file_path)
        return None
    
    def delete_file_by_id(self, upload_id: str) -> bool:
        """Delete stored directory for an upload id (legacy)."""
        try:
            upload_path = self.upload_dir / upload_id
            if upload_path.exists():
                shutil.rmtree(upload_path)
                logger.info("File deleted successfully: %s", upload_path)
                return True
            logger.warning("File not found for deletion: %s", upload_path)
            return False
        except Exception as e:
            logger.error("Error deleting file %s: %s", upload_id, e, exc_info=True)
            return False

    async def delete_file(self, file_path: str) -> bool:
        """Delete a specific file by its path."""
        try:
            p = Path(file_path)
            if p.exists() and p.is_file():
                p.unlink()
                # Remove parent dir if empty
                parent = p.parent
                if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                logger.info("Deleted file by path: %s", file_path)
                return True
            logger.warning("File path not found for deletion: %s", file_path)
            return False
        except Exception as e:
            logger.error("Error deleting file by path %s: %s", file_path, e, exc_info=True)
            return False

    async def file_exists(self, file_path: str) -> bool:
        """Check if a file exists by absolute/relative path."""
        try:
            return Path(file_path).exists()
        except Exception:
            return False
    
    def cleanup_old_files(self, max_age_hours: int = 24) -> int:
        """
        Clean up files older than specified age
        
        Args:
            max_age_hours: Maximum age in hours before cleanup
            
        Returns:
            int: Number of files cleaned up
        """
        try:
            cleaned_count = 0
            current_time = datetime.now()
            
            for upload_dir in self.upload_dir.iterdir():
                if upload_dir.is_dir():
                    # Check if directory is older than max_age_hours
                    dir_time = datetime.fromtimestamp(upload_dir.stat().st_mtime)
                    age_hours = (current_time - dir_time).total_seconds() / 3600
                    
                    if age_hours > max_age_hours:
                        shutil.rmtree(upload_dir)
                        cleaned_count += 1
                        logger.info("Cleaned up old file: %s (age: %.1f hours)", upload_dir, age_hours)
            
            logger.info("Cleanup completed: %d files removed", cleaned_count)
            return cleaned_count
            
        except Exception as e:
            logger.error("Error during cleanup: %s", e, exc_info=True)
            return 0
    
    def get_storage_stats(self) -> Dict[str, Any]:
        """
        Get storage statistics
        
        Returns:
            Dict[str, Any]: Storage statistics
        """
        try:
            total_files = 0
            total_size = 0
            
            for upload_dir in self.upload_dir.iterdir():
                if upload_dir.is_dir():
                    for file_path in upload_dir.iterdir():
                        if file_path.is_file():
                            total_files += 1
                            total_size += file_path.stat().st_size
            
            return {
                "total_files": total_files,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "upload_directory": str(self.upload_dir)
            }
            
        except Exception as e:
            logger.error("Error getting storage stats: %s", e, exc_info=True)
            return {
                "total_files": 0,
                "total_size_bytes": 0,
                "total_size_mb": 0,
                "upload_directory": str(self.upload_dir),
                "error": str(e)
            }
    
    def cleanup_temp_files(self, max_age_hours: int = 1) -> int:
        """
        Clean up temporary files older than specified age
        
        Args:
            max_age_hours: Maximum age in hours before cleanup
            
        Returns:
            int: Number of files cleaned up
        """
        return self.cleanup_old_files(max_age_hours)
    
    def zip_and_archive(self, backup_dir: str) -> str:
        """
        Zip the entire upload directory and save the archive to backup_dir.
        Existing archives are never deleted — each call creates a new timestamped zip.

        Args:
            backup_dir: Host directory where zip archives are stored permanently.

        Returns:
            str: Absolute path of the created zip archive.
        """
        try:
            backup_path = Path(backup_dir)
            backup_path.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_name = f"uploads_backup_{timestamp}.zip"
            zip_path = backup_path / zip_name

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in self.upload_dir.rglob("*"):
                    if item.is_file():
                        zf.write(item, item.relative_to(self.upload_dir))

            logger.info("Archived uploads to %s", zip_path)
            return str(zip_path)
        except Exception as e:
            logger.error("Error creating upload archive: %s", e, exc_info=True)
            raise

    def perform_maintenance(self) -> Dict[str, Any]:
        """
        Perform storage maintenance tasks
        
        Returns:
            Dict[str, Any]: Maintenance results
        """
        try:
            # Clean up old files (older than 24 hours)
            cleaned_files = self.cleanup_old_files(24)
            
            # Get current storage stats
            stats = self.get_storage_stats()
            
            return {
                "status": "success",
                "cleaned_files": cleaned_files,
                "current_stats": stats,
                "maintenance_completed_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error("Error during storage maintenance: %s", e, exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "maintenance_completed_at": datetime.now().isoformat()
            }


# Global instance — uses UPLOAD_DIR from settings so the path is always the
# host-mounted persistent directory, not a relative container path.
from services.ingestion.app.core.config import settings as _ingestion_settings  # noqa: E402
file_storage_service = FileStorageService(upload_dir=_ingestion_settings.UPLOAD_DIR)
