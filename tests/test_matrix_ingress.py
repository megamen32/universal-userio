from universal_userio.matrix_ingress import MatrixMessage,poll_once
class Reader:
 def poll(self,cursor,*,limit): assert cursor=='c1'; return [MatrixMessage('!r','$e','@a','hi')],'c2'
class Sink:
 def __init__(self): self.c={'matrix':'c1'}; self.sent=[]
 def cursor(self,s): return self.c.get(s)
 def set_cursor(self,s,c): self.c[s]=c
 def send_message(self,**kw): self.sent.append((kw["source"],kw["message_id"],kw["sender"],kw["body"]))
def test_matrix_ingress_uses_userio_cursor_and_ingress():
 s=Sink(); assert poll_once(s,Reader())==1; assert s.c['matrix']=='c2'; assert s.sent==[('matrix','!r:$e','@a','hi')]
