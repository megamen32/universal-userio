package dioncall

import "testing"

type fakeCore struct { created, joined, left bool }
func (f *fakeCore) CreateRoom(_, _, _ string) (string, error) { f.created = true; return "room-fixture", nil }
func (f *fakeCore) JoinRoom(_, _, _, _ string) error { f.joined = true; return nil }
func (f *fakeCore) LeaveRoom() error { f.left = true; return nil }

func TestCreateLeaveLifecycle(t *testing.T) {
	f := new(fakeCore); s := New(f)
	r, err := s.Handle(Request{Action:"create", AccountID:"dion:org:user", ProviderUserID:"user", OrgID:"org"})
	if err != nil || r.State != StateAccepted || r.RoomID != "room-fixture" || !f.created { t.Fatalf("create: %#v %v", r, err) }
	r, err = s.Handle(Request{Action:"leave", AccountID:"dion:org:user", ProviderUserID:"user", OrgID:"org"})
	if err != nil || r.State != StateClosed || !f.left { t.Fatalf("leave: %#v %v", r, err) }
}

func TestRejectsMissingIdentityAndUnknownLeave(t *testing.T) {
	s := New(new(fakeCore))
	if _, err := s.Handle(Request{Action:"join", RoomID:"fixture"}); err == nil { t.Fatal("missing identity accepted") }
	if _, err := s.Handle(Request{Action:"leave", AccountID:"a", ProviderUserID:"u", OrgID:"o"}); err == nil { t.Fatal("idle leave accepted") }
}
