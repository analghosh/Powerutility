#!/usr/bin/env python3
"""
Script to check audit log files and verify PII protection
"""

import os
import json
import glob
from datetime import datetime
import re

def find_log_files():
    """Find audit log files"""
    possible_paths = [
        "logs/audit/*.log",
        "logs/*.log", 
        "/var/log/audit/*.log",
        "./audit_logs/*.log",
        "services/auth/logs/*.log"
    ]
    
    log_files = []
    for pattern in possible_paths:
        files = glob.glob(pattern)
        log_files.extend(files)
    
    return log_files

def check_log_file(file_path):
    """Check a single log file for audit entries and PII protection"""
    print(f"\n📄 Checking: {file_path}")
    
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
        
        print(f"Total lines: {len(lines)}")
        
        # Check recent entries (last 50 lines)
        recent_lines = lines[-50:] if len(lines) > 50 else lines
        
        audit_entries = 0
        pii_protected = 0
        
        for line in recent_lines:
            line = line.strip()
            if not line:
                continue
                
            # Try to parse as JSON
            try:
                log_entry = json.loads(line)
                
                # Check if it's an audit entry
                if any(key in log_entry for key in ['service', 'record_type', 'audit', 'message']):
                    audit_entries += 1
                    
                    # Check for PII protection
                    line_str = str(log_entry)
                    if re.search(r'\w+\*\*\*@\*\*\*\*\*\.\w+', line_str):  # Masked email pattern
                        pii_protected += 1
                        print(f"  ✅ PII protected entry found")
                    elif '@' in line_str and '***' in line_str:
                        pii_protected += 1
                        print(f"  ✅ PII masking detected")
                    elif any(pii in line_str.lower() for pii in ['email', 'phone', 'ip_address']):
                        print(f"  📝 Entry with potential PII: {log_entry.get('message', '')[:100]}...")
                        
            except json.JSONDecodeError:
                # Not JSON, check as plain text
                if any(keyword in line.lower() for keyword in ['audit', 'login', 'auth', 'user']):
                    audit_entries += 1
                    if '***' in line:
                        pii_protected += 1
                        print(f"  ✅ Masked data in plain text log")
        
        print(f"  📊 Audit entries: {audit_entries}")
        print(f"  🔒 PII protected entries: {pii_protected}")
        
        return audit_entries, pii_protected
        
    except Exception as e:
        print(f"  ❌ Error reading file: {e}")
        return 0, 0

def check_recent_activity():
    """Check for recent audit activity in log files"""
    print(f"\n🕐 Checking Recent Audit Activity...")
    
    log_files = find_log_files()
    
    if not log_files:
        print("ℹ️ No log files found. Possible locations:")
        print("  - logs/audit/")
        print("  - logs/")
        print("  - /var/log/audit/")
        print("  - ./audit_logs/")
        return
    
    total_audit_entries = 0
    total_pii_protected = 0
    
    for log_file in log_files:
        audit_count, pii_count = check_log_file(log_file)
        total_audit_entries += audit_count
        total_pii_protected += pii_count
    
    print(f"\n📈 Summary:")
    print(f"  📁 Log files checked: {len(log_files)}")
    print(f"  📊 Total audit entries: {total_audit_entries}")
    print(f"  🔒 PII protected entries: {total_pii_protected}")
    
    if total_audit_entries > 0:
        protection_rate = (total_pii_protected / total_audit_entries) * 100
        print(f"  📊 PII protection rate: {protection_rate:.1f}%")

def main():
    """Main log file verification"""
    print("📁 Log File Audit Verification")
    print("=" * 50)
    print(f"Timestamp: {datetime.now()}")
    
    check_recent_activity()
    
    print(f"\n💡 Tips:")
    print("1. If no log files found, check your logging configuration")
    print("2. Run test scripts to generate audit entries")
    print("3. Check both JSON and plain text log formats")
    print("4. Verify PII masking is working correctly")

if __name__ == "__main__":
    main()