package main

import "testing"

func TestFixtureLifecycleBoundary(t *testing.T) {
	for _, action := range []string{"create", "join", "leave"} {
		r := request{Action: action, AccountRef: "dion:org:user", RoomID: "fixture-room", RequestID: "req-1", TestFixture: true}
		if got := handle(r); got.Status != "unavailable" { t.Fatalf("%s: %#v", action, got) }
	}
}

func TestRealCallRejectedBeforeProvider(t *testing.T) {
	got := handle(request{Action:"leave", AccountRef:"dion:org:user", RoomID:"real-room", RequestID:"req-2"})
	if got.Status != "rejected" || got.Error != "only fixture calls are enabled" { t.Fatalf("unexpected response: %#v", got) }
}

func TestMalformedRequestRejected(t *testing.T) {
	got := handle(request{Action:"join", RoomID:"fixture-room", RequestID:"req-3", TestFixture:true})
	if got.Status != "rejected" { t.Fatalf("unexpected response: %#v", got) }
}
