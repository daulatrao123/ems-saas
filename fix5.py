fp = r'C:\Users\Daulat\solid-funcular\backend\main.py'
with open(fp, encoding='utf-8') as f:
    lines = f.readlines()
in_loop = False
fixed = 0
for i, line in enumerate(lines):
    if 'for cmd in cmds:' in line and not in_loop:
        in_loop = True
    elif in_loop:
        if 'break' in line:
            in_loop = False
        elif 'pc.' in line:
            lines[i] = line.replace('pc.', 'cmd.')
            fixed += 1
with open(fp, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print(f'Fixed {fixed} lines in pi_sync')
