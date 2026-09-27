import json

path = r"C:\Users\jaras\.gemini\antigravity-ide\brain\238da5cb-86f7-46b5-a313-d1fe97ff2d70\.system_generated\logs\transcript.jsonl"
out = []
with open(path, "r", encoding="utf-8") as f:
    for line in f:
        d = json.loads(line)
        idx = d.get("step_index", 0)
        t = d.get("type", "")
        content = d.get("content", "")
        if t == "USER_INPUT":
            out.append(f"\n--- USER INPUT (Step {idx}) ---")
            out.append(content)
        elif t == "PLANNER_RESPONSE" and content:
            out.append(f"\n--- PLANNER RESPONSE (Step {idx}) ---")
            out.append(content)

with open(r"c:\Users\jaras\OneDrive\Desktop\eFootball\scratch\summary_chat.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))

print("Wrote summary_chat.txt successfully")
