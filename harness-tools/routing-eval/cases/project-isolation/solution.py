import json, sys
json.load(sys.stdin)
print(json.dumps({"status":"denied"}))
