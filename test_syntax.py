with open(r'D:\Tiger\Wenqu v1.1\database.py', encoding='utf-8') as f:
    lines = f.readlines()

# Find the triple quotes that close the executescript
in_exec = False
for i, line in enumerate(lines):
    if 'conn.executescript' in line:
        in_exec = True
        start = i
    if in_exec and '"""' in line:
        print(f'executescript ends at line {i+1}: {repr(line)}')
        break

print(f'starts at line {start+1}')

# Also check the structure
print('\nLines 36-45:')
for i in range(35, 45):
    print(f'{i+1}: {repr(lines[i][:80])}')
