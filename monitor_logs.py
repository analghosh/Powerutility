#!/usr/bin/env python3
"""
Real-time Log Monitoring Script for EPMS Backend
"""

import os
import sys
import time
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

class LogMonitor:
    """Real-time log monitoring and analysis"""
    
    def __init__(self):
        self.log_file = Path("logs/auth_errors.log")
        self.last_position = 0
        self.error_counts = {}
        self.performance_data = []
        
    def start_monitoring(self):
        """Start real-time log monitoring"""
        print("🔍 EPMS Backend Log Monitor")
        print("=" * 50)
        print("Monitoring logs in real-time...")
        print("Press Ctrl+C to stop")
        print()
        
        try:
            while True:
                self.check_new_logs()
                time.sleep(1)  # Check every second
                
        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped")
            self.show_summary()
    
    def check_new_logs(self):
        """Check for new log entries"""
        if not self.log_file.exists():
            return
            
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                f.seek(self.last_position)
                new_lines = f.readlines()
                self.last_position = f.tell()
                
                for line in new_lines:
                    self.process_log_line(line.strip())
                    
        except Exception as e:
            print(f"❌ Error reading log file: {e}")
    
    def process_log_line(self, line: str):
        """Process a single log line"""
        if not line:
            return
            
        try:
            # Try to parse as JSON first
            if line.startswith('{'):
                log_data = json.loads(line)
                self.handle_json_log(log_data)
            else:
                # Handle text format
                self.handle_text_log(line)
                
        except json.JSONDecodeError:
            # Handle text format
            self.handle_text_log(line)
        except Exception as e:
            print(f"❌ Error processing log line: {e}")
    
    def handle_json_log(self, log_data: Dict):
        """Handle JSON formatted log entry"""
        timestamp = log_data.get('timestamp', '')
        level = log_data.get('level', 'INFO')
        message = log_data.get('message', '')
        
        # Color coding based on level
        color = self.get_level_color(level)
        
        print(f"{color}[{timestamp}] {level}: {message}")
        
        # Track errors
        if level == 'ERROR':
            self.track_error(log_data)
        
        # Track performance
        if 'duration_ms' in log_data:
            self.track_performance(log_data)
    
    def handle_text_log(self, line: str):
        """Handle text formatted log entry"""
        # Simple text log processing
        if 'ERROR' in line:
            print(f"❌ {line}")
        elif 'WARNING' in line:
            print(f"⚠️  {line}")
        elif 'INFO' in line:
            print(f"ℹ️  {line}")
        else:
            print(f"🔍 {line}")
    
    def get_level_color(self, level: str) -> str:
        """Get color code for log level"""
        colors = {
            'ERROR': '❌',
            'WARNING': '⚠️',
            'INFO': 'ℹ️',
            'DEBUG': '🔍'
        }
        return colors.get(level, '🔍')
    
    def track_error(self, log_data: Dict):
        """Track error patterns"""
        error_type = log_data.get('error_type', 'Unknown')
        if error_type not in self.error_counts:
            self.error_counts[error_type] = 0
        self.error_counts[error_type] += 1
    
    def track_performance(self, log_data: Dict):
        """Track performance metrics"""
        duration = log_data.get('duration_ms', 0)
        endpoint = log_data.get('endpoint', 'Unknown')
        
        self.performance_data.append({
            'duration': duration,
            'endpoint': endpoint,
            'timestamp': datetime.now()
        })
        
        # Keep only last 100 entries
        if len(self.performance_data) > 100:
            self.performance_data.pop(0)
    
    def show_summary(self):
        """Show monitoring summary"""
        print("\n📊 Monitoring Summary")
        print("=" * 30)
        
        if self.error_counts:
            print("❌ Error Counts:")
            for error_type, count in self.error_counts.items():
                print(f"   {error_type}: {count}")
        
        if self.performance_data:
            print("\n📈 Performance Summary:")
            avg_duration = sum(p['duration'] for p in self.performance_data) / len(self.performance_data)
            max_duration = max(p['duration'] for p in self.performance_data)
            print(f"   Average Response Time: {avg_duration:.2f}ms")
            print(f"   Max Response Time: {max_duration:.2f}ms")
            print(f"   Total Requests: {len(self.performance_data)}")
    
    def analyze_logs(self):
        """Analyze existing logs"""
        if not self.log_file.exists():
            print("❌ No log file found")
            return
        
        print("📋 Log Analysis")
        print("=" * 30)
        
        error_patterns = {}
        performance_data = []
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Track errors
                    if 'ERROR' in line:
                        self.analyze_error_line(line, error_patterns)
                    
                    # Track performance
                    if 'duration_ms' in line:
                        self.analyze_performance_line(line, performance_data)
        
        except Exception as e:
            print(f"❌ Error analyzing logs: {e}")
            return
        
        # Show error analysis
        if error_patterns:
            print("❌ Error Analysis:")
            for pattern, count in sorted(error_patterns.items(), key=lambda x: x[1], reverse=True):
                print(f"   {pattern}: {count} occurrences")
        
        # Show performance analysis
        if performance_data:
            print("\n📈 Performance Analysis:")
            avg_duration = sum(p['duration'] for p in performance_data) / len(performance_data)
            slow_requests = [p for p in performance_data if p['duration'] > 1000]
            print(f"   Average Response Time: {avg_duration:.2f}ms")
            print(f"   Slow Requests (>1s): {len(slow_requests)}")
            print(f"   Total Requests: {len(performance_data)}")
    
    def analyze_error_line(self, line: str, patterns: Dict):
        """Analyze error line for patterns"""
        # Extract error type
        error_match = re.search(r'error_type["\']?\s*:\s*["\']?([^"\',\s]+)', line)
        if error_match:
            error_type = error_match.group(1)
            patterns[error_type] = patterns.get(error_type, 0) + 1
    
    def analyze_performance_line(self, line: str, data: List):
        """Analyze performance line"""
        # Extract duration
        duration_match = re.search(r'duration_ms["\']?\s*:\s*([0-9.]+)', line)
        if duration_match:
            duration = float(duration_match.group(1))
            data.append({'duration': duration})


def main():
    """Main function"""
    monitor = LogMonitor()
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "analyze":
            monitor.analyze_logs()
        elif command == "monitor":
            monitor.start_monitoring()
        else:
            print("Usage: python monitor_logs.py [analyze|monitor]")
            print("  analyze - Analyze existing logs")
            print("  monitor - Start real-time monitoring")
    else:
        print("🔍 EPMS Backend Log Monitor")
        print("=" * 30)
        print("Usage:")
        print("  python monitor_logs.py analyze  - Analyze existing logs")
        print("  python monitor_logs.py monitor  - Start real-time monitoring")
        print()
        print("Examples:")
        print("  python monitor_logs.py analyze  # Show error patterns")
        print("  python monitor_logs.py monitor  # Watch logs in real-time")


if __name__ == "__main__":
    main() 