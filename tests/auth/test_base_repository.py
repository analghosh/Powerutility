"""
Unit tests for BaseRepository
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from bson import ObjectId

# Mock database connections BEFORE importing any app modules
with patch('pymongo.MongoClient') as mock_mongo_client:
    mock_client = MagicMock()
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_mongo_client.return_value = mock_client
    mock_client.__getitem__.return_value = mock_db
    mock_db.__getitem__.return_value = mock_collection
    
    # Mock Redis connections
    with patch('redis.Redis') as mock_redis:
        mock_redis_instance = Mock()
        mock_redis.from_url.return_value = mock_redis_instance
        mock_redis_instance.sismember.return_value = False
        mock_redis_instance.sadd.return_value = 1
        
        # Now we can safely import
        from services.auth.app.repository.base_repository import BaseRepository


class TestBaseRepository:
    """Test cases for BaseRepository"""
    
    @pytest.fixture
    def base_repo(self):
        """Create BaseRepository instance for testing"""
        with patch('services.auth.app.repository.base_repository.MongoClient'):
            repo = BaseRepository("test_collection")
            # Mock the collection
            repo.collection = Mock()
            return repo
    
    @pytest.fixture
    def mock_document(self):
        """Mock document for testing"""
        return {
            "_id": ObjectId("507f1f77bcf86cd799439011"),
            "name": "Test Document",
            "value": 123,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
    
    def test_init(self, base_repo):
        """Test BaseRepository initialization"""
        assert base_repo.collection_name == "test_collection"
        assert base_repo.collection is not None
    
    def test_create_success(self, base_repo):
        """Test successful document creation"""
        # Mock insert_one to return a result with inserted_id
        mock_result = Mock()
        mock_result.inserted_id = ObjectId("507f1f77bcf86cd799439011")
        base_repo.collection.insert_one.return_value = mock_result
        
        data = {"name": "Test Document", "value": 123}
        result = base_repo.create(data)
        
        assert result == "507f1f77bcf86cd799439011"
        base_repo.collection.insert_one.assert_called_once()
        
        # Verify that timestamps were added
        call_args = base_repo.collection.insert_one.call_args[0][0]
        assert "created_at" in call_args
        assert "updated_at" in call_args
        assert call_args["name"] == "Test Document"
        assert call_args["value"] == 123
    
    def test_create_with_exception(self, base_repo):
        """Test document creation with exception"""
        base_repo.collection.insert_one.side_effect = Exception("Database error")
        
        data = {"name": "Test Document", "value": 123}
        
        with pytest.raises(Exception):
            base_repo.create(data)
    
    def test_find_by_id_success(self, base_repo, mock_document):
        """Test successful document find by ID"""
        base_repo.collection.find_one.return_value = mock_document
        
        result = base_repo.find_by_id("507f1f77bcf86cd799439011")
        
        assert result == mock_document
        base_repo.collection.find_one.assert_called_once_with({"_id": ObjectId("507f1f77bcf86cd799439011")})
    
    def test_find_by_id_not_found(self, base_repo):
        """Test document find by ID when not found"""
        base_repo.collection.find_one.return_value = None
        
        result = base_repo.find_by_id("507f1f77bcf86cd799439011")
        
        assert result is None
    
    def test_find_by_id_invalid_id(self, base_repo):
        """Test document find by ID with invalid ID"""
        result = base_repo.find_by_id("invalid_id")
        
        assert result is None
        # Should not call find_one for invalid ObjectId
        base_repo.collection.find_one.assert_not_called()
    
    def test_find_one_success(self, base_repo, mock_document):
        """Test successful document find one"""
        base_repo.collection.find_one.return_value = mock_document
        
        result = base_repo.find_one({"name": "Test Document"})
        
        assert result == mock_document
        base_repo.collection.find_one.assert_called_once_with({"name": "Test Document"})
    
    def test_find_one_not_found(self, base_repo):
        """Test document find one when not found"""
        base_repo.collection.find_one.return_value = None
        
        result = base_repo.find_one({"name": "Non-existent"})
        
        assert result is None
    
    def test_find_many_success(self, base_repo, mock_document):
        """Test successful document find many"""
        # Mock cursor with proper chaining
        mock_cursor = Mock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.__iter__ = Mock(return_value=iter([mock_document]))
        base_repo.collection.find.return_value = mock_cursor
        
        result = base_repo.find_many({"name": "Test Document"})
        
        assert result == [mock_document]
        base_repo.collection.find.assert_called_once_with({"name": "Test Document"})
    
    def test_find_many_with_limit(self, base_repo, mock_document):
        """Test document find many with limit"""
        # Mock cursor with proper chaining
        mock_cursor = Mock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.__iter__ = Mock(return_value=iter([mock_document]))
        base_repo.collection.find.return_value = mock_cursor
        
        result = base_repo.find_many({"name": "Test Document"}, limit=5)
        
        assert result == [mock_document]
        base_repo.collection.find.assert_called_once_with({"name": "Test Document"})
    
    def test_find_many_empty(self, base_repo):
        """Test document find many with empty result"""
        # Mock cursor with proper chaining
        mock_cursor = Mock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.__iter__ = Mock(return_value=iter([]))
        base_repo.collection.find.return_value = mock_cursor
        
        result = base_repo.find_many({"name": "Non-existent"})
        
        assert result == []
    
    def test_update_success(self, base_repo):
        """Test successful document update"""
        # Mock update_one to return a result with modified_count > 0
        mock_result = Mock()
        mock_result.modified_count = 1
        base_repo.collection.update_one.return_value = mock_result
        
        data = {"name": "Updated"}
        result = base_repo.update("507f1f77bcf86cd799439011", data)
        
        assert result is True
        base_repo.collection.update_one.assert_called_once()
        
        # Verify that updated_at was added
        call_args = base_repo.collection.update_one.call_args
        assert call_args[0][0] == {"_id": ObjectId("507f1f77bcf86cd799439011")}
        assert "$set" in call_args[0][1]
        assert "updated_at" in call_args[0][1]["$set"]
        assert call_args[0][1]["$set"]["name"] == "Updated"
    
    def test_update_not_found(self, base_repo):
        """Test document update when not found"""
        # Mock update_one to return a result with modified_count = 0
        mock_result = Mock()
        mock_result.modified_count = 0
        base_repo.collection.update_one.return_value = mock_result
        
        data = {"name": "Updated"}
        result = base_repo.update("507f1f77bcf86cd799439011", data)
        
        assert result is False
    
    def test_update_invalid_id(self, base_repo):
        """Test document update with invalid ID"""
        data = {"name": "Updated"}
        result = base_repo.update("invalid_id", data)
        
        assert result is False
        # Should not call update_one for invalid ObjectId
        base_repo.collection.update_one.assert_not_called()
    
    def test_delete_success(self, base_repo):
        """Test successful document delete"""
        # Mock delete_one to return a result with deleted_count > 0
        mock_result = Mock()
        mock_result.deleted_count = 1
        base_repo.collection.delete_one.return_value = mock_result
        
        result = base_repo.delete("507f1f77bcf86cd799439011")
        
        assert result is True
        base_repo.collection.delete_one.assert_called_once_with({"_id": ObjectId("507f1f77bcf86cd799439011")})
    
    def test_delete_not_found(self, base_repo):
        """Test document delete when not found"""
        # Mock delete_one to return a result with deleted_count = 0
        mock_result = Mock()
        mock_result.deleted_count = 0
        base_repo.collection.delete_one.return_value = mock_result
        
        result = base_repo.delete("507f1f77bcf86cd799439011")
        
        assert result is False
    
    def test_soft_delete_success(self, base_repo):
        """Test successful document soft delete"""
        # Mock update_one to return a result with modified_count > 0
        mock_result = Mock()
        mock_result.modified_count = 1
        base_repo.collection.update_one.return_value = mock_result
        
        result = base_repo.soft_delete("507f1f77bcf86cd799439011")
        
        assert result is True
        base_repo.collection.update_one.assert_called_once()
        
        # Verify that soft delete fields were added
        call_args = base_repo.collection.update_one.call_args
        assert call_args[0][0] == {"_id": ObjectId("507f1f77bcf86cd799439011")}
        assert "$set" in call_args[0][1]
        assert "is_deleted" in call_args[0][1]["$set"]
        assert "deleted_at" in call_args[0][1]["$set"]
        assert "updated_at" in call_args[0][1]["$set"]
        assert call_args[0][1]["$set"]["is_deleted"] is True
    
    def test_exists_true(self, base_repo):
        """Test document exists when found"""
        # Mock count_documents to return count > 0
        base_repo.collection.count_documents.return_value = 1
        
        result = base_repo.exists({"name": "Test"})
        
        assert result is True
        base_repo.collection.count_documents.assert_called_once_with({"name": "Test"}, limit=1)
    
    def test_exists_false(self, base_repo):
        """Test document exists when not found"""
        # Mock count_documents to return count = 0
        base_repo.collection.count_documents.return_value = 0
        
        result = base_repo.exists({"name": "Non-existent"})
        
        assert result is False
    
    def test_count_success(self, base_repo):
        """Test successful document count"""
        base_repo.collection.count_documents.return_value = 5
        
        result = base_repo.count({"name": "Test"})
        
        assert result == 5
        base_repo.collection.count_documents.assert_called_once_with({"name": "Test"})
    
    def test_close_success(self, base_repo):
        """Test successful connection close"""
        base_repo.client = Mock()
        
        base_repo.close()
        
        base_repo.client.close.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__]) 