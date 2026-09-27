import json

transcript_path = r'C:\Users\jaras\.gemini\antigravity-ide\brain\c3cdda3c-8df1-4c46-acf7-b08e6aef8337\.system_generated\logs\transcript.jsonl'
with open(transcript_path, 'r', encoding='utf-8') as f:
    for line in f:
        data = json.loads(line)
        if data.get('source') == 'USER_EXPLICIT':
            print(f"STEP {data.get('step_index')}: {data.get('content')}")
