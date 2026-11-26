// dion-call-helper accepts credentials only through its private stdin pipe.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"os"
)

type request struct {
	Action      string `json:"action"`
	AccountRef  string `json:"account_ref"`
	UserID      string `json:"user_id"`
	OrgID       string `json:"org_id"`
	AccessToken string `json:"access_token"`
	RoomID      string `json:"room_id"`
	RequestID   string `json:"request_id"`
}

type response struct {
	RequestID string `json:"request_id"`
	Status    string `json:"status"`
	Error     string `json:"error,omitempty"`
	RoomID    string `json:"room_id,omitempty"`
	RoomSlug  string `json:"room_slug,omitempty"`
}

func run(in io.Reader, out io.Writer, h *helper) error {
	defer h.close()
	s := bufio.NewScanner(in)
	s.Buffer(make([]byte, 4096), 1024*1024)
	enc := json.NewEncoder(out)
	for s.Scan() {
		var r request
		d := json.NewDecoder(bytes.NewReader(s.Bytes()))
		d.DisallowUnknownFields()
		err := d.Decode(&r)
		var extra any
		if err == nil && d.Decode(&extra) != io.EOF {
			err = io.ErrUnexpectedEOF
		}
		result := response{Status: "rejected", Error: "invalid_request"}
		if err == nil {
			result = h.handle(r)
		}
		if err := enc.Encode(result); err != nil {
			return err
		}
	}
	return s.Err()
}

func main() {
	if run(os.Stdin, os.Stdout, newHelper(nil, nil)) != nil {
		os.Exit(1)
	}
}
