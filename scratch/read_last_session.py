import json

path = r"C:\Users\jaras\.gemini\antigravity-ide\brain\238da5cb-86f7-46b5-a313-d1fe97ff2d70\.system_generated\logs\transcript.jsonl"
with open(path, "r", encoding="utf-8") as f:
    for line in f:
        d = json.loads(line)
        idx = d.get("step_index", 0)
        t = d.get("type", "")
        if t == "USER_INPUT":
            content = d.get("content", "")
            print(f"\n>>> USER (Step {idx}):")
            print(content[:400])
        elif t == "PLANNER_RESPONSE" and idx in [1, 2, 106, 481, 751, 867, 899]:
            content = d.get("content", "")
            print(f"\n<<< ASSISTANT (Step {idx}):")
            print(content[:400])
