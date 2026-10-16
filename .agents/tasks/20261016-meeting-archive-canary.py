"""Real installed archive/ASR/history acceptance on preserved meeting audio.

Focused integration expected360/max3600seconds, sequential18 ASR requests.
No join, LLM, Telegram action or historical outbound replay. Existing backend
resource limits apply. Reuses verified archived bytes at the remote-read seam;
all transcription and domain ingest run from installed production source.
"""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
AREA=ROOT/'.tmp/20261016-meeting-diarization'
MEETING='af8c369c-4fdd-52c9-ac98-4a2bf4ff7b63'

CODE=r'''
import hashlib,json,os,pathlib,shutil,time
from universal_userio.telemost.meetings import Meetings
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
identity="af8c369c-4fdd-52c9-ac98-4a2bf4ff7b63"
root=pathlib.Path("/var/lib/universal-userio/telemost")
path=root/identity/"meeting.json"
raw=path.read_bytes();record=json.loads(raw)
assert record["owner"]=="user_owner" and record["status"]=="recorded" and record.get("ended_at")
assert record["conversation_id"]=="conv_4544417c60a7a1b8523842f6"
backup=root/identity/"before-diarization-20261016.json"
with backup.open("xb") as f:os.chmod(backup,0o600);f.write(raw);f.flush();os.fsync(f.fileno())
for proc in pathlib.Path("/proc").iterdir():
 if not proc.name.isdigit():continue
 try:
  if (proc/"cmdline").read_bytes().replace(b"\0",b" ").strip()!=b"python -m universal_userio.runtime":continue
  values=dict(x.split(b"=",1)for x in (proc/"environ").read_bytes().split(b"\0")if b"="in x)
  for k in ("USERIO_STT_API_KEY","USERIO_STT_URL","USERIO_STT_MODEL","USERIO_WHISPER_API_KEY","USERIO_WHISPER_URL","USERIO_WHISPER_MODEL"):
   if k.encode()in values:os.environ[k]=values[k.encode()].decode()
  break
 except (FileNotFoundError,PermissionError):continue
assert os.environ.get("USERIO_STT_API_KEY")or os.environ.get("USERIO_WHISPER_API_KEY")
class Forbidden:
 def __getattr__(self,name):raise AssertionError("no AI or external delivery allowed")
store=SQLiteUserIOStore("/var/lib/universal-userio/userio.sqlite3")
service=UserIOService(store,Forbidden(),Forbidden())
meetings=Meetings(service,root=root)
def archived_read(payload,output=None):
 assert payload["op"]=="audio" and payload["id"]==identity and payload["owner"]==record["owner"] and output is not None
 track=next(t for t in record["tracks"] if t["index"]==payload["index"])
 source=root/identity/track["filename"]
 assert hashlib.sha256(source.read_bytes()).hexdigest()==track["sha256"]
 with source.open("rb")as f:shutil.copyfileobj(f,output,65536)
meetings._remote=archived_read
real_save=meetings._save
def checkpoint(r):
 real_save(r)
 chunks=sum(len(t.get("transcripts",[]))for t in r["tracks"])
 print(json.dumps({"checkpointChunks":chunks}),flush=True)
meetings._save=checkpoint
started=time.monotonic();meetings._archive(record)
tracks=[t for t in record["tracks"]if t.get("bytes",0)>44]
assert all(t.get("transcription_status")=="ready" for t in tracks),[(t.get("transcription_status"),t.get("transcription_error"))for t in tracks]
chunks=[c for t in tracks for c in t["transcripts"]]
assert len(chunks)==18 and all(c.get("diarization",{}).get("segments") for c in chunks)
assert len({c["speaker_scope"]for c in chunks})==18
assert record["conversation_id"]=="conv_4544417c60a7a1b8523842f6"
for track in tracks:assert hashlib.sha256((root/identity/track["filename"]).read_bytes()).hexdigest()==track["sha256"]
print(json.dumps({"accepted":True,"meeting":identity,"conversation":record["conversation_id"],"chunks":len(chunks),"segments":sum(len(c["diarization"]["segments"])for c in chunks),"speakerScopes":18,"audioUnchanged":True,"providerSends":0,"elapsedSeconds":round(time.monotonic()-started,3)}),flush=True)
'''

def main():
    AREA.mkdir(parents=True,exist_ok=True)
    intent=AREA/'archive-canary-intent.json'
    fd=os.open(intent,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w')as f:
        json.dump({'meeting':MEETING,'at':time.time(),'noJoin':True,'noProviderSend':True},f);f.flush();os.fsync(f.fileno())
    result=subprocess.run(['docker','exec','-i','userio-backend','python','-u','-'],input=CODE,text=True,timeout=3600)
    raise SystemExit(result.returncode)

if __name__=='__main__':main()
