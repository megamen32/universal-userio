package main

import (
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"whitelist-bypass/relay/dion"
	"whitelist-bypass/relay/tunnel"
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
	}), func(cfg dion.CallConfig, connected func(tunnel.DataTunnel)) callCore {
		if cfg.Role != dion.RoleCreator || cfg.Obfuscator == nil || cfg.DisplayName != "UserIO" {
			t.Fatal("invalid creator configuration")
		}
		return &fakeCall{start: func() error { connected(&fakeTunnel{}); return nil }}
	})
	defer h.close()
	got := h.handle(r)
	if count != 2 || got.Status != "connected" || got.RoomID != "event" {
		t.Fatalf("%+v count=%d", got, count)
	}
}

type fakeTunnel struct{}

func (*fakeTunnel) SendData([]byte)        {}
func (*fakeTunnel) SetOnData(func([]byte)) {}
func (*fakeTunnel) SetOnClose(func())      {}
func (*fakeTunnel) Reconfigure(int, int)   {}

type fakeCall struct {
	start  func() error
	closed bool
}

func (f *fakeCall) Start() error { return f.start() }
func (f *fakeCall) Close()       { f.closed = true }

func TestJoinLeaveBoundToAccountAndRoom(t *testing.T) {
	var call *fakeCall
	h := newHelper(roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.Path == "/conference/v1/connect/wss" {
			return reply(200, `{"url":"wss://fixture.invalid/socket"}`), nil
		}
		return reply(200, `{"id":"event","org_id":"org","slug":"fixture-slug"}`), nil
	}), func(cfg dion.CallConfig, connected func(tunnel.DataTunnel)) callCore {
		if cfg.Role != dion.RoleJoiner || cfg.Obfuscator == nil {
			t.Fatal("invalid joiner configuration")
		}
		call = &fakeCall{start: func() error { connected(&fakeTunnel{}); return nil }}
		return call
	})
	r := fixtureRequest("join")
	if got := h.handle(r); got.Status != "connected" || h.dataTunnel == nil {
		t.Fatalf("%+v", got)
	}
	r.Action = "leave"
	r.RoomID = "event"
	r.UserID = "other"
	r.AccountRef = "dion:org:other"
	if got := h.handle(r); got.Status != "rejected" || call.closed {
		t.Fatal("foreign leave accepted")
	}
	r.UserID = "user"
	r.AccountRef = "dion:org:user"
	r.AccessToken = ""
	if got := h.handle(r); got.Status != "closed" || !call.closed || h.dataTunnel != nil {
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

func TestConnectedWaitsForDataTunnelCallback(t *testing.T) {
	started := make(chan struct{})
	var notify func(tunnel.DataTunnel)
	h := newHelper(roundTripFunc(func(*http.Request) (*http.Response, error) {
		return reply(200, `{"id":"event","slug":"fixture-slug","org_id":"org"}`), nil
	}), func(_ dion.CallConfig, connected func(tunnel.DataTunnel)) callCore {
		notify = connected
		return &fakeCall{start: func() error { close(started); return nil }}
	})
	defer h.close()
	result := make(chan response, 1)
	go func() { result <- h.handle(fixtureRequest("join")) }()
	<-started
	notify(nil)
	select {
	case got := <-result:
		t.Fatalf("returned before non-nil OnConnected: %+v", got)
	case <-time.After(20 * time.Millisecond):
	}
	dt := &fakeTunnel{}
	notify(dt)
	select {
	case got := <-result:
		if got.Status != "connected" || h.dataTunnel != dt {
			t.Fatalf("%+v", got)
		}
	case <-time.After(time.Second):
		t.Fatal("OnConnected did not release response")
	}
}

func TestMissingConnectionCallbackClosesCall(t *testing.T) {
	call := &fakeCall{start: func() error { return nil }}
	h := newHelper(roundTripFunc(func(*http.Request) (*http.Response, error) {
		return reply(200, `{"id":"event","slug":"fixture-slug","org_id":"org"}`), nil
	}), func(dion.CallConfig, func(tunnel.DataTunnel)) callCore { return call })
	h.connectWait = time.Millisecond
	got := h.handle(fixtureRequest("join"))
	if got.Status != "unknown" || got.Error != "connect_timeout" || !call.closed || h.active != nil {
		t.Fatalf("%+v", got)
	}
}

func TestCreateSetupExpiryPreservesRoomAndClosesCall(t *testing.T) {
	call := &fakeCall{start: func() error { return errAuthExpired }}
	posts := 0
	h := newHelper(roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if r.Method == "GET" {
			return reply(200, `{"slug":"fixture-slug"}`), nil
		}
		posts++
		return reply(201, `{"id":"event","slug":"fixture-slug","org_id":"org"}`), nil
	}), func(dion.CallConfig, func(tunnel.DataTunnel)) callCore { return call })
	got := h.handle(fixtureRequest("create"))
	if got.Status != "auth_expired" || got.RoomID != "event" || got.RoomSlug != "fixture-slug" || posts != 1 || !call.closed {
		t.Fatalf("%+v posts=%d", got, posts)
	}
}
