import json

path = r'C:\Users\jaras\.gemini\antigravity-ide\brain\934c42f3-dc1b-4b87-9992-56eeb6e3604e\.system_generated\logs\transcript.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print('Total lines:', len(lines))
user_indices = [i for i, l in enumerate(lines) if '"USER_INPUT"' in l]
print('Last 5 USER_INPUT line indices:', user_indices[-5:])

for idx in user_indices[-5:]:
    obj = json.loads(lines[idx])
    content = obj.get('content', '')
    print(f"\n--- USER STEP {obj.get('step_index')} ---")
    print(content)

print("\n=== LATER STEPS (3700 to end) ===")
for i in range(max(0, len(lines) - 80), len(lines)):
    obj = json.loads(lines[i])
    t = obj.get('type')
    src = obj.get('source')
    idx = obj.get('step_index')
    if t == 'PLANNER_RESPONSE':
        tc = obj.get('tool_calls', [])
        content = obj.get('content', '')
        if tc:
            tool_names = [call.get('name') for call in tc]
            args_summary = []
            for call in tc:
                args = call.get('args', {})
                if 'CommandLine' in args:
                    args_summary.append(args['CommandLine'][:80])
                elif 'AbsolutePath' in args:
                    args_summary.append(args['AbsolutePath'])
                elif 'TargetFile' in args:
                    args_summary.append(args['TargetFile'])
                else:
                    args_summary.append(str(args)[:60])
            print(f"[{idx}] TOOL CALL: {tool_names} -> {args_summary}")
        if content:
            print(f"[{idx}] CONTENT: {content[:150]}")
    elif t == 'RUN_COMMAND':
        content = obj.get('content', '')
        # print first 2 lines of output
        lines_out = [l.strip() for l in content.splitlines() if l.strip()][:3]
        print(f"[{idx}] CMD RES: {' // '.join(lines_out)}")
    elif t == 'VIEW_FILE':
        print(f"[{idx}] VIEW_FILE")

