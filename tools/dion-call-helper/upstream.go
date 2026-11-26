package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
	headless "github.com/kulikov0/headless-client"
	"whitelist-bypass/relay/dion"
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

type signaling interface {
	WaitConnected(time.Duration) error
	Subscribe(string, string) error
	ReadLoop() error
	Close() error
}
type dialFunc func(string, string, chan<- struct{}) (signaling, error)

func dialUpstream(url, agent string, joined chan<- struct{}) (signaling, error) {
	s, err := dion.DialSignaling(url, dion.SignalingDialOptions{UserAgent: agent, LogFn: func(string, ...any) {}})
	if err != nil {
		// Upstream exposes the HTTP handshake status only in its error string.
		if strings.Contains(err.Error(), " status=401 url=") {
			return nil, errAuthExpired
		}
		return nil, err
	}
	s.OnYouJoined = func(dion.YouJoinedParams) {
		select {
		case joined <- struct{}{}:
		default:
		}
	}
	return s, nil
}

type helper struct {
	base          http.RoundTripper
	dial          dialFunc
	active        signaling
	account, room string
}

func newHelper(base http.RoundTripper, dial dialFunc) *helper {
	if base == nil {
		base = headless.ChromeWindows.HTTPClient().Transport
	}
	if dial == nil {
		dial = dialUpstream
	}
	return &helper{base: base, dial: dial}
}
func (h *helper) close() {
	if h.active != nil {
		_ = h.active.Close()
		h.active = nil
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
	// A created room is a delivered effect; do not proceed to another fallible
	// stage and then tell Python to replay room creation.
	if r.Action == "create" {
		return response{RequestID: r.RequestID, Status: "created", RoomID: event.ID, RoomSlug: event.Slug}
	}
	if event.OrgID != r.OrgID {
		return response{RequestID: r.RequestID, Status: "rejected", Error: "organization_mismatch"}
	}
	id := uuid.New().String()
	wss, err := s.ConnectWSS(id)
	if err != nil {
		return failure(r, err)
	}
	joined := make(chan struct{}, 1)
	sig, err := h.dial(wss.URL, s.Device.UserAgent, joined)
	if err != nil {
		return failure(r, err)
	}
	ok := false
	defer func() {
		if !ok {
			_ = sig.Close()
		}
	}()
	if err = sig.WaitConnected(15 * time.Second); err != nil {
		return failure(r, err)
	}
	done := make(chan error, 1)
	go func() { done <- sig.ReadLoop() }()
	if err = sig.Subscribe(event.ID, id); err != nil {
		return failure(r, err)
	}
	select {
	case <-joined:
		h.active, h.account, h.room = sig, r.AccountRef, event.ID
		ok = true
		return response{RequestID: r.RequestID, Status: "signaling_joined", RoomID: event.ID, RoomSlug: event.Slug}
	case <-done:
		return response{RequestID: r.RequestID, Status: "unknown", Error: "signaling_ended"}
	case <-time.After(15 * time.Second):
		return response{RequestID: r.RequestID, Status: "unknown", Error: "join_timeout"}
	}
}

func failure(r request, err error) response {
	status, code := "unknown", "setup_failed"
	if errors.Is(err, errAuthExpired) || errors.Is(err, dion.ErrSessionExpired) {
		status, code = "auth_expired", "auth_expired"
	}
	// Never serialize upstream errors: they may contain tokens, URLs or bodies.
	return response{RequestID: r.RequestID, Status: status, Error: code}
}
