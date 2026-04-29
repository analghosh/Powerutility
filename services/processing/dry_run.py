import sys
import os

# Add the parent directory to sys.path so we can import app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app.main import app
    print("Successfully imported FastAPI app!")
    
    print("\nListing all registered API routes:")
    for route in app.routes:
        if hasattr(route, "methods"):
            print(f"[{','.join(route.methods)}] {route.path}")
            
    print("\nAlso listing V2 app routes if available:")
    try:
        from app.v2.v2 import v2_app
        for route in v2_app.routes:
            if hasattr(route, "methods"):
                print(f"[{','.join(route.methods)}] {route.path}")
    except ImportError as e:
        print(f"Could not load v2_app: {e}")
        
except Exception as e:
    import traceback
    print(f"Failed to load the app: {e}")
    traceback.print_exc()
