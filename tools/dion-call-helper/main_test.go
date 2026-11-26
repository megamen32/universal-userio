package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestStdinRejectsRefreshMaterialWithoutEcho(t *testing.T) {
	var out bytes.Buffer
	err := run(strings.NewReader(`{"action":"create","refresh_token":"private-sentinel"}`+"\n"), &out, newHelper(nil, nil))
	if err != nil || strings.Contains(out.String(), "private-sentinel") || !strings.Contains(out.String(), "invalid_request") {
		t.Fatalf("unsafe response: %s %v", out.String(), err)
	}
}
