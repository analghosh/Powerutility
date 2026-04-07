import sys
try:
    with open(r'd:\EPMS-Backend\tests\test_results.log', 'r', encoding='utf-16') as f:
        lines = f.readlines()
        print(''.join(lines[-50:]))
except Exception as e:
    try:
        with open(r'd:\EPMS-Backend\tests\test_results.log', 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            print(''.join(lines[-50:]))
    except Exception as e2:
        print(f"Error reading file: {e2}")
