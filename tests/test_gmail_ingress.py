from universal_userio.gmail_ingress import GmailMessage, poll_once

class Reader:
    def __init__(self): self.cursors=[]
    def poll(self,cursor,*,limit):
        self.cursors.append(cursor); return ([GmailMessage("m2","2","a@example.com","hello")],"m2")
class Sink:
    def __init__(self): self.items=[]; self.cursors={"gmail":"m1"}
    def cursor(self,source): return self.cursors.get(source)
    def set_cursor(self,source,cursor): self.cursors[source]=cursor
    def send(self,account,message): self.items.append((account,message.message_id))

def test_gmail_ingress_uses_userio_cursor_api():
    reader=Reader(); sink=Sink()
    assert poll_once(sink,{'gmail':reader},limit=10)==1
    assert reader.cursors==['m1']; assert sink.items==[('gmail','m2')]
    assert sink.cursors['gmail']=='m2'
