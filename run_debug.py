import sys
import clasp
import os
print("SYS.PATH:", sys.path)
print("CLASP LOC:", clasp.__file__)
try:
    import clasp.ratelimit.bucket
    print("CLASP.RATELIMIT.BUCKET:", clasp.ratelimit.bucket.__file__)
except Exception as e:
    print("ERR:", e)
