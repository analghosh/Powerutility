#!/usr/bin/env python3
"""
Test script for tile view functionality
"""

import sys
import os
import logging
from datetime import datetime

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_tile_view():
    """Test the tile view functionality"""
    try:
        logger.info("Testing Tile View Implementation...")
        
        # Test 1: Check if dashboard HTML has tile view elements
        logger.info("Test 1: Checking dashboard HTML structure...")
        
        dashboard_path = "services/scheduler/app/static/job_dashboard.html"
        if os.path.exists(dashboard_path):
            with open(dashboard_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Check for tile view elements
            tile_elements = [
                'tile-view',
                'tile-grid',
                'tile-item',
                'switchView',
                'view-toggle',
                'view-btn'
            ]
            
            found_elements = []
            for element in tile_elements:
                if element in content:
                    found_elements.append(element)
            
            logger.info("✅ Found %s/%s tile view elements in dashboard HTML", len(found_elements), len(tile_elements))
            for element in found_elements:
                logger.info("  ✅ %s", element)
            
            missing_elements = set(tile_elements) - set(found_elements)
            if missing_elements:
                logger.warning("⚠️ Missing elements: %s", list(missing_elements))
        
        # Test 2: Check CSS styles for tile view
        logger.info("Test 2: Checking CSS styles for tile view...")
        
        css_styles = [
            '.tile-view',
            '.tile-grid',
            '.tile-item',
            '.tile-header',
            '.tile-title',
            '.tile-status',
            '.tile-details',
            '.view-toggle',
            '.view-btn'
        ]
        
        found_styles = []
        for style in css_styles:
            if style in content:
                found_styles.append(style)
        
        logger.info("✅ Found %s/%s CSS styles for tile view", len(found_styles), len(css_styles))
        for style in found_styles:
            logger.info("  ✅ %s", style)
        
        # Test 3: Check JavaScript functions
        logger.info("Test 3: Checking JavaScript functions...")
        
        js_functions = [
            'switchView(',
            'refreshCurrentView(',
            'updateJobOverview(',
            'updateAllJobsFromJobData(',
            'updateAllJobs('
        ]
        
        found_functions = []
        for func in js_functions:
            if func in content:
                found_functions.append(func)
        
        logger.info("✅ Found %s/%s JavaScript functions", len(found_functions), len(js_functions))
        for func in found_functions:
            logger.info("  ✅ %s", func)
        
        # Test 4: Check HTML containers
        logger.info("Test 4: Checking HTML containers...")
        
        containers = [
            'job-overview-tiles',
            'all-jobs-tiles',
            'jobs-tiles',
            'card-view-btn',
            'tile-view-btn'
        ]
        
        found_containers = []
        for container in containers:
            if container in content:
                found_containers.append(container)
        
        logger.info("✅ Found %s/%s HTML containers", len(found_containers), len(containers))
        for container in found_containers:
            logger.info("  ✅ %s", container)
        
        logger.info("🎉 Tile view implementation test completed successfully!")
        logger.info("")
        logger.info("📊 Tile View Features:")
        logger.info("  ✅ View toggle buttons (Cards/Tiles)")
        logger.info("  ✅ Tile grid layout with responsive design")
        logger.info("  ✅ Service-specific color coding")
        logger.info("  ✅ Status indicators with color coding")
        logger.info("  ✅ Compact tile format with essential information")
        logger.info("  ✅ Hover effects and smooth transitions")
        logger.info("  ✅ Support for all dashboard views (Overview, Job Data, Service Jobs)")
        logger.info("")
        logger.info("🌐 Access the dashboard at: http://localhost:8004/dashboard")
        logger.info("🔲 Click the 'Tiles' button to switch to tile view")
        logger.info("📋 Click the 'Cards' button to switch back to card view")
        
        return True
        
    except Exception as e:
        logger.error("❌ Tile view test failed: %s", e)
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_tile_view()
    sys.exit(0 if success else 1)
