"""
Validation utilities for Auth Service
"""

import re
from typing import List, Dict, Optional, Tuple

from .constants import ADMIN_VALIDATION, PASSWORD_VALIDATION

class CredentialValidator:
    """Validator for user credentials"""
    
    @staticmethod
    def validate_password(password: str) -> Tuple[bool, List[str]]:
        """
        Validate password against password policy
        
        Args:
            password: Password to validate
            
        Returns:
            Tuple[bool, List[str]]: (is_valid, list of validation errors)
        """
        errors = []
        rules = PASSWORD_VALIDATION
        
        # Check length
        if len(password) < rules["min_length"]:
            errors.append(f"Password must be at least {rules['min_length']} characters long")
        if len(password) > rules["max_length"]:
            errors.append(f"Password cannot exceed {rules['max_length']} characters")
        
        # Check character requirements
        if rules["require_uppercase"] and not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")
        
        if rules["require_lowercase"] and not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")
        
        if rules["require_numbers"] and not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one number")
        
        if rules["require_special_chars"] and not any(c in rules["special_chars"] for c in password):
            errors.append("Password must contain at least one special character")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_username(username: str) -> Tuple[bool, List[str]]:
        """
        Validate username
        
        Args:
            username: Username to validate
            
        Returns:
            Tuple[bool, List[str]]: (is_valid, list of validation errors)
        """
        errors = []
        rules = ADMIN_VALIDATION["username"]
        
        # Check length
        if len(username) < rules["min_length"]:
            errors.append(f"Username must be at least {rules['min_length']} characters long")
        if len(username) > rules["max_length"]:
            errors.append(f"Username cannot exceed {rules['max_length']} characters")
        
        # Check pattern
        if not re.match(rules["pattern"], username):
            errors.append("Username can only contain letters, numbers, underscores, and hyphens")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_email(email: str) -> Tuple[bool, List[str]]:
        """
        Validate email address
        
        Args:
            email: Email to validate
            
        Returns:
            Tuple[bool, List[str]]: (is_valid, list of validation errors)
        """
        errors = []
        rules = ADMIN_VALIDATION["email"]
        
        # Check pattern
        if not re.match(rules["pattern"], email):
            errors.append("Invalid email format")
        
        return len(errors) == 0, errors
    
    @classmethod
    def validate_admin_credentials(
        cls,
        username: str,
        email: str,
        password: str
    ) -> Tuple[bool, Dict[str, List[str]]]:
        """
        Validate admin credentials
        
        Args:
            username: Admin username
            email: Admin email
            password: Admin password
            
        Returns:
            Tuple[bool, Dict[str, List[str]]]: (is_valid, validation errors by field)
        """
        errors = {}
        
        # Validate username
        username_valid, username_errors = cls.validate_username(username)
        if not username_valid:
            errors["username"] = username_errors
        
        # Validate email
        email_valid, email_errors = cls.validate_email(email)
        if not email_valid:
            errors["email"] = email_errors
        
        # Validate password
        password_valid, password_errors = cls.validate_password(password)
        if not password_valid:
            errors["password"] = password_errors
        
        return len(errors) == 0, errors 