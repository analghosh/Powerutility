
import sys
import importlib.util

files_to_check = [
    r"d:\EPMS-Backend\services\processing\app\v2\services\dam_transaction_service.py",
    r"d:\EPMS-Backend\services\processing\app\v2\api\router\new_dam_bidding_routes.py",
    r"d:\EPMS-Backend\services\processing\app\v2\controller\dam_transaction_controller.py",
    r"d:\EPMS-Backend\services\processing\app\v2\services\rtm_transaction_service.py",
    r"d:\EPMS-Backend\services\processing\app\v2\api\router\new_rtm_bidding_routes.py",
    r"d:\EPMS-Backend\services\processing\app\v2\controller\rtm_transaction_controller.py"
]

def check_syntax(file_path):
    try:
        with open(file_path, 'r') as f:
            source = f.read()
        compile(source, file_path, 'exec')
        print(f"Syntax OK: {file_path}")
        return True
    except Exception as e:
        print(f"Syntax Error in {file_path}: {e}")
        return False

success = True
for f in files_to_check:
    if not check_syntax(f):
        success = False

if success:
    print("All files passed syntax check.")
    sys.exit(0)
else:
    sys.exit(1)
