package dioncall

import "fmt"

type State string

const (
	StateIdle     State = "idle"
	StateAccepted State = "accepted"
	StateJoined   State = "joined"
	StateClosed   State = "closed"
)

type Request struct {
	Action         string `json:"action"`
	AccountID      string `json:"account_id"`
	ProviderUserID string `json:"provider_user_id"`
	OrgID          string `json:"organization_id"`
	RoomID         string `json:"room_id"`
}

type Result struct {
	State  State  `json:"state"`
	RoomID string `json:"room_id,omitempty"`
}

// Core is deliberately tiny: the eventual adapter to upstream relay/dion
// implements it; lifecycle and authorization stay owned by UserIO.
type Core interface {
	CreateRoom(accountID, userID, orgID string) (string, error)
	JoinRoom(accountID, userID, orgID, roomID string) error
	LeaveRoom() error
}

type Session struct {
	core Core
	state State
	room  string
}

func New(core Core) *Session { return &Session{core: core, state: StateIdle} }

func (s *Session) Handle(r Request) (Result, error) {
	if s.core == nil { return Result{}, fmt.Errorf("dion call core is not configured") }
	if r.AccountID == "" || r.ProviderUserID == "" || r.OrgID == "" { return Result{}, fmt.Errorf("account identity is required") }
	switch r.Action {
	case "create":
		if s.state != StateIdle && s.state != StateClosed { return Result{}, fmt.Errorf("call is already active") }
		room, err := s.core.CreateRoom(r.AccountID, r.ProviderUserID, r.OrgID); if err != nil { return Result{}, err }
		if room == "" { return Result{}, fmt.Errorf("core returned empty room") }
		s.room, s.state = room, StateAccepted
		return Result{State: s.state, RoomID: room}, nil
	case "join":
		if r.RoomID == "" { return Result{}, fmt.Errorf("room_id is required") }
		if s.state != StateIdle && s.state != StateClosed { return Result{}, fmt.Errorf("call is already active") }
		if err := s.core.JoinRoom(r.AccountID, r.ProviderUserID, r.OrgID, r.RoomID); err != nil { return Result{}, err }
		s.room, s.state = r.RoomID, StateJoined
		return Result{State: s.state, RoomID: s.room}, nil
	case "leave":
		if s.state != StateAccepted && s.state != StateJoined { return Result{}, fmt.Errorf("call is not active") }
		if err := s.core.LeaveRoom(); err != nil { return Result{}, err }
		s.state = StateClosed
		return Result{State: s.state, RoomID: s.room}, nil
	default:
		return Result{}, fmt.Errorf("unsupported action %q", r.Action)
	}
}
