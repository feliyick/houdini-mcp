# test_connection.py: run in a terminal (not Houdini) to check the listener answers.
#   .venv/bin/python test_connection.py
import json, os, socket
s = socket.create_connection(("127.0.0.1", int(os.environ.get("HOUDINI_MCP_PORT", "9876"))), timeout=10)
f = s.makefile('rwb')
for cmd in ('ping', 'scene_info'):
    f.write((json.dumps({'id': 1, 'cmd': cmd}) + '\n').encode()); f.flush()
    print(cmd, '->', json.dumps(json.loads(f.readline()), indent=1)[:1500])
