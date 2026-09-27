import json

with open(r'C:\Users\jaras\.gemini\antigravity-ide\brain\c3cdda3c-8df1-4c46-acf7-b08e6aef8337\.system_generated\logs\transcript.jsonl', 'r', encoding='utf-8', errors='ignore') as f:
    for line in f:
        try:
            obj = json.loads(line)
            if obj.get('step_index') in (924, 925, 926):
                print(f"Step {obj.get('step_index')}:")
                if 'thinking' in obj:
                    print("Thinking:", obj['thinking'][:400])
                if 'tool_calls' in obj:
                    print("Tool calls:", obj['tool_calls'])
        except:
            pass
