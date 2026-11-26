package main

import (
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func fixtureRequest(action string) request {
	claims := fmt.Sprintf(`{"exp":%d}`, time.Now().Add(time.Hour).Unix())
	return request{Action: action, AccountRef: "dion:org:user", OrgID: "org", UserID: "user", RequestID: "fixture", RoomID: "fixture-slug", AccessToken: "header." + base64.RawURLEncoding.EncodeToString([]byte(claims)) + ".signature"}
}
func reply(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body))}
}

func TestExpiredJWTNeverReachesNetwork(t *testing.T) {
	h := newHelper(roundTripFunc(func(*http.Request) (*http.Response, error) { t.Fatal("network called"); return nil, nil }), nil)
	r := fixtureRequest("create")
	r.AccessToken = "expired"
	if got := h.handle(r); got.Status != "auth_expired" {
		t.Fatalf("%+v", got)
	}
}

func TestUnauthorizedDoesNotRefreshOrReplay(t *testing.T) {
	count := 0
	h := newHelper(roundTripFunc(func(r *http.Request) (*http.Response, error) {
		count++
		return reply(401, `{"secret":"must-not-echo"}`), nil
	}), nil)
	got := h.handle(fixtureRequest("create"))
	if count != 1 || got.Status != "auth_expired" || strings.Contains(fmt.Sprint(got), "must-not-echo") {
		t.Fatalf("count=%d result=%+v", count, got)
	}
}

func TestUpstreamRefreshPathBlocked(t *testing.T) {
	count := 0
	s, err := seed(fixtureRequest("create"), roundTripFunc(func(*http.Request) (*http.Response, error) { count++; return reply(200, `{}`), nil }))
	if err != nil {
		t.Fatal(err)
	}
	// Even an explicit accidental upstream refresh cannot reach the wire.
	if err = s.Refresh(); err == nil || count != 0 {
		t.Fatalf("refresh err=%v requests=%d", err, count)
	}
}

func TestCreateUsesSeededUpstreamSession(t *testing.T) {
	r := fixtureRequest("create")
	count := 0
	h := newHelper(roundTripFunc(func(req *http.Request) (*http.Response, error) {
		count++
		if req.Header.Get("Authorization") != "Bearer "+r.AccessToken {
			t.Fatal("access token not seeded")
		}
		if strings.Contains(req.Header.Get("Cookie"), "refresh") {
			t.Fatal("refresh cookie supplied")
		}
		switch req.URL.Path {
		case "/v2/events/slug/generate":
			return reply(200, `{"slug":"fixture-slug"}`), nil
		case "/conference/v1/events":
			return reply(201, `{"id":"event","slug":"fixture-slug","org_id":"org"}`), nil
		default:
			t.Fatalf("unexpected path %s", req.URL.Path)
			return nil, nil
		}
	}), nil)
	got := h.handle(r)
	if count != 2 || got.Status != "created" || got.RoomID != "event" {
		t.Fatalf("%+v count=%d", got, count)
	}
}

type fakeSignal struct {
	joined chan<- struct{}
	stop   chan struct{}
	closed bool
}

func (f *fakeSignal) WaitConnected(time.Duration) error { return nil }
func (f *fakeSignal) Subscribe(string, string) error    { f.joined <- struct{}{}; return nil }
func (f *fakeSignal) ReadLoop() error                   { <-f.stop; return nil }
func (f *fakeSignal) Close() error {
	if !f.closed {
		f.closed = true
		close(f.stop)
	}
	return nil
}

func TestJoinLeaveBoundToAccountAndRoom(t *testing.T) {
	var sig *fakeSignal
	h := newHelper(roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.Path == "/conference/v1/connect/wss" {
			return reply(200, `{"url":"wss://fixture.invalid/socket"}`), nil
		}
		return reply(200, `{"id":"event","org_id":"org","slug":"fixture-slug"}`), nil
	}), func(_, _ string, joined chan<- struct{}) (signaling, error) {
		sig = &fakeSignal{joined: joined, stop: make(chan struct{})}
		return sig, nil
	})
	r := fixtureRequest("join")
	if got := h.handle(r); got.Status != "signaling_joined" {
		t.Fatalf("%+v", got)
	}
	r.Action = "leave"
	r.RoomID = "event"
	r.UserID = "other"
	r.AccountRef = "dion:org:other"
	if got := h.handle(r); got.Status != "rejected" || sig.closed {
		t.Fatal("foreign leave accepted")
	}
	r.UserID = "user"
	r.AccountRef = "dion:org:user"
	r.AccessToken = ""
	if got := h.handle(r); got.Status != "closed" || !sig.closed {
		t.Fatalf("%+v", got)
	}
}

func TestServerFailureIsNotAuthExpiry(t *testing.T) {
	h := newHelper(roundTripFunc(func(*http.Request) (*http.Response, error) { return reply(503, "private-body"), nil }), nil)
	if got := h.handle(fixtureRequest("create")); got.Status != "unknown" || got.Error != "setup_failed" {
		t.Fatalf("%+v", got)
	}
}

func TestCreateFailureIsNotReplayed(t *testing.T) {
	for _, status := range []int{401, 503} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			posts := 0
			h := newHelper(roundTripFunc(func(r *http.Request) (*http.Response, error) {
				if r.Method == "GET" {
					return reply(200, `{"slug":"fixture-slug"}`), nil
				}
				posts++
				return reply(status, "private-response"), nil
			}), nil)
			got := h.handle(fixtureRequest("create"))
			want := "unknown"
			if status == 401 {
				want = "auth_expired"
			}
			if posts != 1 || got.Status != want {
				t.Fatalf("posts=%d result=%+v", posts, got)
			}
		})
	}
}
