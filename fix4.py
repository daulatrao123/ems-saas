fp = r'C:\Users\Daulat\solid-funcular\backend\main.py'
with open(fp, encoding='utf-8') as f:
    lines = f.readlines()

out = []
for i, line in enumerate(lines):
    if '    cmd = db.get("pi_commands", {}).get(int(society_id))' in line:
        out.append('    cmds = db.get("pi_commands", {}).get(int(society_id), [])\n')
        out.append('    if isinstance(cmds, dict): cmds = [cmds]\n')
        out.append('    pc = next((x for x in cmds if x.get("status") in ("pending","sent")), None)\n')
        continue
    if 'cmd.get("id")' in line and 'pending_command' not in line:
        out.append(line.replace('cmd.get', 'pc.get'))
        continue
    if 'cmd.get("command")' in line:
        out.append(line.replace('cmd.get', 'pc.get'))
        continue
    if 'cmd.get("status")' in line:
        out.append(line.replace('cmd.get', 'pc.get'))
        continue
    if 'cmd.get("queued_at")' in line:
        out.append(line.replace('cmd.get', 'pc.get'))
        continue
    if 'cmd.get("acked_at")' in line:
        out.append(line.replace('cmd.get', 'pc.get'))
        continue
    if 'cmd.get("error")' in line:
        out.append(line.replace('cmd.get', 'pc.get'))
        continue
    if '} if cmd else None' in line:
        out.append(line.replace('if cmd else None', 'if pc else None'))
        continue
    out.append(line)

with open(fp, 'w', encoding='utf-8') as f:
    f.writelines(out)
print('OK')
