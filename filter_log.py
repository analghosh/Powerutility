
try:
    with open(r'd:\EPMS-Backend\tests\test_results.log', 'r', encoding='utf-16') as f:
        lines = f.readlines()
except:
    with open(r'd:\EPMS-Backend\tests\test_results.log', 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

for i, line in enumerate(lines):
    if "ERROR" in line or "FAILED" in line or "❌" in line or "Traceback" in line:
        print("-" * 20)
        print(line.strip())
        # Print next 5 lines
        for j in range(1, 6):
            if i + j < len(lines):
                print(lines[i+j].strip())
