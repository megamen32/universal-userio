// Command dion-call-helper is the narrow UserIO-owned process boundary for
// DION calls. Provider authentication and room signaling stay outside this
// protocol; the eventual upstream relay/dion implementation will be attached
// behind these validated commands.
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

type request struct {
	Action       string `json:"action"`
	AccountRef   string `json:"account_ref"`
	RoomID       string `json:"room_id"`
	RequestID    string `json:"request_id"`
	TestFixture  bool   `json:"test_fixture"`
}

type response struct {
	RequestID string `json:"request_id"`
	Status    string `json:"status"`
	Error     string `json:"error,omitempty"`
}

func validate(r request) error {
	if r.RequestID == "" || r.AccountRef == "" {
		return errors.New("request_id and account_ref are required")
	}
	switch r.Action {
	case "create", "join":
		if r.RoomID == "" { return errors.New("room_id is required") }
		if !r.TestFixture { return errors.New("only fixture calls are enabled") }
	case "leave":
		if r.RoomID == "" { return errors.New("room_id is required") }
	default:
		return fmt.Errorf("unsupported action %q", r.Action)
	}
	return nil
}

func handle(r request) response {
	if err := validate(r); err != nil { return response{RequestID:r.RequestID, Status:"rejected", Error:err.Error()} }
	// Deliberately no provider call yet: this makes the boundary testable without
	// credentials or accidentally placing a real call during development.
	return response{RequestID:r.RequestID, Status:"accepted"}
}

func main() {
	s := bufio.NewScanner(os.Stdin)
	enc := json.NewEncoder(os.Stdout)
	for s.Scan() {
		var r request
		if err := json.Unmarshal(s.Bytes(), &r); err != nil {
			_ = enc.Encode(response{Status:"rejected", Error:"invalid json"})
			continue
		}
		_ = enc.Encode(handle(r))
	}
}
