import json, sys
payload = json.load(sys.stdin)
print(json.dumps({"status":"ready", "sources":[], "values":[], "execute":True}))
