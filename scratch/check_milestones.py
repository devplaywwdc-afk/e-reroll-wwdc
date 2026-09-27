import sys, json
sys.stdout.reconfigure(encoding='utf-8')

path = r'C:\Users\jaras\.gemini\antigravity-ide\brain\934c42f3-dc1b-4b87-9992-56eeb6e3604e\.system_generated\logs\transcript_full.jsonl'

writes = []
runs = []
content_summaries = []

with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        obj = json.loads(line)
        idx = obj.get('step_index')
        t = obj.get('type')
        if t == 'PLANNER_RESPONSE':
            c = obj.get('content', '')
            if c and len(c.strip()) > 50:
                content_summaries.append((idx, c[:120].strip().replace('\n', ' ')))
            for call in obj.get('tool_calls', []):
                name = call.get('name')
                args = call.get('args', {})
                if name == 'write_to_file':
                    writes.append((idx, args.get('TargetFile')))
                elif name == 'run_command':
                    cmd = args.get('CommandLine', '')
                    if 'python' in cmd or 'test' in cmd:
                        runs.append((idx, cmd[:100]))

print("=== WRITES (first 20 of total {}) ===".format(len(writes)))
for w in writes[-25:]:
    print(f"[{w[0]}] {w[1]}")

print("\n=== RUN COMMANDS (last 25) ===")
for r in runs[-25:]:
    print(f"[{r[0]}] {r[1]}")

print("\n=== CONTENT SUMMARIES (last 15) ===")
for cs in content_summaries[-15:]:
    print(f"[{cs[0]}] {cs[1]}")
