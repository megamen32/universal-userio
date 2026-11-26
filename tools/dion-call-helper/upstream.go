package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	headless "github.com/kulikov0/headless-client"
	"whitelist-bypass/relay/dion"
	"whitelist-bypass/relay/tunnel"
)

var errAuthExpired = errors.New("auth_expired")

// Only conference setup endpoints can leave this process; upstream refresh,
// guest registration and login paths are denied before any network request.
type accessOnlyTransport struct{ base http.RoundTripper }

func (t accessOnlyTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	allowed := r.URL.Scheme == "https" && ((r.URL.Host == "api-clients.dion.vc" && r.Method == "GET" && r.URL.Path == "/v2/events/slug/generate") ||
		(r.URL.Host == "api.dion.vc" && ((r.Method == "POST" && (r.URL.Path == "/conference/v1/events" || r.URL.Path == "/conference/v1/connect/wss")) ||
			(r.Method == "GET" && strings.HasPrefix(r.URL.Path, "/conference/v1/events/slug/")))))
	if !allowed {
		return nil, dion.ErrSessionExpired
	}
	resp, err := t.base.RoundTrip(r)
	// Returning an error prevents DoAuthenticated's implicit 401 refresh/retry.
	if err == nil && resp.StatusCode == http.StatusUnauthorized {
		resp.Body.Close()
		return nil, errAuthExpired
	}
	return resp, err
}

type callCore interface {
	Start() error
	Close()
}
type callFactory func(dion.CallConfig, func(tunnel.DataTunnel)) callCore

func upstreamCall(cfg dion.CallConfig, connected func(tunnel.DataTunnel)) callCore {
	c := dion.NewCall(cfg)
	c.OnConnected = connected
	return c
}

type helper struct {
	base          http.RoundTripper
	makeCall      callFactory
	active        callCore
	dataTunnel    tunnel.DataTunnel
	connectWait   time.Duration
	account, room string
}

func newHelper(base http.RoundTripper, factory callFactory) *helper {
	if base == nil {
		base = headless.ChromeWindows.HTTPClient().Transport
	}
	if factory == nil {
		factory = upstreamCall
	}
	return &helper{base: base, makeCall: factory, connectWait: 30 * time.Second}
}
func (h *helper) close() {
	if h.active != nil {
		h.active.Close()
		h.active = nil
		h.dataTunnel = nil
	}
}

func seed(r request, base http.RoundTripper) (*dion.Session, error) {
	parts := strings.Split(r.AccessToken, ".")
	if len(parts) != 3 {
		return nil, errAuthExpired
	}
	data, err := base64.RawURLEncoding.DecodeString(parts[1])
	var claims struct {
		Exp int64 `json:"exp"`
	}
	if err != nil || json.Unmarshal(data, &claims) != nil || time.Until(time.Unix(claims.Exp, 0)) <= 60*time.Second {
		return nil, errAuthExpired
	}
	s, err := dion.NewSession(&http.Client{Transport: accessOnlyTransport{base}, Timeout: 20 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }})
	if err != nil {
		return nil, err
	}
	// No refresh cookie, password, cookie file or persistent credential store.
	s.AccessToken, s.AccessTokenExp, s.UserID = r.AccessToken, time.Unix(claims.Exp, 0), r.UserID
	return s, nil
}

func (h *helper) handle(r request) response {
	result := response{RequestID: r.RequestID, Status: "rejected", Error: "invalid_request"}
	if r.RequestID == "" || r.UserID == "" || r.OrgID == "" || r.AccountRef != "dion:"+r.OrgID+":"+r.UserID {
		return result
	}
	if r.Action == "leave" {
		if h.active == nil || r.AccountRef != h.account || r.RoomID != h.room {
			return result
		}
		h.close()
		return response{RequestID: r.RequestID, Status: "closed", RoomID: h.room}
	}
	if (r.Action != "create" && r.Action != "join") || h.active != nil || (r.Action == "join" && r.RoomID == "") {
		return result
	}
	s, err := seed(r, h.base)
	var event *dion.EventInfo
	if err == nil {
		if r.Action == "create" {
			event, err = s.CreateRoom()
		} else {
			event, err = s.GetEventBySlug(dion.ParseRoom(r.RoomID))
		}
	}
	if err != nil {
		return failure(r, err)
	}
	if event.OrgID != r.OrgID {
		return response{RequestID: r.RequestID, Status: "rejected", Error: "organization_mismatch", RoomID: event.ID, RoomSlug: event.Slug}
	}
	setupFailure := func(err error) response {
		res := failure(r, err)
		// Preserve the delivered create effect even when later setup expires.
		res.RoomID, res.RoomSlug = event.ID, event.Slug
		return res
	}
	if event.Slug == "" {
		return setupFailure(errors.New("missing room slug"))
	}
	obfuscator, err := tunnel.NewTunnelObfuscator(tunnel.DeriveSecretFromJoinLink(event.Slug))
	if err != nil {
		return setupFailure(err)
	}
	role := dion.RoleJoiner
	if r.Action == "create" {
		role = dion.RoleCreator
	}
	connected := make(chan tunnel.DataTunnel, 1)
	call := h.makeCall(dion.CallConfig{Auth: s, Event: event, Obfuscator: obfuscator, DisplayName: "UserIO", Role: role, LogFn: func(string, ...any) {}}, func(dt tunnel.DataTunnel) {
		if dt != nil {
			select {
			case connected <- dt:
			default:
			}
		}
	})
	ok := false
	defer func() {
		if !ok {
			call.Close()
		}
	}()
	if err = call.Start(); err != nil {
		return setupFailure(err)
	}
	select {
	case dt := <-connected:
		h.active, h.dataTunnel, h.account, h.room = call, dt, r.AccountRef, event.ID
		ok = true
		return response{RequestID: r.RequestID, Status: "connected", RoomID: event.ID, RoomSlug: event.Slug}
	case <-time.After(h.connectWait):
		res := setupFailure(errors.New("connection timeout"))
		res.Error = "connect_timeout"
		return res
	}
}

func failure(r request, err error) response {
	status, code := "unknown", "setup_failed"
	if errors.Is(err, errAuthExpired) || errors.Is(err, dion.ErrSessionExpired) || strings.Contains(err.Error(), " status=401 url=") {
		status, code = "auth_expired", "auth_expired"
	}
	// Never serialize upstream errors: they may contain tokens, URLs or bodies.
	return response{RequestID: r.RequestID, Status: status, Error: code}
}
