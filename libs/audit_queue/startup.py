"""
Queue startup and recovery utilities
"""

from libs.audit_queue.queue_manager import initialize_queue_after_restart


def startup_queue_recovery():
    """
    Run this function when your application starts to recover pending jobs
    """
    print("🔄 Starting audit queue recovery...")
    result = initialize_queue_after_restart()
    
    if "error" in result:
        print(f"❌ Queue recovery failed: {result['error']}")
    else:
        print(f"✅ Queue recovery completed:")
        print(f"   - Restarted interrupted jobs: {result['restarted']}")
        print(f"   - Requeued pending jobs: {result['requeued']}")
    
    return result
