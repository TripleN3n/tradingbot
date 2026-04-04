#!/usr/bin/env python3
"""Remove entire equity curve / charts section from dashboard"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# Find section start
start = None
for marker in [
    '# =============================================================================\n# EQUITY CURVE',
    '# =============================================================================\n# CHARTS',
]:
    idx = txt.find(marker)
    if idx != -1:
        start = idx
        break

# Find section end
end = txt.find('# =============================================================================\n# OPEN POSITIONS')

if start is None or end == -1:
    print(f'ERROR: start={start}, end={end}')
    exit(1)

# Also remove the CSS block that was injected just before if present
css_marker = '\n# CSS for tab-style filter buttons'
css_idx = txt.rfind(css_marker, 0, start)
if css_idx != -1:
    start = css_idx

txt = txt[:start] + txt[end:]

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done — chart section removed')
